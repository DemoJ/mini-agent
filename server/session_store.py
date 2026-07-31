"""
最近会话持久化（轻量 JSON 文件方案）。

只保存"最近一次"会话的事件流，供页面刷新/重开后恢复显示。
- 新对话开始时清空旧记录，重新累积。
- 用户点击清空对话时删除文件。
- 进行中的任务：事件实时追加到内存并落盘，前端轮询拉取增量续接。

存储格式（.session.json）：
{
  "events": [ <event_dict>, ... ],   # 已产出的事件（SSE 事件格式）
  "done": true|false                  # 本次会话是否已结束
}
"""

import json
import threading
from pathlib import Path
from typing import Any

from server.constants import PROJECT_ROOT

_SESSION_FILE = PROJECT_ROOT / ".session.json"

# 写锁：事件追加和清空可能来自不同线程（流式生成器 vs reset 接口）
_lock = threading.Lock()


def _read() -> dict[str, Any]:
    """读取会话文件，返回 {events: [], done: bool}，文件不存在或损坏返回空会话。"""
    if not _SESSION_FILE.exists():
        return {"events": [], "done": True}
    try:
        with open(_SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"events": [], "done": True}


def _write(data: dict[str, Any]) -> None:
    """写入会话文件。"""
    tmp = _SESSION_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(_SESSION_FILE)


def start_new_session() -> None:
    """开始一次新会话：清空旧记录，标记为未完成。"""
    with _lock:
        _write({"events": [], "done": False})


def append_event(evt: dict[str, Any]) -> None:
    """追加一个事件到当前会话（实时落盘）。"""
    with _lock:
        data = _read()
        data["events"].append(evt)
        _write(data)


def mark_done() -> None:
    """标记当前会话已完成。"""
    with _lock:
        data = _read()
        data["done"] = True
        _write(data)


def get_session() -> dict[str, Any]:
    """读取当前会话状态，返回 {events, done}。"""
    with _lock:
        return _read()


def clear_session() -> None:
    """删除会话记录（用户清空对话时调用）。"""
    with _lock:
        if _SESSION_FILE.exists():
            try:
                _SESSION_FILE.unlink()
            except OSError:
                pass
