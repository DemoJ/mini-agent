"""
内置工具
========
独立、无状态的工具，可直接被 Agent 调用。

- bash   : 执行一条 shell 命令
- finish : 完成任务并给出最终总结
"""

import subprocess
from typing import Any


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


def get_builtin_tools() -> dict[str, dict[str, Any]]:
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
