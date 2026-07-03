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


def _mask_api_key(key: str) -> str:
    """脱敏 API Key：保留前 4 + 后 4 位，中间星号。短 key 全部星号。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


class APIConfig:
    """OpenAI 兼容 API 配置"""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_url: str = cfg.get("base_url", "https://api.openai.com/v1")
        self.api_key: str = cfg.get("api_key", "")
        self.model: str = cfg.get("model", "gpt-4o")

    def to_dict(self, mask_key: bool = False) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_key": _mask_api_key(self.api_key) if mask_key else self.api_key,
            "model": self.model,
        }


class AgentConfig:
    """Agent 行为配置"""

    def __init__(self, cfg: dict[str, Any], base_dir: Path = Path(".")) -> None:
        self.max_steps: int = cfg.get("max_steps", 10)
        self.temperature: float = cfg.get("temperature", 0.7)
        self.max_tokens: int = cfg.get("max_tokens", 4096)
        # 思考模式：none/null → 关闭，minimal/low/medium/high/xhigh → 对应级别
        _raw = cfg.get("reasoning_effort", None)
        self.reasoning_effort: str | None = (
            None if _raw is None or str(_raw).lower() == "none" else str(_raw)
        )
        # 支持直接写文本或引用 .md 文件路径
        self.system_prompt: str = self._resolve_prompt(cfg, "system_prompt", base_dir)
        self.user_prompt: str = self._resolve_prompt(cfg, "user_prompt", base_dir)
        # Skill 目录路径（相对于配置文件父目录或绝对路径）
        skills_dir_raw = cfg.get("skills_dir", "skills")
        skills_dir_path = (base_dir / skills_dir_raw) if not Path(skills_dir_raw).is_absolute() else Path(skills_dir_raw)
        self.skills_dir: Path = skills_dir_path

    @staticmethod
    def _resolve_prompt(cfg: dict[str, Any], key: str, base_dir: Path) -> str:
        """如果值是 .md 文件路径则读取文件内容，否则直接返回值"""
        val = cfg.get(key, "")
        path = base_dir / val
        if val.endswith(".md") and path.exists():
            return path.read_text(encoding="utf-8")
        return val

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort or "none",
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "skills_dir": str(self.skills_dir),
        }


class DebugConfig:
    """调试日志配置"""

    def __init__(self, cfg: dict[str, Any]) -> None:
        # 是否打印发送给 LLM 的请求内容（system prompt + messages + tools）
        self.log_llm_request: bool = cfg.get("log_llm_request", False)
        # 是否打印 LLM 的响应内容
        self.log_llm_response: bool = cfg.get("log_llm_response", False)
        # 是否打印 OpenAI tools 函数定义
        self.log_tools: bool = cfg.get("log_tools", True)
        # 是否同时写入日志文件
        self.log_to_file: bool = cfg.get("log_to_file", False)
        # 日志文件路径（相对于配置文件父目录或绝对路径）
        log_file_raw = cfg.get("log_file", "logs/llm_debug.log")
        self.log_file: str = log_file_raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_llm_request": self.log_llm_request,
            "log_llm_response": self.log_llm_response,
            "log_tools": self.log_tools,
            "log_to_file": self.log_to_file,
            "log_file": self.log_file,
        }


class Config:
    """全局配置"""

    def __init__(self, path: str | Path = "config.yaml") -> None:
        self.path = Path(path)
        raw = _load_yaml(self.path)
        base_dir = self.path.parent
        self.api = APIConfig(raw.get("api", {}))
        self.agent = AgentConfig(raw.get("agent", {}), base_dir)
        self.debug = DebugConfig(raw.get("debug", {}))
        self._base_dir = base_dir

    def to_dict(self, mask_key: bool = False) -> dict[str, Any]:
        return {
            "api": self.api.to_dict(mask_key=mask_key),
            "agent": self.agent.to_dict(),
            "debug": self.debug.to_dict(),
        }

    def resolve_log_file(self) -> Path:
        """返回日志文件的绝对路径，自动创建父目录。"""
        p = Path(self.debug.log_file)
        if not p.is_absolute():
            p = self._base_dir / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


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


def save_config(data: dict[str, Any], path: str | Path = "config.yaml") -> Config:
    """
    把配置字典写回 yaml 文件，并重新加载模块级单例。

    data 结构：{"api": {...}, "agent": {...}, "debug": {...}}
    reasoning_effort 为 "none" 时写为 null。
    """
    path = Path(path)

    # 规范化 reasoning_effort：none → null
    agent_data = dict(data.get("agent", {}))
    re = agent_data.get("reasoning_effort", "none")
    if re is None or str(re).lower() == "none":
        agent_data["reasoning_effort"] = None
    else:
        agent_data["reasoning_effort"] = str(re)

    out = {
        "api": data.get("api", {}),
        "agent": agent_data,
        "debug": data.get("debug", {}),
    }

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)

    # 重新加载
    return load_config(path)
