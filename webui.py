"""
mini-agent WebUI 入口
=====================
启动 FastAPI 服务，提供对话页 + 设置页。

用法：
    python webui.py                 # 默认 127.0.0.1:8000
    python webui.py --port 8080
    python webui.py --host 0.0.0.0 --port 8000
"""

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.agent_loop import Agent
from agent.config_loader import get_config, load_config, save_config
from agent.skill_manager import (
    SkillManageError,
    delete_skill,
    info_skill,
    install_skill,
    list_skills,
    update_skill,
)


# ============================================================
# 全局状态：单 Agent 单例，串行处理请求
# ============================================================

CONFIG_PATH = "config.yaml"
WEB_DIR = Path(__file__).parent / "web"

_agent: Agent | None = None

# 串行化对话请求的"处理槽"：用布尔标志 + 锁替代 threading.Lock。
# 好处：停止端点可强制重置标志（threading.Lock 不能被非持有者释放），
# 避免 LLM 调用卡死导致锁永不释放、新请求永远 409。
_busy: bool = False
_busy_lock = threading.Lock()


def _try_acquire_busy() -> bool:
    """尝试获取处理权（非阻塞）。成功返回 True，已被占用返回 False。"""
    global _busy
    with _busy_lock:
        if _busy:
            return False
        _busy = True
        return True


def _release_busy() -> None:
    """释放处理权（正常完成时调用）。"""
    global _busy
    with _busy_lock:
        _busy = False


def _force_release_busy() -> None:
    """强制释放处理权（停止兜底用，即使旧生成器还在后台阻塞也重置）。"""
    global _busy
    with _busy_lock:
        _busy = False


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent(CONFIG_PATH)
    return _agent


def rebuild_agent() -> Agent:
    global _agent
    _agent = Agent(CONFIG_PATH)
    return _agent


# ============================================================
# FastAPI 应用
# ============================================================

app = FastAPI(title="mini-agent WebUI")

# 静态资源（JS / CSS）
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index():
    html = WEB_DIR / "index.html"
    if not html.exists():
        raise HTTPException(status_code=404, detail="index.html 不存在")
    return FileResponse(str(html))


# ------------------------------------------------------------
# 请求 / 响应模型
# ------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


class ConfigRequest(BaseModel):
    api: dict[str, Any]
    agent: dict[str, Any]


class SkillInstallRequest(BaseModel):
    url: str
    name: str | None = None
    force: bool = False


class SkillUpdateRequest(BaseModel):
    name: str


class SkillDeleteRequest(BaseModel):
    name: str


# ------------------------------------------------------------
# 接口
# ------------------------------------------------------------

@app.post("/api/chat")
def api_chat(req: ChatRequest):
    """
    发送消息，返回 { reply, steps, error }。
    steps 中每一项为 { type: "reasoning"|"tool_call", ... }。
    串行处理：同一时刻只处理一条消息。
    """
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    if not _try_acquire_busy():
        return JSONResponse(
            status_code=409,
            content={"error": "当前已有对话在处理中，请稍后再试"},
        )
    try:
        agent = get_agent()
    except FileNotFoundError:
        _release_busy()
        return JSONResponse(
            status_code=400,
            content={"reply": None, "steps": [], "error": "config.yaml 不存在，请先在设置页配置或复制 config.example.yaml 为 config.yaml"},
        )
    try:
        result = agent.chat_with_steps(text)
        return result
    except Exception as e:
        return {"reply": None, "steps": [], "error": f"内部错误: {e}"}
    finally:
        _release_busy()


@app.post("/api/chat/stream")
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

    if not _try_acquire_busy():
        return JSONResponse(
            status_code=409,
            content={"error": "当前已有对话在处理中，请稍后再试"},
        )

    try:
        agent = get_agent()
    except FileNotFoundError:
        _release_busy()
        return JSONResponse(
            status_code=400,
            content={"error": "config.yaml 不存在，请先在设置页配置或复制 config.example.yaml 为 config.yaml"},
        )

    def event_stream() -> Iterator[str]:
        try:
            for evt in agent.chat_stream(text):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = {"type": "done", "error": f"内部错误: {e}", "reply": None}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            _release_busy()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用反向代理缓冲，保证流式实时性
        },
    )


