"""
Skill 相关工具定义
==================
load_skill / list_skills / install_skill / update_skill / delete_skill / info_skill
的 schema（description + parameters）。

这些工具的执行需要访问 Agent 实例状态（self.tools / self.skills_registry /
self._loaded_skills / self._cfg），因此此处只定义 schema，fn 置为 None，
由 Agent._execute_tool_call 统一分发到对应的 _do_* 方法。
"""

from typing import Any


def get_skill_tool_defs() -> dict[str, dict[str, Any]]:
    """返回 skill 相关工具的定义（schema）。fn=None，由 Agent 注入执行逻辑。"""
    return {
        "load_skill": {
            "description": (
                "加载指定 skill 的完整指令与工具。"
                "当用户请求匹配某 skill 的触发词或描述时调用此工具。"
                "调用后会返回该 skill 的详细指令，并自动注册其工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要加载的 skill 名称（见上方 skill 索引）",
                    }
                },
                "required": ["name"],
            },
            "fn": None,  # 由 Agent._execute_tool_call 特殊处理
        },
        "list_skills": {
            "description": (
                "列出所有已安装的 skill 及其元数据（名称、描述、触发词、是否 git 仓库、git 提交）。"
                "用户询问有哪些 skill、想了解当前能力时调用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
            "fn": None,
        },
        "install_skill": {
            "description": (
                "从 git 仓库安装一个新 skill。"
                "用户想新增某个能力、提供了 skill 的 git 仓库地址时调用。"
                "安装成功后该 skill 会立即出现在 skill 索引中，可用 load_skill 加载。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "git 仓库 URL（https 或 ssh 或本地路径）",
                    },
                    "name": {
                        "type": "string",
                        "description": "安装到的目录名（可选，默认从 URL 推断）。仅允许字母数字下划线短横线",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "目标目录已存在时是否覆盖（默认 false）",
                    },
                },
                "required": ["url"],
            },
            "fn": None,
        },
        "update_skill": {
            "description": (
                "更新已安装的 skill 到最新版本（git fetch + reset）。"
                "用户想升级某个 skill、拉取最新代码时调用。"
                "返回更新前后的 git 提交 hash，可判断是否有变化。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要更新的 skill 名",
                    }
                },
                "required": ["name"],
            },
            "fn": None,
        },
        "delete_skill": {
            "description": (
                "删除一个已安装的 skill。"
                "用户想移除某个 skill、卸载某个能力时调用。"
                "删除后该 skill 从索引中消失，不可恢复。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要删除的 skill 名",
                    }
                },
                "required": ["name"],
            },
            "fn": None,
        },
        "info_skill": {
            "description": (
                "查询某个 skill 的详细信息：描述、触发词、目录、git 状态、是否有工具和参考文档。"
                "用户想深入了解某个 skill 时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要查询的 skill 名",
                    }
                },
                "required": ["name"],
            },
            "fn": None,
        },
    }
