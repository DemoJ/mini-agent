"""配置读写接口。"""

from fastapi import APIRouter, HTTPException

from agent.config_loader import load_config, save_config
from server.config_util import require_config
from server.constants import CONFIG_PATH
from server.schemas import ConfigRequest
from server.state import rebuild_agent

router = APIRouter()


@router.get("/api/config")
def api_get_config():
    """读取当前配置，api_key 脱敏返回。"""
    cfg = require_config()
    return cfg.to_dict(mask_key=True)


@router.post("/api/config")
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

    api_key = data["api"].get("api_key", "")
    if "*" in api_key and old_cfg is not None:
        data["api"]["api_key"] = old_cfg.api.api_key

    try:
        save_config(data, CONFIG_PATH)
        rebuild_agent()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")

    return {"ok": True}
