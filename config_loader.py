"""
配置加载模块
========
从 config.yaml 读取配置，提供类型安全的配置对象。
"""

from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class APIConfig:
    """OpenAI 兼容 API 配置"""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_url: str = cfg.get("base_url", "https://api.openai.com/v1")
        self.api_key: str = cfg.get("api_key", "")
        self.model: str = cfg.get("model", "gpt-4o")


class AgentConfig:
    """Agent 行为配置"""

    def __init__(self, cfg: dict[str, Any], base_dir: Path = Path(".")) -> None:
        self.max_steps: int = cfg.get("max_steps", 10)
        self.temperature: float = cfg.get("temperature", 0.7)
        self.max_tokens: int = cfg.get("max_tokens", 4096)
        # 支持直接写文本或引用 .md 文件路径
        self.system_prompt: str = self._resolve_prompt(cfg, "system_prompt", base_dir)
        self.user_prompt: str = self._resolve_prompt(cfg, "user_prompt", base_dir)

    @staticmethod
    def _resolve_prompt(cfg: dict[str, Any], key: str, base_dir: Path) -> str:
        """如果值是 .md 文件路径则读取文件内容，否则直接返回值"""
        val = cfg.get(key, "")
        path = base_dir / val
        if val.endswith(".md") and path.exists():
            return path.read_text(encoding="utf-8")
        return val


class Config:
    """全局配置"""

    def __init__(self, path: str | Path = "config.yaml") -> None:
        raw = _load_yaml(path)
        base_dir = Path(path).parent
        self.api = APIConfig(raw.get("api", {}))
        self.agent = AgentConfig(raw.get("agent", {}), base_dir)


# 模块级单例，方便各处引用
config: Config | None = None


def load_config(path: str | Path = "config.yaml") -> Config:
    global config
    config = Config(path)
    return config


def get_config() -> Config:
    if config is None:
        raise RuntimeError("配置尚未加载，请先调用 load_config()")
    return config