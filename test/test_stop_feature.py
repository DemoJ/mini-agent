"""验证停止功能的核心逻辑：
- request_stop / _do_bash 中断 / _cleanup_interrupted_messages
- stream 引用管理（request_stop 关闭 stream 中断阻塞读取）
- generation 计数（强制停止后旧生成器不污染 messages）
"""
import os
import sys
import time
import threading
import json
import subprocess

# 插入项目根到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

from agent.agent_loop import Agent

# 用 __new__ 绕过 __init__（避免加载 config / OpenAI client）
agent = Agent.__new__(Agent)

# 手动初始化停止相关字段
agent._stop_event = threading.Event()
agent._proc_lock = threading.Lock()
agent._current_proc = None
agent._stream_lock = threading.Lock()
agent._current_stream = None
agent._generation = 0
agent.messages = []

# ---------------------------------------------------------------
# 测试 1: _cleanup_interrupted_messages — 正常情况不应改动
# ---------------------------------------------------------------
agent._generation = 1
agent.messages = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"ls"}'}}
    ]},
    {"role": "tool", "tool_call_id": "tc1", "content": '{"success":true}'},
    {"role": "assistant", "content": "done"},
]
agent._cleanup_interrupted_messages(my_gen=1)
assert len(agent.messages) == 4, f"正常情况不应追加消息，但得到 {len(agent.messages)} 条"
print("PASS  测试1: 正常消息历史不被改动")

# ---------------------------------------------------------------
# 测试 2: _cleanup_interrupted_messages — 中断后补占位
# ---------------------------------------------------------------
agent._generation = 2
agent.messages = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"ls"}'}},
        {"id": "tc2", "type": "function", "function": {"name": "bash", "arguments": '{"command":"pwd"}'}},
    ]},
    # tc1 有结果，tc2 缺失
    {"role": "tool", "tool_call_id": "tc1", "content": '{"success":true}'},
]
agent._cleanup_interrupted_messages(my_gen=2)
assert len(agent.messages) == 4, f"应补 1 条占位，得到 {len(agent.messages)} 条"
last = agent.messages[-1]
assert last["role"] == "tool"
assert last["tool_call_id"] == "tc2"
assert "已停止" in last["content"]
print("PASS  测试2: 中断后为缺失的 tool_call 补占位结果")

# ---------------------------------------------------------------
# 测试 3: _cleanup_interrupted_messages — generation 过期则跳过
# ---------------------------------------------------------------
agent._generation = 5  # 当前代际是 5
agent.messages = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "bash", "arguments": '{}'}}
    ]},
    # tc1 缺失结果
]
before_len = len(agent.messages)
agent._cleanup_interrupted_messages(my_gen=3)  # 旧代际 3 != 当前 5
assert len(agent.messages) == before_len, "过期代际不应修改 messages"
print("PASS  测试3: generation 过期时 _cleanup_interrupted_messages 跳过")

# ---------------------------------------------------------------
# 测试 4: _do_bash 正常执行
# ---------------------------------------------------------------
agent._stop_event.clear()
result = agent._do_bash('echo hello')
parsed = json.loads(result)
assert parsed["success"] is True
assert parsed["stdout"].strip() == "hello"
print("PASS  测试4: _do_bash 正常执行命令")

# ---------------------------------------------------------------
# 测试 5: _do_bash 被停止中断
# ---------------------------------------------------------------
agent._stop_event.clear()

def stop_after_delay():
    time.sleep(0.3)
    agent._stop_event.set()

threading.Thread(target=stop_after_delay, daemon=True).start()
result = agent._do_bash('sleep 10')
parsed = json.loads(result)
assert parsed["success"] is False
assert "已停止" in parsed["stderr"], f"期望 '已停止'，得到: {parsed['stderr']}"
print("PASS  测试5: _do_bash 执行中被停止中断，子进程被 terminate")

# ---------------------------------------------------------------
# 测试 6: request_stop 终止子进程
# ---------------------------------------------------------------
agent._stop_event.clear()
agent._current_stream = None  # 确保无残留

def call_stop():
    time.sleep(0.3)
    agent.request_stop()

threading.Thread(target=call_stop, daemon=True).start()
result = agent._do_bash('sleep 10')
parsed = json.loads(result)
assert parsed["success"] is False
assert "已停止" in parsed["stderr"]
assert agent._current_proc is None
print("PASS  测试6: request_stop 终止子进程并设置停止标志")

# ---------------------------------------------------------------
# 测试 7: request_stop 关闭 LLM stream（核心新增）
# ---------------------------------------------------------------
agent._stop_event.clear()
agent._current_proc = None

# 模拟一个 LLM stream 对象
mock_stream = MagicMock()
agent._current_stream = mock_stream
agent.request_stop()
# 验证 stream.close() 被调用
mock_stream.close.assert_called_once()
assert agent._stop_event.is_set()
print("PASS  测试7: request_stop 关闭当前 LLM stream")

# ---------------------------------------------------------------
# 测试 8: request_stop 在 _current_stream 为 None 时不报错
# ---------------------------------------------------------------
agent._stop_event.clear()
agent._current_stream = None
agent._current_proc = None
agent.request_stop()  # 不应抛异常
print("PASS  测试8: request_stop 在无 stream/proc 时不报错")

# ---------------------------------------------------------------
# 测试 9: _stop_event.clear() + generation 递增（新对话重置）
# ---------------------------------------------------------------
agent._stop_event.set()
old_gen = agent._generation
# 模拟 chat_stream 开头
agent._stop_event.clear()
agent._generation += 1
my_gen = agent._generation
assert not agent._stop_event.is_set()
assert my_gen == old_gen + 1
print("PASS  测试9: 新对话开头 _stop_event 清除 + generation 递增")

# ---------------------------------------------------------------
# 测试 10: webui busy 标志机制
# ---------------------------------------------------------------
# 直接测试 webui 的 busy 函数
import webui

# 重置状态
webui._busy = False

assert webui._try_acquire_busy() is True, "空闲时应获取成功"
assert webui._try_acquire_busy() is False, "已占用时应获取失败"
webui._release_busy()
assert webui._try_acquire_busy() is True, "释放后应能再次获取"
webui._release_busy()

# 强制释放
webui._try_acquire_busy()  # 占用
assert webui._busy is True
webui._force_release_busy()
assert webui._busy is False, "强制释放后 _busy 应为 False"
print("PASS  测试10: webui busy 标志获取/释放/强制释放正常")

print("\n全部通过 ✓")
