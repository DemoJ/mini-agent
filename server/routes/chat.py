"""对话相关接口：chat / stream / reset / stop。"""

import json
import threading
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from server.message_builder import build_message_with_files
from server.schemas import ChatRequest
from server.state import (
    force_release_busy,
    get_agent,
    get_agent_or_none,
    release_busy,
    try_acquire_busy,
)

router = APIRouter()


@router.post("/api/chat")
def api_chat(req: ChatRequest):
    """
    发送消息，返回 { reply, steps, error }。
    steps 中每一项为 { type: "reasoning"|"tool_call", ... }。
    串行处理：同一时刻只处理一条消息。
    """
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    if not try_acquire_busy():
        return JSONResponse(
            status_code=409,
            content={"error": "当前已有对话在处理中，请稍后再试"},
        )
    try:
        agent = get_agent()
    except FileNotFoundError:
        release_busy()
        return JSONResponse(
            status_code=400,
            content={
                "reply": None,
                "steps": [],
                "error": "config.yaml 不存在，请先在设置页配置或复制 config.example.yaml 为 config.yaml",
            },
        )
    try:
        full_text = build_message_with_files(text, req.file_ids)
        result = agent.chat_with_steps(full_text)
        return result
    except Exception as e:
        return {"reply": None, "steps": [], "error": f"内部错误: {e}"}
    finally:
        release_busy()


@router.post("/api/chat/stream")
def api_chat_stream(req: ChatRequest):
    """
    流式对话接口（SSE）。

    返回 text/event-stream，每条事件以 `data: <json>\\n\\n` 形式发送：
      - {"type": "reasoning_delta", "content": "..."}   思考增量
      - {"type": "reply_delta", "content": "..."}        回复正文增量
      - {"type": "tool_call", "id", "name", "args"}      工具调用
      - {"type": "tool_result", "id", "name", "result"}  工具结果
      - {"type": "done", "error", "reply"}              结束

    串行处理：同一时刻只处理一条消息。
    """
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    if not try_acquire_busy():
        return JSONResponse(
            status_code=409,
            content={"error": "当前已有对话在处理中，请稍后再试"},
        )

    try:
        agent = get_agent()
    except FileNotFoundError:
        release_busy()
        return JSONResponse(
            status_code=400,
            content={
                "error": "config.yaml 不存在，请先在设置页配置或复制 config.example.yaml 为 config.yaml",
            },
        )

    def event_stream() -> Iterator[str]:
        try:
            full_text = build_message_with_files(text, req.file_ids)
            for evt in agent.chat_stream(full_text):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = {"type": "done", "error": f"内部错误: {e}", "reply": None}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            release_busy()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/reset")
def api_reset():
    """清空对话历史。"""
    if not try_acquire_busy():
        return JSONResponse(
            status_code=409,
            content={"error": "当前已有对话在处理中，请稍后再试"},
        )
    try:
        agent = get_agent()
        agent.reset()
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="config.yaml 不存在")
    finally:
        release_busy()
    return {"ok": True}


@router.post("/api/chat/stop")
def api_stop_chat():
    """
    请求停止当前正在进行的对话。

    1. 线程安全地置位 agent 停止标志 + 终止当前 bash 子进程 + 关闭 LLM stream。
    2. 延迟 2 秒后强制释放处理槽（兜底）——防止 LLM 调用卡死导致 busy 永不释放、
       新请求永远 409。即使旧生成器还在后台阻塞，2 秒后新请求也能进来。

    注意：强制释放后旧生成器可能仍在后台运行，但它通过 generation 计数器
    检测到自己已过期，不会修改 messages，最终会因 stream 被 close 而异常退出。
    """
    agent = get_agent_or_none()
    if agent is not None:
        agent.request_stop()
    threading.Timer(2.0, force_release_busy).start()
    return {"ok": True}
