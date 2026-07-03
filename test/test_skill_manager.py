"""
Skill 管理器单元测试
====================
用真实的本地 git 仓库做 install/update/delete 端到端验证，不依赖网络。

覆盖：
- _validate_name 合法/非法
- install：成功、目录已存在、force 覆盖、缺 SKILL.md、名字非法
- list：列出已安装
- info：详情正确、不存在
- update：成功、非 git 仓库、不存在
- delete：成功、不存在、防路径穿越
- SkillManageError 抛错
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 把项目根目录加入 sys.path，使 agent 包可被导入
# （无论从项目根 `python -m test.test_skill_manager` 还是从 test/ 直接运行都生效）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config_loader import load_config
from agent.skill_manager import (
    SkillManageError,
    _validate_name,
    delete_skill,
    info_skill,
    install_skill,
    list_skills,
    update_skill,
)


# ============================================================
# 测试夹具：构造临时配置 + 临时 skills_dir + 真实 git 仓库
# ============================================================

def _setup_isolated_env():
    """
    构造一个隔离的测试环境：
    - 临时目录作为项目根
    - 写一份 config.yaml，skills_dir 指向临时目录下的 skills
    - 返回 (tmpdir, skills_dir, config_path)
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="skill_mgr_test_"))

    # 准备 git 身份（ci 环境可能没配）
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@test.local")
    env.setdefault("GIT_COMMITTER_NAME", "test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@test.local")

    skills_dir = tmpdir / "skills"
    skills_dir.mkdir()

    # 写 config.yaml
    cfg_path = tmpdir / "config.yaml"
    cfg_path.write_text(
        f"""api:
  base_url: "http://localhost/v1"
  api_key: "sk-test"
  model: "test-model"
agent:
  max_steps: 5
  temperature: 0.5
  max_tokens: 1024
  reasoning_effort: ~
  system_prompt: "sys"
  user_prompt: "u"
  skills_dir: "{skills_dir.as_posix()}"
debug:
  log_llm_request: false
""",
        encoding="utf-8",
    )

    load_config(str(cfg_path))
    return tmpdir, skills_dir, cfg_path, env


def _make_git_skill_repo(parent: Path, name: str, description: str, env: dict) -> Path:
    """
    在 parent 下创建一个真实可克隆的本地 git 仓库（bare 或普通都行，这里用普通仓库）。
    仓库根目录有 SKILL.md + tools.py。
    返回仓库路径。
    """
    repo = parent / f"{name}_src"
    repo.mkdir()
    _run_git(["init"], cwd=repo, env=env)
    _run_git(["config", "user.name", "test"], cwd=repo, env=env)
    _run_git(["config", "user.email", "test@test.local"], cwd=repo, env=env)

    skill_md = (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"triggers:\n"
        f"  - {name}\n"
        f"  - 测试\n"
        f"---\n\n"
        f"# {name} Skill\n\n这是测试 skill 的正文。\n"
    )
    (repo / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (repo / "tools.py").write_text(
        "def get_tools():\n    return {}\n", encoding="utf-8"
    )

    _run_git(["add", "."], cwd=repo, env=env)
    _run_git(["commit", "-m", "init"], cwd=repo, env=env)

    return repo


def _run_git(args, cwd, env=None, timeout=30):
    """测试用 git 执行器，直接调 subprocess。"""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


# ============================================================
# 1. 名字校验
# ============================================================

def test_validate_name_ok():
    assert _validate_name("weather") == "weather"
    assert _validate_name("my-skill-1") == "my-skill-1"
    assert _validate_name("  trim  ") == "trim"
    print("[OK] _validate_name 合法名字")


def test_validate_name_illegal():
    bad_cases = ["", "   ", "with space", "a/b", "a\\b", "../etc", "a.b",
                 "中文", "a" * 65, "with#hash"]
    for bad in bad_cases:
        try:
            _validate_name(bad)
        except SkillManageError:
            continue
        raise AssertionError(f"应拒绝非法名字: {bad!r}")
    print("[OK] _validate_name 拒绝非法名字")


# ============================================================
# 2. install
# ============================================================

def test_install_success():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "演示 skill", env)

        # 显式传 name，目录名可控
        result = install_skill(str(src_repo), name="demo")
        assert result["name"] == "demo"
        assert result["description"] == "演示 skill"
        assert (skills_dir / "demo" / "SKILL.md").exists()
        assert (skills_dir / "demo" / ".git").exists()
        print("[OK] install 成功")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_install_with_custom_name():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "orig", "原始", env)
        result = install_skill(
            str(src_repo), name="my-alias"
        )
        # 目录名是 my-alias，但 frontmatter name=orig，discover 后返回 orig
        assert (skills_dir / "my-alias").exists()
        assert result["name"] == "orig"
        assert result["description"] == "原始"
        print("[OK] install 自定义名字")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_install_already_exists_no_force():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "演示", env)
        install_skill(str(src_repo), name="demo")

        # 再装一次，应报错
        try:
            install_skill(str(src_repo), name="demo")
        except SkillManageError as e:
            assert "已存在" in str(e)
            print("[OK] install 目录已存在报错")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_install_force_overwrite():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "第一版", env)
        install_skill(str(src_repo), name="demo")

        # 在目标目录里塞一个文件，验证 force 会清空
        (skills_dir / "demo" / "junk.txt").write_text("xxx", encoding="utf-8")

        # force 重新安装
        result = install_skill(str(src_repo), name="demo", force=True)
        assert result["name"] == "demo"
        # junk.txt 应该没了
        assert not (skills_dir / "demo" / "junk.txt").exists()
        print("[OK] install force 覆盖")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_install_missing_skill_md():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        # 构造一个没有 SKILL.md 的 git 仓库
        bad_repo = tmpdir / "bad_src"
        bad_repo.mkdir()
        _run_git(["init"], cwd=bad_repo, env=env)
        _run_git(["config", "user.name", "test"], cwd=bad_repo, env=env)
        _run_git(["config", "user.email", "test@test.local"], cwd=bad_repo, env=env)
        (bad_repo / "README.md").write_text("no skill here", encoding="utf-8")
        _run_git(["add", "."], cwd=bad_repo, env=env)
        _run_git(["commit", "-m", "init"], cwd=bad_repo, env=env)

        try:
            install_skill(str(bad_repo), name="badskill")
        except SkillManageError as e:
            assert "SKILL.md" in str(e)
            # 目录应被清理
            assert not (skills_dir / "badskill").exists()
            print("[OK] install 缺 SKILL.md 报错并清理")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_install_illegal_name():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "x", env)
        try:
            install_skill(str(src_repo), name="../escape")
        except SkillManageError as e:
            assert "非法" in str(e) or "illegal" in str(e).lower()
            print("[OK] install 非法名字被拒")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 3. list / info
