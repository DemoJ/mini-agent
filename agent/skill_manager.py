"""
Skill 管理器 - 安装 / 更新 / 删除 / 列表 / 详情
================================================

所有操作都基于 git，目标目录来自 config.agent.skills_dirs（多目录，默认 ["skills"]）。

设计要点：
- install：git clone <url> 到 <target_skills_dir>/<name>/，目录名做严格校验；
           target_dir 可指定装到哪个已配置目录，默认装到第一个（主）目录
- update：在 skill 所在目录执行 git fetch + git reset --hard origin/@{upstream}
          （未设 upstream 时退化为 git pull --ff-only）
- delete：shutil.rmtree 删除目录，删除前确认是 git 仓库或至少含 SKILL.md
- list / info：复用 skill_loader.discover_skills() 的 L1 索引（跨所有目录扫描）

安全：
- 目录名只允许 [a-zA-Z0-9_-]，禁止 . / \\ 等字符，杜绝路径穿越
- 所有 git 子进程显式传入 cwd，不依赖 shell
- 删除前二次确认目标在某个 skills_dir 内
- install 的 target_dir 必须是已配置的 skills 目录之一（防穿越）
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.config_loader import get_config
from agent.skill_loader import discover_skills


# ============================================================
# 常量与校验
# ============================================================

# 合法 skill 名：字母数字下划线短横线，1-64 字符
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SkillManageError(Exception):
    """skill 管理操作的业务错误，message 可直接展示给用户"""


def _validate_name(name: str) -> str:
    """校验 skill 名，非法则抛 SkillManageError。返回清洗后的名字。"""
    if not name or not isinstance(name, str):
        raise SkillManageError("skill 名不能为空")
    name = name.strip()
    if not _VALID_NAME.match(name):
        raise SkillManageError(
            f"skill 名非法: {name!r}（仅允许字母、数字、下划线、短横线，1-64 字符）"
        )
    # 额外防穿越：不允许 .. 之类（虽然正则已挡）
    if ".." in name or "/" in name or "\\" in name:
        raise SkillManageError(f"skill 名非法: {name!r}")
    return name


def _get_skills_dirs() -> list[Path]:
    """从全局配置拿 skills_dirs（多目录），目录不存在则创建。返回解析后的绝对路径列表。"""
    cfg = get_config()
    result: list[Path] = []
    for d in cfg.agent.skills_dirs:
        d.mkdir(parents=True, exist_ok=True)
        result.append(d.resolve())
    return result


def _locate_skill(name: str) -> tuple[Path, Path] | None:
    """跨所有 skills 目录查找 skill。

    返回 (skill 所在目录绝对路径, 所属 skills_dir 绝对路径)；找不到返回 None。
    """
    for skills_dir in _get_skills_dirs():
        target = skills_dir / name
        if target.exists():
            return target.resolve(), skills_dir
    return None


def _resolve_target_dir(target_dir: str, dirs: list[Path]) -> Path:
    """把前端传来的 target_dir 解析为已配置的 skills 目录之一（防穿越）。

    匹配规则：先按字符串/绝对路径精确匹配，再按末段目录名匹配。
    """
    if not target_dir:
        return dirs[0]
    tgt = Path(target_dir)
    tgt_resolved = tgt.resolve() if tgt.is_absolute() else None
    # 精确匹配
    for d in dirs:
        if str(d) == target_dir or (tgt_resolved is not None and d == tgt_resolved):
            return d
    # 末段目录名匹配
    for d in dirs:
        if d.name == target_dir:
            return d
    raise SkillManageError(
        f"目标目录不在已配置的 skills_dir 中: {target_dir}"
        f"（已配置: {', '.join(str(d) for d in dirs)}）"
    )


def _run_git(args: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str, str]:
    """执行 git 子进程，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        raise SkillManageError("未找到 git 命令，请先安装 git 并加入 PATH")
    except subprocess.TimeoutExpired:
        raise SkillManageError(f"git 操作超时（{timeout}s）: git {' '.join(args)}")


