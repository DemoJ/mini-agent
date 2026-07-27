"""
全局状态：单 Agent 单例 + 串行处理槽。

串行化对话请求的"处理槽"：用布尔标志 + 锁替代 threading.Lock。
好处：停止端点可强制重置标志（threading.Lock 不能被非持有者释放），
避免 LLM 调用卡死导致锁永不释放、新请求永远 409。
"""

import threading

from agent.agent_loop import Agent

from server.constants import CONFIG_PATH

_agent: Agent | None = None
_busy: bool = False
_busy_lock = threading.Lock()


def try_acquire_busy() -> bool:
    """尝试获取处理权（非阻塞）。成功返回 True，已被占用返回 False。"""
    global _busy
    with _busy_lock:
        if _busy:
            return False
        _busy = True
        return True


def release_busy() -> None:
    """释放处理权（正常完成时调用）。"""
    global _busy
    with _busy_lock:
        _busy = False


def force_release_busy() -> None:
    """强制释放处理权（停止兜底用，即使旧生成器还在后台阻塞也重置）。"""
    global _busy
    with _busy_lock:
        _busy = False


def is_busy() -> bool:
    with _busy_lock:
        return _busy


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent(CONFIG_PATH)
    return _agent


def get_agent_or_none() -> Agent | None:
    return _agent


def rebuild_agent() -> Agent:
    global _agent
    _agent = Agent(CONFIG_PATH)
    return _agent
