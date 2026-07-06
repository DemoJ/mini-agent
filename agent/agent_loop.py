"""
一个最小的 Agent Loop 实现（含 Skill 三层懒加载）
=================================================
使用 OpenAI SDK 调用兼容 API（OpenAI / Azure / ollama / vLLM 等）

内置工具：bash（执行 Shell 命令）、finish（完成任务）、load_skill（加载技能）

工具注册拆分到 agent/tools/ 下：
  - agent/tools/builtin.py    : bash / finish（无状态内置工具）
  - agent/tools/skill_tools.py: load_skill / list / install / update / delete / info
                                （schema 定义，执行逻辑在本模块的 _do_* 方法中）

Skill 采用三层懒加载：
  L1 索引层 —— 启动时扫描 skills/，只读 frontmatter，注入 system prompt（常驻）
  L2 指令层 —— LLM 调 load_skill(name) 时读 SKILL.md 正文 + 注册该 skill 工具
  L3 参考层 —— skill 自带的 read_file 工具按需读 references/
"""

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from agent.config_loader import Config, get_config, load_config
from agent.file_manager import get_file_manager, init_file_manager
from agent.skill_loader import (
    SkillInfo,
    discover_skills,
    load_skill_full,
    make_read_file_tool,
)
from agent.file_manager import is_image_file
from agent.tools.builtin import _decode_output
from agent.skill_manager import (
    SkillManageError,
    delete_skill as _sm_delete_skill,
    info_skill as _sm_info_skill,
    install_skill as _sm_install_skill,
    list_skills as _sm_list_skills,
    update_skill as _sm_update_skill,
)
from agent.tools import get_builtin_tools, get_skill_tool_defs


# ============================================================
# Agent — 自主循环
# ============================================================

