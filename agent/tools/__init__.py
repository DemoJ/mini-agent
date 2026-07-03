"""
工具注册表
==========
- builtin.py    : 内置工具（bash、finish）
- skill_tools.py: skill 相关工具定义（load_skill / list / install / update / delete / info）
"""

from agent.tools.builtin import get_builtin_tools
from agent.tools.skill_tools import get_skill_tool_defs

__all__ = ["get_builtin_tools", "get_skill_tool_defs"]
