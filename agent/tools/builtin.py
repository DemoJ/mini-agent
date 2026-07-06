"""
内置工具
========
独立、无状态的工具，可直接被 Agent 调用。

- bash   : 执行一条 shell 命令
- finish : 完成任务并给出最终总结
"""

import locale
import subprocess
from typing import Any


def _decode_output(data: bytes | None) -> str:
    """安全解码子进程输出字节。

    Windows 上 shell 默认用系统 OEM 编码（GBK）输出，但很多工具
    （git/python/node 等）输出 UTF-8。策略：先试 UTF-8，再试系统
    编码，最后用 replace 兜底，保证不崩溃。
    """
    if not data:
        return ""
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode(locale.getpreferredencoding(False))
    except (UnicodeDecodeError, LookupError):
        pass
    return data.decode("utf-8", errors="replace")


def tool_bash(command: str) -> dict:
    """执行一条 shell 命令并返回结果"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=30,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": _decode_output(result.stdout),
            "stderr": _decode_output(result.stderr),
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
        "deliver_file": {
            "description": (
                "将一个文件交付给用户下载。"
                "当你生成了文件（如报告、脚本、数据文件等）并希望用户能在网页上下载时调用此工具。"
                "调用后文件会被复制到 outputs 目录并生成下载链接展示给用户。"
                "注意：path 必须是已存在的文件路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要交付的文件路径（绝对路径或相对工作目录的路径）",
                    },
                    "description": {
                        "type": "string",
                        "description": "对文件的简要描述（可选），如'生成的分析报告'、'处理后的数据'等",
                    },
                },
                "required": ["path"],
            },
            "fn": None,  # 由 Agent._execute_tool_call 特殊处理
        },
    }
