"""
一个最简单的 Agent Loop 实现
=====================
使用 OpenAI SDK 调用兼容 API（OpenAI / Azure / ollama / vLLM 等）
工具：bash（执行 Shell 命令）
"""

import json
import subprocess
import sys
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from config_loader import load_config, get_config

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
}


def tools_to_openai_format() -> list[dict]:
    """将内部工具定义转换为 OpenAI tool calling 格式"""
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
# Agent Loop
# ============================================================

def build_system_prompt(task: str) -> str:
    """构建 system prompt，注入工具描述"""
    cfg = get_config()
    tools_lines = []
    for name, tool in TOOLS.items():
        param_hints = ", ".join(tool["parameters"].get("required", []))
        tools_lines.append(f"- {name}({param_hints}): {tool['description']}")

    return cfg.agent.system_prompt.format(
        tools_desc="\n".join(tools_lines),
        task=task,
    )


def run_agent(task: str, max_steps: int | None = None) -> str | None:
    """
    主循环：发送消息 → LLM 决定工具调用 → 执行 → 返回结果 → 继续
    直到 LLM 返回纯文本（最终答案）或达到最大步数
    """
    cfg = get_config()
    max_steps = max_steps or cfg.agent.max_steps

    # --- 初始化 OpenAI 客户端 ---
    client = OpenAI(
        base_url=cfg.api.base_url,
        api_key=cfg.api.api_key,
    )

    system_prompt = build_system_prompt(task)
    openai_tools = tools_to_openai_format()

    # 消息历史
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请完成以下任务：{task}"},
    ]

    for step in range(1, max_steps + 1):
        print(f"\n{'#'*60}")
        print(f"# Step {step}")
        print(f"{'#'*60}")

        # --- 调用 LLM ---
        print(f"[LLM] 调用 {cfg.api.model} ...")
        response = client.chat.completions.create(
            model=cfg.api.model,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
            temperature=cfg.agent.temperature,
            max_tokens=cfg.agent.max_tokens,
        )

        choice = response.choices[0]
        msg = choice.message
        reason = choice.finish_reason

        # --- 记录 token 用量 ---
        if response.usage:
            print(f"[用量] prompt={response.usage.prompt_tokens} "
                  f"completion={response.usage.completion_tokens} "
                  f"total={response.usage.total_tokens}")

        # --- 处理工具调用 ---
        if reason == "tool_calls" and msg.tool_calls:
            # 把 assistant 消息加入历史（包含 tool_calls）
            messages.append({
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

            # 逐个执行工具
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                print(f"[工具] {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")
                result_str = execute_tool_call(tool_name, tool_args)
                print(f"[结果] {result_str[:300]}")

                # 把工具结果加入历史
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

            continue  # 继续下一轮循环

        # --- 处理最终文本回复 ---
        answer = msg.content or ""
        print(f"\n[完成] {reason}")
        print(f"[回答] {answer}")
        return answer

    print(f"\n[结束] 达到最大步数 {max_steps}")
    return None


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    # 加载配置
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    load_config(cfg_path)

    # 读取任务
    task = sys.argv[2] if len(sys.argv) > 2 else "列出当前目录下的文件，然后告诉我你看到了什么"
    answer = run_agent(task)
    if answer:
        print(f"\n最终答案: {answer}")