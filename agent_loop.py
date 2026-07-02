"""
一个最小的 Agent Loop 实现
=====================
使用 OpenAI SDK 调用兼容 API（OpenAI / Azure / ollama / vLLM 等）
工具：bash（执行 Shell 命令）、finish（完成任务）
"""

import json
import subprocess
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from config_loader import get_config, load_config

# ============================================================
# 工具定义
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


# ============================================================
# 工具注册表
# ============================================================

TOOLS: dict[str, dict[str, Any]] = {
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


def tools_to_openai_format() -> list[dict]:
    """将内部工具定义转换为 OpenAI tool calling 格式。"""
    openai_tools = []
    for name, tool in TOOLS.items():
        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        })
    return openai_tools


def execute_tool_call(tool_name: str, args: dict) -> str:
    """执行工具调用并返回序列化的结果"""
    if tool_name not in TOOLS:
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    fn = TOOLS[tool_name]["fn"]
    result = fn(**args)
    return json.dumps(result, ensure_ascii=False)


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

        # 安全上限：防止死循环
        self.max_internal_steps = 50

        self.openai_tools = tools_to_openai_format()

        # 系统提示词
        tools_desc = self._build_tools_description()
        self.system_prompt = cfg.agent.system_prompt.format(tools_desc=tools_desc)
        self.messages: list[ChatCompletionMessageParam] = []

    # --------------------------------------------------------
    # 内部方法
    # --------------------------------------------------------

    def _build_tools_description(self) -> str:
        """为 system prompt 生成工具列表文本。"""
        lines = []
        for name, tool in TOOLS.items():
            param_hints = ", ".join(tool["parameters"].get("required", []))
            lines.append(f"- {name}({param_hints}): {tool['description']}")
        return "\n".join(lines)

    def _call_llm(self) -> tuple[Any, str | None]:
        """调用 LLM，返回 (response, error)。"""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self.system_prompt},
            *self.messages,
        ]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.openai_tools,
            tool_choice="auto",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response, None

    # --------------------------------------------------------
    # 公开接口
    # --------------------------------------------------------

    def chat(self, user_input: str) -> str | None:
        """
        发送用户消息，让 Agent 自主决定行为。
        返回 Agent 的最终回复文本。
        """
        self.messages.append({"role": "user", "content": user_input})

        for step in range(self.max_internal_steps):
            response, err = self._call_llm()
            if err:
                print(f"  [错误] {err}")
                return None

            choice = response.choices[0]
            msg = choice.message

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
                        return summary

                    # 普通工具 → 执行并暂存结果
                    print(f"  🔧 {tc.function.name}({json.dumps(tool_args, ensure_ascii=False)})")
                    result_str = execute_tool_call(tc.function.name, tool_args)
                    print(f"  ⬅ {result_str[:200]}")
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
            return answer

        print("  [结束] 达到内部步数上限")
        return None

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
