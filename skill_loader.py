"""
Skill 加载器 - 三层懒加载
========================

L1 索引层：discover_skills() 只读每个 SKILL.md 的 YAML frontmatter
          → name / description / triggers，拼成索引注入 system prompt（常驻）

L2 指令层：load_skill_full() 读 SKILL.md 完整正文 + 动态导入 tools.py
          → 由 Agent 的 load_skill 内置工具触发，正文作为 tool result 注入对话

L3 参考层：references/ 下的文档/脚本
          → 由 skill 自带的 read_file 工具按需读取（不预加载）

SKILL.md 格式：
    ---
    name: weather
    description: 查询天气
    triggers: ["天气", "weather"]
    ---
    这里是给 LLM 看的完整指令正文……

tools.py 需暴露：
    def get_tools() -> dict:
        return {
            "get_weather": {
                "description": "...",
                "parameters": {...},   # JSON Schema
                "fn": callable,
            },
            ...
        }
"""

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ============================================================
# 数据结构
# ============================================================

@dataclass
class SkillInfo:
    """L1 索引层：skill 元数据（轻量，常驻 system prompt）"""

    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    dir_path: Path = None
    loaded: bool = False  # L2 是否已加载（运行时状态）

    def index_line(self) -> str:
        """生成注入 system prompt 的索引行"""
        trig = f"  触发词: {', '.join(self.triggers)}" if self.triggers else ""
        return f"- {self.name}: {self.description}{trig}"


@dataclass
class LoadedSkill:
    """L2 加载结果"""

    instructions: str               # SKILL.md 正文
    tools: dict[str, dict]          # 工具名 → {description, parameters, fn}


# ============================================================
# 解析工具
# ============================================================

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    解析 YAML frontmatter，返回 (meta, body)。
    frontmatter 格式：首行 --- ，次行起 YAML，再一行 --- 结束。
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, m.group(2)


# ============================================================
# L1：索引层 - 只读 frontmatter
# ============================================================

def discover_skills(skills_dir: str | Path) -> dict[str, SkillInfo]:
    """
    扫描 skills/ 目录下所有 */SKILL.md，只解析 frontmatter。
    返回 {skill_name: SkillInfo}。目录不存在则返回空 dict。
    """
    skills_dir = Path(skills_dir)
    registry: dict[str, SkillInfo] = {}
    if not skills_dir.exists():
        return registry

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            text = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, _ = _parse_frontmatter(text)
        name = meta.get("name") or skill_md.parent.name
        registry[name] = SkillInfo(
            name=name,
            description=meta.get("description", ""),
            triggers=list(meta.get("triggers", []) or []),
            dir_path=skill_md.parent,
        )
    return registry


# ============================================================
# L2：指令层 - 读正文 + 加载 tools.py
# ============================================================

def _load_tools_module(skill_dir: Path, skill_name: str) -> dict[str, dict]:
    """动态导入 skill 的 tools.py，调用其 get_tools() 返回工具字典。"""
    tools_py = skill_dir / "tools.py"
    if not tools_py.exists():
        return {}

    mod_name = f"skill_{skill_name}_tools"
    spec = importlib.util.spec_from_file_location(mod_name, tools_py)
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return {}

    if not hasattr(mod, "get_tools"):
        return {}
    return mod.get_tools() or {}


def load_skill_full(info: SkillInfo) -> LoadedSkill:
    """
    读取 SKILL.md 完整正文 + 加载 tools.py 的工具。
    不缓存 —— 由调用方（Agent）负责幂等去重。
    """
    skill_md = info.dir_path / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)

    raw_tools = _load_tools_module(info.dir_path, info.name)

    info.loaded = True
    return LoadedSkill(instructions=body.strip(), tools=raw_tools)


# ============================================================
# L3：参考层 - read_file 工具工厂
# ============================================================

def make_read_file_tool(skill_dir: Path) -> dict:
    """
    为 skill 生成一个作用域受限的 read_file 工具，
    只能读该 skill 目录下（含 references/）的文件。
    """
    skill_dir = skill_dir.resolve()

    def _read_file(path: str) -> dict:
        target = (skill_dir / path).resolve()
        # 防路径穿越：必须在本 skill 目录内
        try:
            target.relative_to(skill_dir)
        except ValueError:
            return {"error": f"禁止读取 skill 目录外的文件: {path}"}
        if not target.exists():
            return {"error": f"文件不存在: {path}"}
        if target.is_dir():
            return {"error": f"目标是目录而非文件: {path}"}
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as e:
            return {"error": f"读取失败: {e}"}
        return {"path": path, "content": content}

    return {
        "description": "读取本 skill 目录下的参考文件（references/ 等）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于 skill 根目录的文件路径，如 references/usage.md",
                }
            },
            "required": ["path"],
        },
        "fn": _read_file,
    }