# ============================================================

def test_list_empty():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        result = list_skills()
        assert result == []
        print("[OK] list 空目录")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_list_after_install():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "演示 skill", env)
        install_skill(str(src_repo), name="demo")

        result = list_skills()
        assert len(result) == 1
        s = result[0]
        assert s["name"] == "demo"
        assert s["description"] == "演示 skill"
        assert s["is_git_repo"] is True
        assert s["git_remote"] is not None
        assert s["has_tools"] is True
        print("[OK] list 含已安装 skill")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_info_success():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "演示", env)
        install_skill(str(src_repo), name="demo")

        detail = info_skill("demo")
        assert detail["name"] == "demo"
        assert detail["is_git_repo"] is True
        assert detail["git_commit"] is not None
        assert "测试" in detail["triggers"]
        print("[OK] info 详情")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_info_not_found():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        try:
            info_skill("nonexistent")
        except SkillManageError as e:
            assert "不存在" in str(e)
            print("[OK] info 不存在报错")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 4. update
# ============================================================

def test_update_success_no_change():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "演示", env)
        install_skill(str(src_repo), name="demo")

        result = update_skill("demo")
        assert result["updated"] is False
        assert result["before"] == result["after"]
        print("[OK] update 无新提交")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_update_with_new_commit():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "第一版", env)
        install_skill(str(src_repo), name="demo")

        before = info_skill("demo")["git_commit"]

        # 在源仓库新增 commit
        (src_repo / "SKILL.md").write_text(
            "---\nname: demo\ndescription: 第二版\ntriggers: [demo]\n---\n\n第二版\n",
            encoding="utf-8",
        )
        _run_git(["add", "."], cwd=src_repo, env=env)
        _run_git(["commit", "-m", "v2"], cwd=src_repo, env=env)

        result = update_skill("demo")
        assert result["updated"] is True
        after = info_skill("demo")["git_commit"]
        assert result["after"] == after
        # description 应已更新
        assert info_skill("demo")["description"] == "第二版"
        print("[OK] update 拉到新提交")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_update_not_git_repo():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        # 手动建一个非 git 的 skill 目录
        skill_dir = skills_dir / "manual"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: manual\ndescription: 手动\n---\n\n正文\n",
            encoding="utf-8",
        )
        try:
            update_skill("manual")
        except SkillManageError as e:
            assert "不是 git 仓库" in str(e)
            print("[OK] update 非 git 仓库报错")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_update_not_found():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        try:
            update_skill("ghost")
        except SkillManageError as e:
            assert "不存在" in str(e)
            print("[OK] update 不存在报错")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 5. delete
# ============================================================

def test_delete_success():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        src_repo = _make_git_skill_repo(tmpdir, "demo", "演示", env)
        install_skill(str(src_repo), name="demo")

        result = delete_skill("demo")
        assert result["deleted"] is True
        assert not (skills_dir / "demo").exists()
        print("[OK] delete 成功")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_delete_not_found():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        try:
            delete_skill("ghost")
        except SkillManageError as e:
            assert "不存在" in str(e)
            print("[OK] delete 不存在报错")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_delete_path_traversal_blocked():
    tmpdir, skills_dir, cfg_path, env = _setup_isolated_env()
    try:
        # _validate_name 已挡掉 ..，但即使绕过名字校验，_skill_path 也只拼到 skills_dir 下
        # 这里验证名字校验层
        try:
            delete_skill("../escape")
        except SkillManageError as e:
            assert "非法" in str(e)
            print("[OK] delete 路径穿越被名字校验拦截")
            return
        raise AssertionError("应抛 SkillManageError")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# main
# ============================================================

if __name__ == "__main__":
    test_validate_name_ok()
    test_validate_name_illegal()
    test_install_success()
    test_install_with_custom_name()
    test_install_already_exists_no_force()
    test_install_force_overwrite()
    test_install_missing_skill_md()
    test_install_illegal_name()
    test_list_empty()
    test_list_after_install()
    test_info_success()
    test_info_not_found()
    test_update_success_no_change()
    test_update_with_new_commit()
    test_update_not_git_repo()
    test_update_not_found()
    test_delete_success()
    test_delete_not_found()
    test_delete_path_traversal_blocked()
    print("\n全部测试通过 ✓")