def _rmtree(path: Path) -> None:
    """跨平台安全删除目录树。

    Windows 下 git 仓库的 .git/objects 里有只读文件，shutil.rmtree 默认会失败。
    遇到 PermissionError 时强制改可写再删。
    """
    import stat

    def _on_error(func, fpath, exc_info):
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except Exception:
            # 再试一次 rmtree（可能是目录本身只读）
            try:
                os.chmod(fpath, stat.S_IWRITE)
                shutil.rmtree(fpath, ignore_errors=True)
            except Exception:
                pass

    shutil.rmtree(path, onerror=_on_error)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class SkillDetail:
    """单个 skill 的详情（list / info 共用）"""

    name: str
    description: str
    triggers: list[str]
    dir_path: str
    loaded: bool
    is_git_repo: bool
    git_remote: str | None  # origin URL，非 git 仓库为 None
    git_branch: str | None  # 当前分支
    git_commit: str | None  # 当前 commit 短 hash
    has_tools: bool  # 是否有 tools.py
    has_references: bool  # 是否有 references/ 目录

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "dir_path": self.dir_path,
            "loaded": self.loaded,
            "is_git_repo": self.is_git_repo,
            "git_remote": self.git_remote,
            "git_branch": self.git_branch,
            "git_commit": self.git_commit,
            "has_tools": self.has_tools,
            "has_references": self.has_references,
        }


# ============================================================
# 核心操作
# ============================================================

def install_skill(url: str, name: str | None = None, force: bool = False, target_dir: str | None = None) -> dict:
    """
    从 git 仓库安装 skill。

    Args:
        url: git 仓库 URL（https 或 ssh）
        name: 安装到 <skills_dir>/<name>/；不传则从 URL 末段推断（去 .git 后缀）
        force: 目标目录已存在时是否覆盖（先删再 clone）
        target_dir: 指定安装到哪个 skills 目录（路径或目录名）；不传则装到第一个（主）目录

    Returns:
        {"name": ..., "dir": ..., "description": ..., "triggers": [...]}

    Raises:
        SkillManageError: 名字非法 / 目录已存在且未 force / git clone 失败 / clone 产物无 SKILL.md
    """
    if not url or not isinstance(url, str):
        raise SkillManageError("git URL 不能为空")
    url = url.strip()

    # 推断名字
    if name is None:
        # 取 URL 末段，去掉 .git；同时兼容 / 和 \ 分隔符（本地 Windows 路径）
        tail = re.split(r"[\\/]", url.rstrip("/\\"))[-1]
        if tail.endswith(".git"):
            tail = tail[:-4]
        name = tail
    name = _validate_name(name)

    dirs = _get_skills_dirs()
    if not dirs:
        raise SkillManageError("未配置 skills_dir")
    dest_base = _resolve_target_dir(target_dir, dirs) if target_dir else dirs[0]
    target = dest_base / name

    # 目标已存在
    if target.exists():
        if not force:
            raise SkillManageError(
                f"目录已存在: {target}（如需覆盖请传 force=True 或调用 update）"
            )
        # force 模式：先删
        _rmtree(target)

    # git clone
    code, out, err = _run_git(["clone", "--depth", "1", url, str(target)], cwd=target.parent)
    if code != 0:
        # 清理半成品
        if target.exists():
            _rmtree(target)
        raise SkillManageError(f"git clone 失败: {err.strip() or out.strip()}")

    # 校验产物
    skill_md = target / "SKILL.md"
    if not skill_md.exists():
        _rmtree(target)
        raise SkillManageError(
            f"克隆成功但仓库根目录缺少 SKILL.md，已清理。请确认该仓库是一个合法 skill"
        )

    # 读 frontmatter 给出反馈
    registry = discover_skills(_get_skills_dirs())
    # 目录名可能和 frontmatter 里的 name 不一致，按目录路径匹配
    target_resolved = target.resolve()
    info = None
    for sk_name, sk_info in registry.items():
        if sk_info.dir_path and sk_info.dir_path.resolve() == target_resolved:
            info = sk_info
            break
    if info is None:
        # 极少见：SKILL.md 存在但 frontmatter 没解析出 name，且目录名也异常
        _rmtree(target)
        raise SkillManageError("SKILL.md 存在但无法解析 skill 元数据，已清理")

    return {
        "name": info.name,
        "dir": str(target),
        "description": info.description,
        "triggers": info.triggers,
    }


