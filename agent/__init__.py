"""
mini-agent 核心包
==================
Agent 自主循环、配置加载、Skill 加载与管理。

入口：
- main.py      —— 命令行 REPL
- webui.py     —— FastAPI WebUI

子模块也可单独以模块方式运行，例如：
    python -m agent.skill_manager list

为避免 ``python -m agent.xxx`` 时因包初始化预导入子模块而触发
RuntimeWarning，此处使用 PEP 562 惰性导入：仅在真正访问属性时才加载。
"""

__all__ = ["Agent", "Config", "get_config", "load_config", "save_config"]


def __getattr__(name: str):
    if name == "Agent":
        from agent.agent_loop import Agent
        return Agent
    if name == "Config":
        from agent.config_loader import Config
        return Config
    if name == "get_config":
        from agent.config_loader import get_config
        return get_config
    if name == "load_config":
        from agent.config_loader import load_config
        return load_config
    if name == "save_config":
        from agent.config_loader import save_config
        return save_config
    raise AttributeError(f"module 'agent' has no attribute {name!r}")