@app.post("/api/reset")
def api_reset():
    """清空对话历史。"""
    if not _try_acquire_busy():
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
        _release_busy()
    return {"ok": True}


@app.post("/api/chat/stop")
def api_stop_chat():
    """
    请求停止当前正在进行的对话。

    1. 线程安全地置位 agent 停止标志 + 终止当前 bash 子进程 + 关闭 LLM stream。
    2. 延迟 2 秒后强制释放处理槽（兜底）——防止 LLM 调用卡死导致 busy 永不释放、
       新请求永远 409。即使旧生成器还在后台阻塞，2 秒后新请求也能进来。

    注意：强制释放后旧生成器可能仍在后台运行，但它通过 generation 计数器
    检测到自己已过期，不会修改 messages，最终会因 stream 被 close 而异常退出。
    """
    agent = _agent
    if agent is not None:
        agent.request_stop()
    # 兜底：2 秒后强制释放处理槽
    threading.Timer(2.0, _force_release_busy).start()
    return {"ok": True}


@app.get("/api/config")
def api_get_config():
    """读取当前配置，api_key 脱敏返回。"""
    try:
        cfg = get_config()
    except RuntimeError:
        # 配置未加载，尝试加载
        try:
            cfg = load_config(CONFIG_PATH)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="config.yaml 不存在")
    return cfg.to_dict(mask_key=True)


@app.post("/api/config")
def api_save_config(req: ConfigRequest):
    """
    保存配置到 config.yaml，并重建 Agent。
    注意：api_key 若为脱敏形式（含 *），则保留原文件中的值。
    """
    try:
        old_cfg = load_config(CONFIG_PATH)
    except FileNotFoundError:
        old_cfg = None

    data = {"api": dict(req.api), "agent": dict(req.agent)}

    # api_key 处理：如果前端传回的是脱敏形式（含 *），用原值替换
    api_key = data["api"].get("api_key", "")
    if "*" in api_key and old_cfg is not None:
        data["api"]["api_key"] = old_cfg.api.api_key

    try:
        save_config(data, CONFIG_PATH)
        rebuild_agent()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")

    return {"ok": True}


# ============================================================
# Skill 管理接口
# ============================================================

@app.get("/api/skills")
def api_list_skills():
    """列出所有已安装 skill。"""
    try:
        cfg = get_config()
    except RuntimeError:
        try:
            cfg = load_config(CONFIG_PATH)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="config.yaml 不存在")
    try:
        return {"skills": list_skills()}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/skills/{name}")
def api_info_skill(name: str):
    """查询单个 skill 详情。"""
    try:
        cfg = get_config()
    except RuntimeError:
        try:
            cfg = load_config(CONFIG_PATH)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="config.yaml 不存在")
    try:
        return info_skill(name)
    except SkillManageError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/skills/install")
def api_install_skill(req: SkillInstallRequest):
    """从 git 仓库安装 skill。"""
    try:
        cfg = get_config()
    except RuntimeError:
        try:
            cfg = load_config(CONFIG_PATH)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="config.yaml 不存在")
    try:
        result = install_skill(req.url, name=req.name, force=req.force)
        # 安装后重建 Agent，刷新 skill 索引
        rebuild_agent()
        return {"ok": True, **result}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/skills/update")
def api_update_skill(req: SkillUpdateRequest):
    """更新已安装的 skill。"""
    try:
        cfg = get_config()
    except RuntimeError:
        try:
            cfg = load_config(CONFIG_PATH)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="config.yaml 不存在")
    try:
        result = update_skill(req.name)
        # 更新后重建 Agent，刷新 skill 索引（frontmatter 可能变化）
        rebuild_agent()
        return {"ok": True, **result}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/skills/{name}")
def api_delete_skill(name: str):
    """删除已安装的 skill。"""
    try:
        cfg = get_config()
    except RuntimeError:
        try:
            cfg = load_config(CONFIG_PATH)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="config.yaml 不存在")
    try:
        result = delete_skill(name)
        # 删除后重建 Agent，刷新 skill 索引
        rebuild_agent()
        return {"ok": True, **result}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# 入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="mini-agent WebUI")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    args = parser.parse_args()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