def update_skill(name: str) -> dict:
    """
    更新已安装的 skill（git fetch + reset --hard origin/<upstream>）。

    Returns:
        {"name": ..., "before": <commit>, "after": <commit>, "updated": bool}

    Raises:
        SkillManageError: 名字非法 / 目录不存在 / 不是 git 仓库 / git 操作失败
    """
    name = _validate_name(name)
    located = _locate_skill(name)
    if located is None:
        raise SkillManageError(f"skill 不存在: {name}")
    target, _skills_dir = located

    git_dir = target / ".git"
    if not git_dir.exists():
        raise SkillManageError(f"skill {name} 不是 git 仓库（无法 update，请删除后重新 install）")

    # 记录更新前 commit
    code, before, err = _run_git(["rev-parse", "--short", "HEAD"], cwd=target)
    before = before.strip() if code == 0 else "unknown"

    # 拿当前分支
    code, branch, err = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target)
    branch = branch.strip() if code == 0 else ""

    # 尝试 fetch + reset 到 origin/<branch>
    if branch and branch != "HEAD":
        code, out, err = _run_git(["fetch", "origin", branch], cwd=target)
        if code != 0:
            raise SkillManageError(f"git fetch 失败: {err.strip() or out.strip()}")

        code, out, err = _run_git(
            ["reset", "--hard", f"origin/{branch}"], cwd=target
        )
        if code != 0:
            raise SkillManageError(f"git reset 失败: {err.strip() or out.strip()}")
    else:
        # detached HEAD 或拿不到分支，退化为 pull --ff-only
        code, out, err = _run_git(["pull", "--ff-only"], cwd=target)
        if code != 0:
            raise SkillManageError(
                f"git pull 失败: {err.strip() or out.strip()}（detached HEAD，建议重新 install）"
            )

    # 更新后 commit
    code, after, err = _run_git(["rev-parse", "--short", "HEAD"], cwd=target)
    after = after.strip() if code == 0 else "unknown"

    return {
        "name": name,
        "before": before,
        "after": after,
        "updated": before != after,
    }


def delete_skill(name: str) -> dict:
    """
    删除已安装的 skill。

    Returns:
        {"name": ..., "dir": ..., "deleted": True}

    Raises:
        SkillManageError: 名字非法 / 目录不存在 / 目标不在 skills_dir 内（防穿越）
    """
    name = _validate_name(name)
    located = _locate_skill(name)
    if located is None:
        raise SkillManageError(f"skill 不存在: {name}")
    target, skills_dir = located

    # 二次防穿越：解析后必须仍在所属 skills_dir 内（_locate_skill 已保证，这里兜底确认）
    try:
        target.relative_to(skills_dir)
    except ValueError:
        raise SkillManageError(f"目标路径越界，拒绝删除: {target}")

    # 删除
    _rmtree(target)

    return {"name": name, "dir": str(target), "deleted": True}


def list_skills() -> list[dict]:
    """
    列出所有已安装 skill（基于 SKILL.md frontmatter 索引）。

    Returns:
        [{name, description, triggers, dir_path, loaded, is_git_repo, ...}, ...]
    """
    registry = discover_skills(_get_skills_dirs())
    result: list[dict] = []
    for name, info in sorted(registry.items()):
        detail = _build_detail(info)
        result.append(detail.to_dict())
    return result


def info_skill(name: str) -> dict:
    """
    查询单个 skill 详情。

    Raises:
        SkillManageError: 名字非法 / skill 不存在
    """
    name = _validate_name(name)
    registry = discover_skills(_get_skills_dirs())
    info = registry.get(name)
    if info is None:
        raise SkillManageError(f"skill 不存在: {name}")
    return _build_detail(info).to_dict()


# ============================================================
# 内部：构造详情
# ============================================================

