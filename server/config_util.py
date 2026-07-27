"""配置加载辅助：统一处理 get_config / load_config 回退。"""

from fastapi import HTTPException

from agent.config_loader import get_config, load_config
from server.constants import CONFIG_PATH


def require_config():
    """获取已加载配置；未加载则尝试从文件加载；文件不存在则 404。"""
    try:
        return get_config()
    except RuntimeError:
        try:
            return load_config(CONFIG_PATH)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="config.yaml 不存在")
