"""
配置加载模块
========
从 config.yaml 读取配置，提供类型安全的配置对象。
"""

import os
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
        # 模型是否支持视觉能力（多模态）。开启后用户上传的图片会以 base64 发送给模型；
        # 关闭则仅传文件路径文本，模型无法直接"看到"图片。
        self.vision: bool = cfg.get("vision", False)

    def to_dict(self, mask_key: bool = False) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_key": _mask_api_key(self.api_key) if mask_key else self.api_key,
            "model": self.model,
            "vision": self.vision,
        }


class AgentConfig:
    """Agent 行为配置"""

    def __init__(self, cfg: dict[str, Any], base_dir: Path = Path(".")) -> None:
        self._base_dir: Path = base_dir
        self.max_steps: int = cfg.get("max_steps", 10)
        self.temperature: float = cfg.get("temperature", 0.7)
        self.max_tokens: int = cfg.get("max_tokens", 4096)
        # 消息历史最大字符数（JSON 序列化后）。超过时自动截断旧消息，
        # 防止多轮对话后请求体过大导致 API 报错（如 'unexpected end of data'）。
        # 0 = 不限制。默认 100000（约 100KB），兼顾多数 API 服务端的请求体限制。
        self.max_context_chars: int = cfg.get("max_context_chars", 100000)
        # 思考模式：none/null → 关闭，minimal/low/medium/high/xhigh → 对应级别
        _raw = cfg.get("reasoning_effort", None)
        self.reasoning_effort: str | None = (
            None if _raw is None or str(_raw).lower() == "none" else str(_raw)
        )
        # 支持直接写文本或引用 .md 文件路径
        # system_prompt / user_prompt 存实际内容（供 Agent 使用）
        # system_prompt_file / user_prompt_file 存来源文件路径（None 表示内联文本）
        self.system_prompt, self.system_prompt_file = self._resolve_prompt(
            cfg, "system_prompt", base_dir
        )
        self.user_prompt, self.user_prompt_file = self._resolve_prompt(
            cfg, "user_prompt", base_dir
        )
        # Skill 目录路径列表（支持多目录；配置可写单字符串或列表，统一存为 list[Path]）
        # 靠前的目录优先级更高：同名 skill 由先扫描到的目录提供
        skills_dir_raw = cfg.get("skills_dir", "skills")
        if isinstance(skills_dir_raw, (list, tuple)):
            raw_list = [str(x) for x in skills_dir_raw if str(x).strip()]
        else:
            raw_list = [str(skills_dir_raw)] if str(skills_dir_raw).strip() else []
        if not raw_list:
            raw_list = ["skills"]
        # 展开 ~ 为用户主目录，使路径配置跨平台兼容
        # （如 ~/.agents/skills 在 Windows 上展开为 C:\Users\xxx\.agents\skills，
        #   在 Linux 上展开为 /root/.agents/skills 或 /home/xxx/.agents/skills）
        self.skills_dirs: list[Path] = []
        for p in raw_list:
            expanded = os.path.expanduser(p)
            path = Path(expanded)
            if not path.is_absolute():
                path = base_dir / expanded
            self.skills_dirs.append(path)

    @staticmethod
    def _resolve_prompt(cfg: dict[str, Any], key: str, base_dir: Path) -> tuple[str, Path | None]:
        """解析提示词字段。

        - 若值为 .md 路径且文件存在：读取文件内容，返回 (content, file_path)
        - 否则视为内联文本：返回 (text, None)

        返回的 file_path 为绝对路径，用于后续写回；to_dict 时再转回相对路径。
        """
        val = cfg.get(key, "")
        if val and str(val).endswith(".md"):
            path = (base_dir / val).resolve()
            if path.exists():
                return path.read_text(encoding="utf-8"), path
        return (str(val) if val else ""), None

    def _file_rel(self, fpath: Path) -> str:
        """把路径转为配置文件中的字符串表示（与 config.yaml 中写法一致）。

        用户主目录下的路径用 ~ 缩写（跨平台兼容），其余尝试相对 base_dir。
        """
        # 先尝试 ~ 缩写（用户主目录下的路径）
        home = Path.home()
        try:
            rel = fpath.relative_to(home)
            return "~/" + "/".join(rel.parts)
        except ValueError:
            pass
        # 再尝试相对 base_dir
        try:
            return str(fpath.relative_to(self._base_dir.resolve()))
        except ValueError:
            return str(fpath)

    def to_dict(self) -> dict[str, Any]:
        # system_prompt / user_prompt 始终返回全文，供前端编辑展示
        # system_prompt_file / user_prompt_file 返回来源路径（null=内联），供前端提示
        return {
            "max_steps": self.max_steps,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_context_chars": self.max_context_chars,
            "reasoning_effort": self.reasoning_effort or "none",
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "system_prompt_file": self._file_rel(self.system_prompt_file) if self.system_prompt_file else None,
            "user_prompt_file": self._file_rel(self.user_prompt_file) if self.user_prompt_file else None,
            "skills_dir": [self._file_rel(d) for d in self.skills_dirs],
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

    提示词处理（关键）：
    - 若原 config.yaml 中 system_prompt / user_prompt 为 .md 文件路径（文件引用模式），
      则把 data 中新的提示词**内容**写回对应 md 文件，config.yaml 中仍保留路径引用；
    - 否则视为内联文本，直接把全文写入 config.yaml。

    这样可避免在 webui 编辑提示词后，整段文本被塞进 config.yaml、
    破坏原本的 `prompt/system.md` / `prompt/user.md` 文件引用结构。

    reasoning_effort 为 "none" 时写为 null。

    保留字段：前端只发送 api / agent 两段，debug 段不在表单中，写盘时从旧配置继承，避免保存后丢失。
    skills_dir 已纳入设置页表单（多目录，前端发送列表）；单目录写标量、多目录写列表。
    """
    path = Path(path)
    base_dir = path.parent

    # 读取旧配置原始值，判断提示词是文件引用还是内联
    old_raw: dict[str, Any] = {}
    if path.exists():
        try:
            old_raw = _load_yaml(path)
        except Exception:
            old_raw = {}
    old_agent_raw = old_raw.get("agent", {}) or {}

    agent_data = dict(data.get("agent", {}))
    # 前端可能回传 system_prompt_file / user_prompt_file（仅展示用），写盘时剔除
    agent_data.pop("system_prompt_file", None)
    agent_data.pop("user_prompt_file", None)

    # skills_dir：前端发送列表（多目录）或字符串；统一规范化
    # 单目录写为标量保持 config.yaml 简洁，多目录写为列表
    sd = agent_data.get("skills_dir")
    if sd is None:
        # 前端未发送，保留旧值
        sd = old_agent_raw.get("skills_dir", "skills")
    if isinstance(sd, str):
        # 兼容前端误传多行字符串：按换行拆分
        parts = [s.strip() for s in sd.split("\n") if s.strip()]
        sd_list = parts if parts else ["skills"]
    elif isinstance(sd, (list, tuple)):
        sd_list = [str(s).strip() for s in sd if str(s).strip()] or ["skills"]
    else:
        sd_list = ["skills"]
    agent_data["skills_dir"] = sd_list[0] if len(sd_list) == 1 else sd_list

    # 规范化 reasoning_effort：none → null
    re = agent_data.get("reasoning_effort", "none")
    if re is None or str(re).lower() == "none":
        agent_data["reasoning_effort"] = None
    else:
        agent_data["reasoning_effort"] = str(re)

    # 提示词：文件引用模式 → 写回 md 文件，字段保留路径；内联模式 → 全文写入 config.yaml
    for key in ("system_prompt", "user_prompt"):
        old_val = old_agent_raw.get(key, "")
        new_content = agent_data.get(key, "")
        if isinstance(old_val, str) and old_val.endswith(".md"):
            md_path = base_dir / old_val
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(new_content, encoding="utf-8")
            agent_data[key] = old_val  # 保留路径引用
        else:
            agent_data[key] = new_content

    # debug 段：前端不发送，保留旧值
    debug_data = data.get("debug", {})
    if not debug_data and "debug" in old_raw:
        debug_data = old_raw["debug"] or {}

    out = {
        "api": data.get("api", {}),
        "agent": agent_data,
        "debug": debug_data,
    }

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)

    # 重新加载
    return load_config(path)