class Agent:
    """
    自主 Agent，持续运行直到主动调用 finish 或产生文本回复。

    每一轮用户消息启动一个内部循环：
      - 调用 LLM
      - 如果是工具调用 → 执行 → 继续循环
      - 如果是 finish 调用 → 提取总结并返回
      - 如果是纯文本回复 → 直接返回

    Skill 支持：
      - 启动时 discover_skills() 扫描 skills/ 目录，构建索引注入 system prompt
      - LLM 可调用 load_skill(name) 加载某 skill 的完整指令 + 工具
      - 加载后的 skill 工具以 {skill_name}__{tool_name} 命名空间注册，避免撞名
      - 每个 skill 自动附带 {skill_name}__read_file 工具用于读取 references/
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        load_config(config_path)
        cfg = get_config()
        self._cfg: Config = cfg

        # 初始化文件管理器（上传/交付目录基于配置文件父目录）
        init_file_manager(cfg.path.parent)

        self.client = OpenAI(
            base_url=cfg.api.base_url,
            api_key=cfg.api.api_key,
            timeout=120.0,  # 避免网络或 LLM 无响应时无限阻塞（SDK 默认 600s 太长）
        )
        self.model = cfg.api.model
        self.temperature = cfg.agent.temperature
        self.max_tokens = cfg.agent.max_tokens
        self.reasoning_effort = cfg.agent.reasoning_effort

        # 安全上限：防止死循环
        self.max_internal_steps = 50

        # ---- 调试日志配置 ----
        self.debug = cfg.debug

        # ---- 实例级工具注册表（可动态扩展）----
        # 内置工具（bash / finish）+ skill 相关工具（load_skill / list / install / update / delete / info）
        # schema 定义来自 agent/tools/，skill 相关工具的 fn=None，执行逻辑在本类的 _do_* 方法中
        self.tools: dict[str, dict[str, Any]] = get_builtin_tools()
        self.tools.update(get_skill_tool_defs())

        # ---- Skill 注册表 ----
        # L1 索引：所有已发现的 skill 元数据（常驻）
        self.skills_registry: dict[str, SkillInfo] = discover_skills(
            cfg.agent.skills_dirs
        )
        # 已完整加载（L2）的 skill 名集合，用于幂等去重
        self._loaded_skills: set[str] = set()

        # 刷新 OpenAI tools 参数
        self.openai_tools = self._build_openai_tools()

        # ---- 系统提示词（注入工具描述 + skill 索引）----
        tools_desc = self._build_tools_description()
        skills_index = self._build_skills_index()
        self.system_prompt = cfg.agent.system_prompt.format(
            tools_desc=tools_desc,
            skills_index=skills_index,
        )
        self.messages: list[ChatCompletionMessageParam] = []

        # ---- 停止控制（WebUI 停止按钮用）----
        # _stop_event: 跨线程停止标志，request_stop() 置位，chat_stream/chat_with_steps 轮询检查
        # _proc_lock + _current_proc: 当前 bash 子进程引用，停止时 terminate 它
        # _stream_lock + _current_stream: 当前 LLM 流式响应引用，停止时 close 中断阻塞读取
        # _generation: 对话代际，强制停止后旧生成器检测到过期则不修改 messages
        self._stop_event = threading.Event()
        self._proc_lock = threading.Lock()
        self._current_proc: subprocess.Popen | None = None
        self._stream_lock = threading.Lock()
        self._current_stream: Any = None
        self._generation: int = 0

    # --------------------------------------------------------
    # 工具注册与刷新
    # --------------------------------------------------------
    # 内置工具与 skill 工具的 schema 定义已移至 agent/tools/：
    #   - get_builtin_tools()      → bash / finish
    #   - get_skill_tool_defs()    → load_skill / list / install / update / delete / info
    # skill 工具的执行逻辑见下方 _do_* 方法，由 _execute_tool_call 统一分发。

    def _build_openai_tools(self) -> list[dict]:
        """把 self.tools 转成 OpenAI tool calling 格式。"""
        out = []
        for name, tool in self.tools.items():
            out.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            })
        return out

    def _refresh_openai_tools(self) -> None:
        """工具注册表变更后刷新 OpenAI tools 参数（每轮 LLM 调用前取最新值）。"""
        self.openai_tools = self._build_openai_tools()

    # --------------------------------------------------------
    # Skill 加载逻辑（load_skill 工具的内部实现）
    # --------------------------------------------------------

    def _do_load_skill(self, name: str) -> str:
        """
        执行 skill 加载：
          1. 查注册表，不存在则懒刷新一次再查（兜底启动后新增的 skill）
          2. 已加载则幂等返回
          3. 读 SKILL.md 正文 + tools.py
          4. 以 {skill}__{tool} 命名空间注册工具
          5. 自动注册 {skill}__read_file 用于读 references/
          6. 刷新 OpenAI tools
          7. 返回 SKILL.md 正文作为 tool result（注入对话）
        """
        info = self.skills_registry.get(name)
        if info is None:
            # 懒刷新：skills/ 目录可能在 Agent 启动后才出现新增项
            # （如手动建符号链接、外部 git clone 等，绕过了 install_skill
            # 的刷新路径）。此处兜底重扫一次，避免 load_skill 永远找不到。
            self._refresh_skills_after_change()
            info = self.skills_registry.get(name)
        if info is None:
            avail = ", ".join(self.skills_registry.keys()) or "(无)"
            return json.dumps(
                {"error": f"未知 skill: {name}。可用: {avail}"},
                ensure_ascii=False,
            )

        # 幂等：已加载则直接返回提示
        if name in self._loaded_skills:
            return json.dumps(
                {"ok": True, "message": f"skill '{name}' 已加载，可直接使用其工具。"},
                ensure_ascii=False,
            )

        try:
            loaded = load_skill_full(info)
        except Exception as e:
            return json.dumps(
                {"error": f"加载 skill '{name}' 失败: {e}"},
                ensure_ascii=False,
            )

        # 注册该 skill 的工具，加命名空间前缀防撞名
        registered = []
        for tool_name, tool_def in loaded.tools.items():
            namespaced = f"{name}__{tool_name}"
            self.tools[namespaced] = tool_def
            registered.append(namespaced)

        # 自动注册 read_file 工具（L3 参考层读取入口）
        read_tool_name = f"{name}__read_file"
        if read_tool_name not in self.tools:
            self.tools[read_tool_name] = make_read_file_tool(info.dir_path)
            registered.append(read_tool_name)

        self._loaded_skills.add(name)
        self._refresh_openai_tools()

        # 返回 SKILL.md 正文 + 已注册工具清单
        return json.dumps(
            {
                "ok": True,
                "skill": name,
                "instructions": loaded.instructions,
                "registered_tools": registered,
            },
            ensure_ascii=False,
        )

    # --------------------------------------------------------
    # Skill 管理工具的内部实现（list/install/update/delete/info）
    # --------------------------------------------------------

    def _do_list_skills(self) -> str:
        """列出所有已安装 skill。"""
        try:
            skills = _sm_list_skills()
            return json.dumps(
                {"ok": True, "count": len(skills), "skills": skills},
                ensure_ascii=False,
            )
        except SkillManageError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _do_install_skill(self, url: str, name: str | None = None, force: bool = False) -> str:
        """安装 skill，成功后刷新 skill 索引与 system prompt。"""
        try:
            result = _sm_install_skill(url, name=name, force=force)
        except SkillManageError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        # 刷新 skill 索引（新增 skill 要出现在 system prompt 里）
        self._refresh_skills_after_change()
        return json.dumps({"ok": True, **result}, ensure_ascii=False)

    def _do_update_skill(self, name: str) -> str:
        """更新 skill，成功后刷新 skill 索引（frontmatter 可能变化）。"""
        try:
            result = _sm_update_skill(name)
        except SkillManageError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        self._refresh_skills_after_change()
        return json.dumps({"ok": True, **result}, ensure_ascii=False)

    def _do_delete_skill(self, name: str) -> str:
        """删除 skill，成功后刷新 skill 索引与 system prompt。"""
        try:
            result = _sm_delete_skill(name)
        except SkillManageError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        # 清理该 skill 已加载的工具（避免 LLM 调到不存在的工具）
        self._unload_skill_tools(name)
        self._refresh_skills_after_change()
        return json.dumps({"ok": True, **result}, ensure_ascii=False)

    def _do_info_skill(self, name: str) -> str:
        """查询 skill 详情。"""
        try:
            detail = _sm_info_skill(name)
            return json.dumps({"ok": True, **detail}, ensure_ascii=False)
        except SkillManageError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _unload_skill_tools(self, skill_name: str) -> None:
        """从工具注册表移除某 skill 的所有工具（用于删除 skill 后清理）。"""
        prefix = f"{skill_name}__"
        to_remove = [n for n in self.tools if n.startswith(prefix)]
        for n in to_remove:
            self.tools.pop(n, None)
        self._loaded_skills.discard(skill_name)

    def _refresh_skills_after_change(self) -> None:
        """skill 增删改后调用：重扫 skills 目录、刷新索引与 system prompt、刷新 tools。"""
        cfg = self._cfg
        self.skills_registry = discover_skills(cfg.agent.skills_dirs)
        # 删除已不存在的 skill 的已加载标记
        self._loaded_skills = {
            n for n in self._loaded_skills if n in self.skills_registry
        }
        # 重建 system prompt（skill 索引段落会变）
        tools_desc = self._build_tools_description()
        skills_index = self._build_skills_index()
        self.system_prompt = cfg.agent.system_prompt.format(
            tools_desc=tools_desc,
            skills_index=skills_index,
        )
        self._refresh_openai_tools()

    def _reload_config_and_refresh_skills(self) -> None:
        """重新加载 config.yaml 并刷新 skills 索引。

        在每次对话开始时调用，确保运行期间对配置的修改（如新增 skills 目录）
        和 skills 目录内容的变化（如手动放入新 skill）能被实时感知，
        无需重启 Agent。
        """
        try:
            load_config(self._cfg.path)
            cfg = get_config()
            self._cfg = cfg
        except Exception:
            # 配置加载失败时保持现状，不阻断对话
            return
        self._refresh_skills_after_change()

    # --------------------------------------------------------
    # 工具描述 / skill 索引（注入 system prompt）
    # --------------------------------------------------------

    def _build_tools_description(self) -> str:
        """为 system prompt 生成当前可用工具列表文本。"""
        lines = []
        for name, tool in self.tools.items():
            required = tool.get("parameters", {}).get("required", [])
            # 防御：required 中可能混入非 str 元素（如 skill tools.py schema 写错），
            # 统一转 str 避免 join 报 TypeError
            param_hints = ", ".join(str(r) for r in required)
            lines.append(f"- {name}({param_hints}): {tool.get('description', '')}")
        return "\n".join(lines)

    def _build_skills_index(self) -> str:
        """生成 skill 索引文本，注入 system prompt（L1 索引层）。"""
        if not self.skills_registry:
            return "（暂无可用 skill）"
        lines = [info.index_line() for info in self.skills_registry.values()]
        hint = (
            "如需使用某 skill，先调用 load_skill(name) 获取其完整指令与工具；"
            "加载后即可调用该 skill 提供的工具。"
        )
        return "\n".join(lines) + "\n" + hint

    # --------------------------------------------------------
    # 工具执行
    # --------------------------------------------------------

    def _execute_tool_call(self, tool_name: str, args: dict) -> str:
        """执行工具调用并返回序列化的结果字符串。"""
        # 需要访问 self 状态的工具特殊处理
        if tool_name == "bash":
            return self._do_bash(args.get("command", ""))
        if tool_name == "deliver_file":
            return self._do_deliver_file(
                args.get("path", ""),
                args.get("description", ""),
            )
        if tool_name == "load_skill":
            return self._do_load_skill(args.get("name", ""))
        if tool_name == "list_skills":
            return self._do_list_skills()
        if tool_name == "install_skill":
            return self._do_install_skill(
                url=args.get("url", ""),
                name=args.get("name"),
                force=bool(args.get("force", False)),
            )
        if tool_name == "update_skill":
            return self._do_update_skill(args.get("name", ""))
        if tool_name == "delete_skill":
            return self._do_delete_skill(args.get("name", ""))
        if tool_name == "info_skill":
            return self._do_info_skill(args.get("name", ""))

        if tool_name not in self.tools:
            return json.dumps(
                {"error": f"未知工具: {tool_name}"},
                ensure_ascii=False,
            )

        fn = self.tools[tool_name]["fn"]
        if fn is None:
            return json.dumps(
                {"error": f"工具 '{tool_name}' 不可执行"},
                ensure_ascii=False,
            )
        try:
            result = fn(**args)
        except Exception as e:
            return json.dumps(
                {"error": f"工具执行异常: {e}"},
                ensure_ascii=False,
            )
        return json.dumps(result, ensure_ascii=False)

    # --------------------------------------------------------
    # 停止控制（WebUI 停止按钮）
    # --------------------------------------------------------

    def request_stop(self) -> None:
        """请求停止当前对话（线程安全）。

        1. 置位停止标志，chat_stream/chat_with_steps 在下个检查点退出
        2. 终止当前正在运行的 bash 子进程（如果有），让 _do_bash 立即返回
        3. 关闭当前 LLM 流式响应（如果有），中断阻塞的 chunk 读取
        """
        self._stop_event.set()
        with self._proc_lock:
            proc = self._current_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        with self._stream_lock:
            stream = self._current_stream
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def _do_bash(self, command: str) -> str:
        """可中断版 bash 执行（替代 tools.builtin.tool_bash）。

        与 tool_bash 的区别：用 Popen + poll 循环替代 subprocess.run，
        在执行过程中检查 _stop_event，被停止时 terminate 子进程并立即返回。
        CLI 模式下 _stop_event 永远不会被置位，行为等同 tool_bash。
        """
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "exit_code": -1, "stdout": "", "stderr": str(e)},
                ensure_ascii=False,
            )

        with self._proc_lock:
            self._current_proc = proc

        try:
            deadline = time.monotonic() + 30  # 30s 超时，与 tool_bash 一致
            while True:
                # 检查停止
                if self._stop_event.is_set():
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    except Exception:
                        pass
                    return json.dumps(
                        {"success": False, "exit_code": -1, "stdout": "", "stderr": "已停止"},
                        ensure_ascii=False,
                    )

                ret = proc.poll()
                if ret is not None:
                    stdout, stderr = proc.communicate()
                    return json.dumps(
                        {
                            "success": ret == 0,
                            "exit_code": ret,
                            "stdout": _decode_output(stdout),
                            "stderr": _decode_output(stderr),
                        },
                        ensure_ascii=False,
                    )

                # 超时
                if time.monotonic() > deadline:
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    except Exception:
                        pass
                    return json.dumps(
                        {"success": False, "exit_code": -1, "stdout": "", "stderr": "命令执行超时"},
                        ensure_ascii=False,
                    )

                time.sleep(0.1)
        finally:
            with self._proc_lock:
                self._current_proc = None

    def _do_deliver_file(self, path: str, description: str = "") -> str:
        """执行文件交付：将文件复制到 outputs/ 目录并注册，返回 JSON 结果字符串。"""
        fm = get_file_manager()
        result = fm.deliver_file(path, description=description)
        return json.dumps(result, ensure_ascii=False)

    def _cleanup_interrupted_messages(self, my_gen: int = -1) -> None:
        """停止后清理消息历史，确保一致性。

        问题：如果在 assistant 消息（含 tool_calls）已追加但 tool 结果未全部追加时停止，
        下一轮 API 调用会因缺少 tool 结果报错。
        解决：扫描所有 assistant tool_calls，为没有对应 tool 结果的补占位结果。

        my_gen: 调用方的代际；若与 self._generation 不符则跳过
        （防止强制停止后旧生成器污染新请求的 messages）。
        """
        if my_gen >= 0 and my_gen != self._generation:
            return
        # 收集所有已有结果的 tool_call_id
        answered_ids: set[str] = set()
        for msg in self.messages:
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                answered_ids.add(msg["tool_call_id"])

        # 为缺失结果的 tool_call 补占位
        for msg in self.messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                continue
            for tc in tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id and tc_id not in answered_ids:
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(
                            {"error": "已停止，未执行"}, ensure_ascii=False
                        ),
                    })
                    answered_ids.add(tc_id)

    # --------------------------------------------------------
    # 消息历史管理
    # --------------------------------------------------------

    def _strip_old_image_data(self) -> None:
        """将历史消息中非最新轮次的图片 base64 数据替换为文本占位符。

        多轮对话后，旧图片的 base64 数据占用大量请求体空间，
        是导致 API 400 'unexpected end of data' 错误的主要原因。
        只保留最后一条带图片的用户消息中的图片数据，
        之前的图片消息降级为纯文本描述（图片已不可再被模型查看）。
        """
        # 找到最后一条带图片的 user 消息索引
        last_image_msg_idx = -1
        for i, msg in enumerate(self.messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            if any(
                isinstance(p, dict) and p.get("type") == "image_url"
                for p in content
            ):
                last_image_msg_idx = i

        if last_image_msg_idx < 0:
            return  # 没有图片消息

        # 将最后一条图片消息之前的图片消息降级
        for i, msg in enumerate(self.messages):
            if i >= last_image_msg_idx:
                break
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            new_parts: list[dict] = []
            has_image = False
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    has_image = True
                    new_parts.append({
                        "type": "text",
                        "text": "[此图片内容已在历史消息中省略以节省上下文空间]",
                    })
                else:
                    new_parts.append(part)
            if not has_image:
                continue
            # 降级后如果只剩 text 部分，直接转为字符串（减少 JSON 开销）
            if all(p.get("type") == "text" for p in new_parts):
                text_parts = [p.get("text", "") for p in new_parts]
                msg["content"] = "\n".join(text_parts)
            else:
                msg["content"] = new_parts

    def _estimate_messages_size(self) -> int:
        """估算消息历史的 JSON 序列化大小（字符数）。"""
        try:
            return len(json.dumps(self.messages, ensure_ascii=False))
        except (TypeError, ValueError):
            # 降级：累加各消息内容长度
            total = 0
            for msg in self.messages:
                content = msg.get("content", "")
                if isinstance(content, str):
                    total += len(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            total += len(str(part))
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    total += len(str(tool_calls))
            return total

    def _trim_messages(self) -> None:
        """截断过长的消息历史，保持 tool_calls + tool result 配对完整性。

        当消息历史大小超过 max_context_chars 时，从最早的完整对话轮次开始移除。
        确保不在 assistant(tool_calls) 和其 tool results 之间截断。
        保留至少最后 2 条消息（当前轮次的 user 消息 + 可能的 assistant 回复）。
        """
        max_chars = self._cfg.agent.max_context_chars
        if max_chars <= 0:
            return  # 不限制

        min_keep = 2  # 至少保留 2 条消息

        while (
            self._estimate_messages_size() > max_chars
            and len(self.messages) > min_keep
        ):
            if not self.messages:
                break

            first = self.messages[0]
            role = first.get("role", "")

            if role == "assistant" and first.get("tool_calls"):
                # assistant(tool_calls) → 连同后续所有 tool results 一起移除
                self.messages.pop(0)
                while (
                    self.messages
                    and self.messages[0].get("role") == "tool"
                    and len(self.messages) > min_keep
                ):
                    self.messages.pop(0)
            else:
                # user / assistant(text) / 孤立 tool result → 安全移除
                self.messages.pop(0)

            if len(self.messages) <= min_keep:
                break

    def _manage_message_history(self) -> None:
        """在每次 LLM 调用前管理消息历史：图片降级 + 大小截断。

        这是防止多轮对话后 API 报 'unexpected end of data' 错误的核心方法。
        顺序：先降级旧图片（减少体积），再检查大小截断（兜底）。
        """
        self._strip_old_image_data()
        self._trim_messages()

    # --------------------------------------------------------
    # LLM 调用
    # --------------------------------------------------------

    def _format_messages_for_log(
        self, messages: list[ChatCompletionMessageParam]
    ) -> str:
        """把 messages 列表格式化成可读文本（用于调试日志）。"""
        lines: list[str] = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            lines.append(f"  [{i}] role={role}")

            content = msg.get("content")
            if content:
                # content 可能是 str 或 list（多模态），统一成字符串
                if isinstance(content, list):
                    content_str = json.dumps(content, ensure_ascii=False)
                else:
                    content_str = str(content)
                lines.append(f"      content: {content_str}")

            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else tc.function
                    if isinstance(fn, dict):
                        name = fn.get("name", "")
                        args = fn.get("arguments", "")
                    else:
                        name = getattr(fn, "name", "")
                        args = getattr(fn, "arguments", "")
                    lines.append(f"      tool_call: {name}({args})")

            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                lines.append(f"      tool_call_id: {tool_call_id}")

            lines.append("")
        return "\n".join(lines)

    def _format_tools_for_log(self, tools: list[dict]) -> str:
        """把 OpenAI tools 参数格式化成可读文本。"""
        if not tools:
            return "  (无)"
        lines: list[str] = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            desc = fn.get("description", "")
            params = fn.get("parameters", {})
            required = params.get("required", [])
            required_str = ", ".join(str(r) for r in required) if required else ""
            lines.append(f"  - {name}({required_str}): {desc}")
        return "\n".join(lines)

    def _log_llm_request(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict],
        tag: str = "LLM-REQUEST",
    ) -> None:
        """格式化打印发送给 LLM 的请求内容（system prompt + messages + tools）。

        受 debug.log_llm_request 开关控制。可选写入文件（debug.log_to_file）。
        输出到 stderr，避免与正常对话输出混淆。
        """
        if not self.debug.log_llm_request:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        sep = "=" * 70

        # system prompt 是 messages[0]
        system_content = ""
        rest_messages = messages
        if messages and messages[0].get("role") == "system":
            system_content = str(messages[0].get("content", ""))
            rest_messages = messages[1:]

        body = [
            sep,
            f"[{tag}] {now}  model={self.model}",
            sep,
            "",
            "▼ System Prompt",
            "-" * 70,
            system_content or "(空)",
            "",
            f"▼ Messages (共 {len(rest_messages)} 条)",
            "-" * 70,
            self._format_messages_for_log(rest_messages),
        ]

        if self.debug.log_tools:
            body.extend([
                f"▼ Tools (共 {len(tools)} 个)",
                "-" * 70,
                self._format_tools_for_log(tools),
                "",
            ])

        body.append(sep)
        body.append("")
        output = "\n".join(body)

        # 输出到 stderr
        print(output, file=sys.stderr, flush=True)

        # 可选写入文件
        if self.debug.log_to_file:
            try:
                log_path = self._cfg.resolve_log_file()
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(output)
            except Exception as e:
                print(f"[{tag}] 写入日志文件失败: {e}", file=sys.stderr)

    def _log_llm_response(self, response: Any, tag: str = "LLM-RESPONSE") -> None:
        """打印 LLM 的响应内容。受 debug.log_llm_response 开关控制。"""
        if not self.debug.log_llm_response:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        sep = "=" * 70
        lines = [
            sep,
            f"[{tag}] {now}",
            sep,
        ]
        try:
            choice = response.choices[0]
            msg = choice.message
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                lines.append(f"reasoning_content: {reasoning}")
            lines.append(f"content: {msg.content or '(空)'}")
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    lines.append(
                        f"tool_call: {tc.function.name}({tc.function.arguments})"
                    )
            finish = getattr(choice, "finish_reason", None)
            if finish:
                lines.append(f"finish_reason: {finish}")
        except Exception as e:
            lines.append(f"(解析响应失败: {e})")
        lines.append(sep)
        lines.append("")
        output = "\n".join(lines)
        print(output, file=sys.stderr, flush=True)

        if self.debug.log_to_file:
            try:
                log_path = self._cfg.resolve_log_file()
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(output)
            except Exception as e:
                print(f"[{tag}] 写入日志文件失败: {e}", file=sys.stderr)

    def _call_llm(self) -> tuple[Any, str | None]:
        """调用 LLM，返回 (response, error)。"""
        # 在发送前管理消息历史：降级旧图片 + 截断过长历史
        self._manage_message_history()

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self.system_prompt},
            *self.messages,
        ]
        # 调试日志：打印发送给 LLM 的完整请求
        self._log_llm_request(messages, self.openai_tools)

        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            tools=self.openai_tools,
            tool_choice="auto",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(**kwargs)

        # 调试日志：打印 LLM 响应
        self._log_llm_response(response)

        return response, None

    def _call_llm_stream(self):
        """流式调用 LLM，返回 chunk 迭代器。"""
        # 在发送前管理消息历史：降级旧图片 + 截断过长历史
        self._manage_message_history()

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self.system_prompt},
            *self.messages,
        ]
        # 调试日志：打印发送给 LLM 的完整请求
        self._log_llm_request(messages, self.openai_tools, tag="LLM-REQUEST-STREAM")

        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            tools=self.openai_tools,
            tool_choice="auto",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        return self.client.chat.completions.create(**kwargs)

    # --------------------------------------------------------
    # 公开接口
    # --------------------------------------------------------

    def chat(self, user_input: str) -> str | None:
        """
        发送用户消息，让 Agent 自主决定行为。
        返回 Agent 的最终回复文本。
        """
        result = self.chat_with_steps(user_input)
        return result["reply"]

    def chat_with_steps(self, user_input: str | list) -> dict:
        """
        同 chat()，但额外返回过程步骤（思考内容 + 工具调用）。
        返回 dict: { "reply": str|None, "steps": list[dict], "error": str|None }
        """
        # 每次对话开始时重新加载配置并刷新 skills 索引，
        # 确保运行期间新增的 skills 目录或 skill 文件能被实时感知
        self._reload_config_and_refresh_skills()
        self._stop_event.clear()
        self._generation += 1
        my_gen = self._generation
        self.messages.append({"role": "user", "content": user_input})
        steps: list[dict] = []

        for step in range(self.max_internal_steps):
            # 检查停止
            if self._stop_event.is_set():
                self._cleanup_interrupted_messages(my_gen)
                return {"reply": None, "steps": steps, "error": "已停止"}
            try:
                response, err = self._call_llm()
            except Exception as e:
                return {"reply": None, "steps": steps, "error": f"LLM 调用失败: {e}"}
            if err:
                return {"reply": None, "steps": steps, "error": err}

            choice = response.choices[0]
            msg = choice.message

            # 思考内容（部分模型通过 reasoning_effort 启用后返回）
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                steps.append({"type": "reasoning", "content": reasoning})

            # --- 工具调用 ---
            if msg.tool_calls:
                tool_results: list[tuple[Any, str]] = []

                for tc in msg.tool_calls:
                    # 检查停止（工具执行前）
                    if self._stop_event.is_set():
                        self._cleanup_interrupted_messages(my_gen)
                        return {"reply": None, "steps": steps, "error": "已停止"}
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    # finish → Agent 自主完成
                    if tc.function.name == "finish":
                        summary = tool_args.get("summary", "")
                        self._append_assistant(msg)
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps({"ok": True}),
                        })
                        return {"reply": summary, "steps": steps, "error": None}

                    # deliver_file → 交付文件给用户（不结束对话，继续执行）
                    if tc.function.name == "deliver_file":
                        result_str = self._do_deliver_file(
                            tool_args.get("path", ""),
                            tool_args.get("description", ""),
                        )
                        steps.append({
                            "type": "file",
                            "name": "deliver_file",
                            "args": tool_args,
                            "result": result_str,
                        })
                        tool_results.append((tc, result_str))
                        continue

                    # 普通工具（含 load_skill）→ 执行并暂存结果
                    result_str = self._execute_tool_call(tc.function.name, tool_args)
                    steps.append({
                        "type": "tool_call",
                        "name": tc.function.name,
                        "args": tool_args,
                        "result": result_str,
                    })
                    tool_results.append((tc, result_str))

                # 一轮中所有工具调用完成后，统一写入历史
                self._append_assistant(msg)
                for tc, result_str in tool_results:
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })

                continue

            # --- 纯文本回复（无工具调用）→ 作为本轮回答返回 ---
            answer = msg.content or ""
            self.messages.append({"role": "assistant", "content": answer})
            return {"reply": answer, "steps": steps, "error": None}

        return {"reply": None, "steps": steps, "error": "达到内部步数上限"}

    def chat_stream(self, user_input: str | list):
        """
        流式版本：生成器逐片 yield 事件字典。

        事件类型：
        - {"type": "reasoning_delta", "content": str}  思考内容增量
        - {"type": "reply_delta", "content": str}      回复正文增量（含中间说明）
        - {"type": "tool_call", "id": str, "name": str, "args": dict}   工具调用（参数完整后发出）
        - {"type": "tool_result", "id": str, "name": str, "result": str} 工具执行结果
        - {"type": "file", "file_id": str, "filename": str, "size": int, "description": str}  文件交付
        - {"type": "done", "error": str | None, "reply": str | None}    结束

        支持停止：request_stop() 置位 _stop_event 后，在检查点退出并清理消息历史。
        request_stop() 还会 close 当前 LLM stream，中断阻塞的 chunk 读取。
        """
        # 每次对话开始时重新加载配置并刷新 skills 索引，
        # 确保运行期间新增的 skills 目录或 skill 文件能被实时感知
        self._reload_config_and_refresh_skills()
        self._stop_event.clear()
        self._generation += 1
        my_gen = self._generation
        self.messages.append({"role": "user", "content": user_input})

        for step in range(self.max_internal_steps):
            # 检查点 1：每轮 LLM 调用前
            if self._stop_event.is_set():
                self._cleanup_interrupted_messages(my_gen)
                yield {"type": "done", "error": "已停止", "reply": None}
                return

            try:
                stream = self._call_llm_stream()
            except Exception as e:
                yield {"type": "done", "error": f"LLM 调用失败: {e}", "reply": None}
                return

            # 存储 stream 引用，供 request_stop() close 中断阻塞读取
            with self._stream_lock:
                self._current_stream = stream

            content_acc = ""
            tool_calls_acc: dict[int, dict] = {}
            stopped_during_stream = False

            try:
                for chunk in stream:
                    # 检查点 2：流式读取每个 chunk 之间
                    if self._stop_event.is_set():
                        stopped_during_stream = True
                        break
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # 思考内容增量（部分模型在启用 reasoning_effort 后返回）
                    reasoning_delta = getattr(delta, "reasoning_content", None)
                    if reasoning_delta:
                        yield {"type": "reasoning_delta", "content": reasoning_delta}

                    # 正文增量
                    if delta.content:
                        content_acc += delta.content
                        yield {"type": "reply_delta", "content": delta.content}

                    # 工具调用增量（按 index 累积 arguments 分片）
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx]["arguments"] += tc.function.arguments
            except Exception as e:
                yield {"type": "done", "error": f"流式读取失败: {e}", "reply": None}
                return
            finally:
                # 清理 stream 引用并关闭连接（无论正常结束、停止还是异常）
                with self._stream_lock:
                    if self._current_stream is stream:
                        self._current_stream = None
                try:
                    stream.close()
                except Exception:
                    pass

            # 检查点 2 后续：流式中被停止 → 不追加不完整的 assistant 消息，清理后退出
            if stopped_during_stream or self._stop_event.is_set():
                self._cleanup_interrupted_messages(my_gen)
                yield {"type": "done", "error": "已停止", "reply": None}
                return

            has_tool_calls = bool(tool_calls_acc)
            sorted_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]

            if has_tool_calls:
                # 写入 assistant 消息（含 tool_calls）
                self.messages.append({
                    "role": "assistant",
                    "content": content_acc or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in sorted_calls
                    ],
                })

                # 逐个执行工具
                for tc in sorted_calls:
                    # 检查点 3：每个工具执行前
                    if self._stop_event.is_set():
                        self._cleanup_interrupted_messages(my_gen)
                        yield {"type": "done", "error": "已停止", "reply": None}
                        return

                    try:
                        tool_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        tool_args = {}

                    # finish → Agent 自主完成
                    # 把 summary 作为回复正文流式发出，避免与前面的解释文字混在一个气泡
                    if tc["name"] == "finish":
                        summary = tool_args.get("summary", "")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps({"ok": True}, ensure_ascii=False),
                        })
                        yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "args": tool_args}
                        yield {"type": "tool_result", "id": tc["id"], "name": tc["name"], "result": json.dumps({"ok": True}, ensure_ascii=False)}
                        if summary:
                            yield {"type": "reply_delta", "content": summary}
                        yield {"type": "done", "error": None, "reply": None}
                        return

                    # deliver_file → 交付文件给用户（不结束对话，继续执行）
                    if tc["name"] == "deliver_file":
                        yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "args": tool_args}
                        result_str = self._do_deliver_file(
                            tool_args.get("path", ""),
                            tool_args.get("description", ""),
                        )
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_str,
                        })
                        yield {"type": "tool_result", "id": tc["id"], "name": tc["name"], "result": result_str}
                        # 解析结果，成功则发出 file 事件供前端渲染下载卡片
                        try:
                            file_obj = json.loads(result_str)
                            if file_obj.get("ok"):
                                yield {
                                    "type": "file",
                                    "file_id": file_obj["file_id"],
                                    "filename": file_obj["filename"],
                                    "size": file_obj["size"],
                                    "description": file_obj.get("description", ""),
                                    "is_image": is_image_file(file_obj["filename"]),
                                }
                        except (json.JSONDecodeError, KeyError):
                            pass
                        continue

                    # 普通工具（含 load_skill）→ 执行
                    yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "args": tool_args}
                    result_str = self._execute_tool_call(tc["name"], tool_args)
                    # 工具执行后再次检查停止（bash 可能因停止而返回 "已停止"）
                    if self._stop_event.is_set():
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_str,
                        })
                        self._cleanup_interrupted_messages(my_gen)
                        yield {"type": "tool_result", "id": tc["id"], "name": tc["name"], "result": result_str}
                        yield {"type": "done", "error": "已停止", "reply": None}
                        return
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })
                    yield {"type": "tool_result", "id": tc["id"], "name": tc["name"], "result": result_str}

                # 继续下一轮 LLM 调用
                continue

            # 纯文本回复 → 结束
            self.messages.append({"role": "assistant", "content": content_acc})
            yield {"type": "done", "error": None, "reply": content_acc}
            return

        # 达到步数上限
        yield {"type": "done", "error": "达到内部步数上限", "reply": None}

    # --------------------------------------------------------
    # 辅助
    # --------------------------------------------------------

    def _append_assistant(self, msg: Any) -> None:
        """将 assistant 消息（含 tool_calls）加入历史。"""
        if not msg.tool_calls:
            return
        self.messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })

    def reset(self) -> None:
        """清空对话历史。"""
        self.messages.clear()
