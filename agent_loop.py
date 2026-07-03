"""
一个最小的 Agent Loop 实现（含 Skill 三层懒加载）
=================================================
使用 OpenAI SDK 调用兼容 API（OpenAI / Azure / ollama / vLLM 等）

内置工具：bash（执行 Shell 命令）、finish（完成任务）、load_skill（加载技能）

Skill 采用三层懒加载：
  L1 索引层 —— 启动时扫描 skills/，只读 frontmatter，注入 system prompt（常驻）
  L2 指令层 —— LLM 调 load_skill(name) 时读 SKILL.md 正文 + 注册该 skill 工具
  L3 参考层 —— skill 自带的 read_file 工具按需读 references/
"""

import json
import subprocess
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from config_loader import get_config, load_config
from skill_loader import (
    SkillInfo,
    discover_skills,
    load_skill_full,
    make_read_file_tool,
)

# ============================================================
# 内置工具函数
# ============================================================

def tool_bash(command: str) -> dict:
    """执行一条 shell 命令并返回结果"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": "命令执行超时"}
    except Exception as e:
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(e)}


def tool_finish(summary: str) -> dict:
    """完成任务并给出最终总结（Agent 内部使用）"""
    return {"summary": summary}


def _builtin_tools() -> dict[str, dict[str, Any]]:
    """返回内置工具的副本（每个 Agent 实例独立一份，可动态扩展）。"""
    return {
        "bash": {
            "description": "执行一条 shell 命令，支持管道、重定向等标准 shell 语法",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    }
                },
                "required": ["command"],
            },
            "fn": tool_bash,
        },
        "finish": {
            "description": "完成任务，提供最终答案或总结。当你已经完成了用户的要求、可以给出最终回答时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "最终的答案或任务总结",
                    }
                },
                "required": ["summary"],
            },
            "fn": tool_finish,
        },
    }


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

        self.client = OpenAI(
            base_url=cfg.api.base_url,
            api_key=cfg.api.api_key,
        )
        self.model = cfg.api.model
        self.temperature = cfg.agent.temperature
        self.max_tokens = cfg.agent.max_tokens
        self.reasoning_effort = cfg.agent.reasoning_effort

        # 安全上限：防止死循环
        self.max_internal_steps = 50

        # ---- 实例级工具注册表（可动态扩展）----
        self.tools: dict[str, dict[str, Any]] = _builtin_tools()

        # ---- Skill 注册表 ----
        # L1 索引：所有已发现的 skill 元数据（常驻）
        self.skills_registry: dict[str, SkillInfo] = discover_skills(
            cfg.agent.skills_dir
        )
        # 已完整加载（L2）的 skill 名集合，用于幂等去重
        self._loaded_skills: set[str] = set()

        # 注册 load_skill 内置工具
        self._register_load_skill_tool()

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

    # --------------------------------------------------------
    # 工具注册与刷新
    # --------------------------------------------------------

    def _register_load_skill_tool(self) -> None:
        """注册 load_skill 内置工具：让 LLM 能按需激活某 skill。"""
        self.tools["load_skill"] = {
            "description": (
                "加载指定 skill 的完整指令与工具。"
                "当用户请求匹配某 skill 的触发词或描述时调用此工具。"
                "调用后会返回该 skill 的详细指令，并自动注册其工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要加载的 skill 名称（见上方 skill 索引）",
                    }
                },
                "required": ["name"],
            },
            "fn": None,  # 由 _execute_tool_call 特殊处理
        }

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
          1. 查注册表，不存在则报错
          2. 已加载则幂等返回
          3. 读 SKILL.md 正文 + tools.py
          4. 以 {skill}__{tool} 命名空间注册工具
          5. 自动注册 {skill}__read_file 用于读 references/
          6. 刷新 OpenAI tools
          7. 返回 SKILL.md 正文作为 tool result（注入对话）
        """
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
    # 工具描述 / skill 索引（注入 system prompt）
    # --------------------------------------------------------

    def _build_tools_description(self) -> str:
        """为 system prompt 生成当前可用工具列表文本。"""
        lines = []
        for name, tool in self.tools.items():
            param_hints = ", ".join(tool["parameters"].get("required", []))
            lines.append(f"- {name}({param_hints}): {tool['description']}")
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
        # load_skill 特殊处理（需访问 self 状态）
        if tool_name == "load_skill":
            return self._do_load_skill(args.get("name", ""))

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
    # LLM 调用
    # --------------------------------------------------------

    def _call_llm(self) -> tuple[Any, str | None]:
        """调用 LLM，返回 (response, error)。"""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self.system_prompt},
            *self.messages,
        ]
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
        return response, None

    def _call_llm_stream(self):
        """流式调用 LLM，返回 chunk 迭代器。"""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self.system_prompt},
            *self.messages,
        ]
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

    def chat_with_steps(self, user_input: str) -> dict:
        """
        同 chat()，但额外返回过程步骤（思考内容 + 工具调用）。
        返回 dict: { "reply": str|None, "steps": list[dict], "error": str|None }
        """
        self.messages.append({"role": "user", "content": user_input})
        steps: list[dict] = []

        for step in range(self.max_internal_steps):
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

    def chat_stream(self, user_input: str):
        """
        流式版本：生成器逐片 yield 事件字典。

        事件类型：
        - {"type": "reasoning_delta", "content": str}  思考内容增量
        - {"type": "reply_delta", "content": str}      回复正文增量（含中间说明）
        - {"type": "tool_call", "id": str, "name": str, "args": dict}   工具调用（参数完整后发出）
        - {"type": "tool_result", "id": str, "name": str, "result": str} 工具执行结果
        - {"type": "done", "error": str | None, "reply": str | None}    结束
        """
        self.messages.append({"role": "user", "content": user_input})

        for step in range(self.max_internal_steps):
            try:
                stream = self._call_llm_stream()
            except Exception as e:
                yield {"type": "done", "error": f"LLM 调用失败: {e}", "reply": None}
                return

            content_acc = ""
            tool_calls_acc: dict[int, dict] = {}

            try:
                for chunk in stream:
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

                    # 普通工具（含 load_skill）→ 执行
                    yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "args": tool_args}
                    result_str = self._execute_tool_call(tc["name"], tool_args)
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