def _build_detail(info) -> SkillDetail:
    """从 SkillInfo 构造 SkillDetail，补齐 git 状态。"""
    dir_path = info.dir_path
    is_git = (dir_path / ".git").exists()
    git_remote = None
    git_branch = None
    git_commit = None

    if is_git:
        # remote
        code, out, _ = _run_git(["remote", "get-url", "origin"], cwd=dir_path)
        if code == 0:
            git_remote = out.strip()
        # branch
        code, out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=dir_path)
        if code == 0:
            git_branch = out.strip()
        # commit
        code, out, _ = _run_git(["rev-parse", "--short", "HEAD"], cwd=dir_path)
        if code == 0:
            git_commit = out.strip()

    has_tools = (dir_path / "tools.py").exists()
    has_references = (dir_path / "references").is_dir()

    return SkillDetail(
        name=info.name,
        description=info.description,
        triggers=info.triggers,
        dir_path=str(dir_path),
        loaded=info.loaded,
        is_git_repo=is_git,
        git_remote=git_remote,
        git_branch=git_branch,
        git_commit=git_commit,
        has_tools=has_tools,
        has_references=has_references,
    )


# ============================================================
# 命令行入口
# ============================================================

def _cli() -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="skill_manager",
        description="mini-agent skill 管理工具（安装/更新/删除/列表/详情）",
    )
    parser.add_argument(
        "--config", default="config.yaml", help="配置文件路径（默认 config.yaml）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出所有已安装 skill")
    p_list.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_install = sub.add_parser("install", help="从 git 仓库安装 skill")
    p_install.add_argument("url", help="git 仓库 URL")
    p_install.add_argument("--name", default=None, help="安装目录名（默认从 URL 推断）")
    p_install.add_argument("--force", action="store_true", help="目录已存在时覆盖")
    p_install.add_argument("--target-dir", default=None, help="装到哪个 skills 目录（路径或目录名，默认第一个）")

    p_update = sub.add_parser("update", help="更新已安装的 skill")
    p_update.add_argument("name", help="skill 名")

    p_delete = sub.add_parser("delete", help="删除已安装的 skill")
    p_delete.add_argument("name", help="skill 名")
    p_delete.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    p_info = sub.add_parser("info", help="查看单个 skill 详情")
    p_info.add_argument("name", help="skill 名")

    args = parser.parse_args()

    # 加载配置
    from agent.config_loader import load_config
    try:
        load_config(args.config)
    except FileNotFoundError:
        print(f"[错误] 配置文件不存在: {args.config}", file=sys.stderr)
        return 2

    try:
        if args.cmd == "list":
            skills = list_skills()
            if args.json:
                print(json.dumps(skills, ensure_ascii=False, indent=2))
            else:
                if not skills:
                    print("（暂无 skill）")
                else:
                    print(f"已安装 {len(skills)} 个 skill：")
                    print()
                    for s in skills:
                        trig = f"  触发词: {', '.join(s['triggers'])}" if s["triggers"] else ""
                        git_mark = " [git]" if s["is_git_repo"] else ""
                        print(f"  - {s['name']}{git_mark}: {s['description']}{trig}")
            return 0

        elif args.cmd == "install":
            print(f"正在从 {args.url} 安装 skill...")
            result = install_skill(args.url, name=args.name, force=args.force, target_dir=args.target_dir)
            print(f"[OK] 安装成功: {result['name']}")
            print(f"     目录: {result['dir']}")
            print(f"     描述: {result['description']}")
            return 0

        elif args.cmd == "update":
            print(f"正在更新 {args.name}...")
            result = update_skill(args.name)
            if result["updated"]:
                print(f"[OK] 已更新: {result['name']}")
                print(f"     {result['before']} → {result['after']}")
            else:
                print(f"[OK] 已是最新: {result['name']} @ {result['after']}")
            return 0

        elif args.cmd == "delete":
            if not args.yes:
                confirm = input(f"确认删除 skill {args.name}? [y/N] ").strip().lower()
                if confirm not in ("y", "yes"):
                    print("已取消")
                    return 1
            result = delete_skill(args.name)
            print(f"[OK] 已删除: {result['name']}")
            print(f"     目录: {result['dir']}")
            return 0

        elif args.cmd == "info":
            detail = info_skill(args.name)
            print(json.dumps(detail, ensure_ascii=False, indent=2))
            return 0

    except SkillManageError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[异常] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
