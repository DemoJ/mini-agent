"""
测试 skills_dir 多目录功能
==========================
覆盖：
- AgentConfig 解析单字符串 / 列表 / 默认值，to_dict 返回列表
- discover_skills 多目录扫描 + 同名靠前优先 + 单目录参数兼容
- save_config：单目录写标量、多目录写列表、重载
- skill_manager 跨目录 list / _locate_skill / info_skill
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config_loader import AgentConfig, save_config, load_config
from agent.skill_loader import discover_skills


def _make_skill(path: Path, name: str, desc: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\ntriggers: []\n---\n指令正文\n",
        encoding="utf-8",
    )


def test_config_parse():
    # 单字符串
    cfg = AgentConfig({"skills_dir": "skills"}, base_dir=ROOT)
    assert isinstance(cfg.skills_dirs, list) and len(cfg.skills_dirs) == 1, cfg.skills_dirs
    print("[OK] 单字符串解析为列表:", cfg.skills_dirs)

    # 列表
    cfg2 = AgentConfig({"skills_dir": ["skills", "skills_extra"]}, base_dir=ROOT)
    assert len(cfg2.skills_dirs) == 2, cfg2.skills_dirs
    print("[OK] 列表解析:", cfg2.skills_dirs)

    # 默认
    cfg3 = AgentConfig({}, base_dir=ROOT)
    assert cfg3.skills_dirs == [ROOT / "skills"], cfg3.skills_dirs
    print("[OK] 默认值:", cfg3.skills_dirs)

    # to_dict 返回列表
    d = cfg2.to_dict()
    assert isinstance(d["skills_dir"], list), d["skills_dir"]
    print("[OK] to_dict 返回列表:", d["skills_dir"])

    # 绝对路径也支持
    cfg4 = AgentConfig({"skills_dir": [str(ROOT / "skills"), "skills_extra"]}, base_dir=ROOT)
    assert len(cfg4.skills_dirs) == 2, cfg4.skills_dirs
    print("[OK] 混合绝对/相对路径:", cfg4.skills_dirs)


def test_discover_multi():
    tmp = Path(tempfile.mkdtemp(prefix="mini_agent_disc_"))
    try:
        dir1, dir2 = tmp / "skills", tmp / "skills_extra"
        dir1.mkdir(); dir2.mkdir()
        _make_skill(dir1 / "skill_a", "skill_a", "目录1的A")
        _make_skill(dir1 / "skill_b", "skill_b", "目录1的B")
        _make_skill(dir2 / "skill_b", "skill_b", "目录2的B")  # 同名
        _make_skill(dir2 / "skill_c", "skill_c", "目录2的C")

        reg = discover_skills([dir1, dir2])
        assert {"skill_a", "skill_b", "skill_c"} <= set(reg.keys()), list(reg)
        # 同名靠前优先：skill_b 应来自 dir1
        assert reg["skill_b"].dir_path == (dir1 / "skill_b"), reg["skill_b"].dir_path
        print("[OK] 多目录扫描 + 同名靠前优先: skill_b ->", reg["skill_b"].dir_path.name)

        # 单目录参数兼容
        reg2 = discover_skills(dir2)
        assert set(reg2.keys()) == {"skill_b", "skill_c"}, list(reg2)
        print("[OK] 单目录参数兼容")

        # 字符串参数兼容
        reg3 = discover_skills(str(dir2))
        assert set(reg3.keys()) == {"skill_b", "skill_c"}, list(reg3)
        print("[OK] 字符串参数兼容")

        # 不存在的目录跳过
        reg4 = discover_skills([dir1, tmp / "nope"])
        assert "skill_a" in reg4, list(reg4)
        print("[OK] 不存在的目录被跳过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_config_single_vs_multi():
    tmp = Path(tempfile.mkdtemp(prefix="mini_agent_save_"))
    try:
        cfg_path = tmp / "config.yaml"

        # 单目录 → 写标量
        save_config({
            "api": {"base_url": "x", "api_key": "k", "model": "m"},
            "agent": {"skills_dir": ["skills"]},
        }, cfg_path)
        raw = cfg_path.read_text(encoding="utf-8")
        assert "skills_dir: skills" in raw and "skills_extra" not in raw, raw
        print("[OK] 单目录写标量")

        # 多目录 → 写列表
        save_config({
            "api": {"base_url": "x", "api_key": "k", "model": "m"},
            "agent": {"skills_dir": ["skills", "skills_extra"]},
        }, cfg_path)
        raw = cfg_path.read_text(encoding="utf-8")
        assert "skills_extra" in raw, raw
        print("[OK] 多目录写列表")

        # 重载验证
        cfg = load_config(cfg_path)
        assert len(cfg.agent.skills_dirs) == 2, cfg.agent.skills_dirs
        print("[OK] 多目录重载:", cfg.agent.skills_dirs)

        # 前端误传多行字符串也兼容
        save_config({
            "api": {"base_url": "x", "api_key": "k", "model": "m"},
            "agent": {"skills_dir": "skills\nskills_extra"},
        }, cfg_path)
        cfg = load_config(cfg_path)
        assert len(cfg.agent.skills_dirs) == 2, cfg.agent.skills_dirs
        print("[OK] 多行字符串兼容:", cfg.agent.skills_dirs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_skill_manager_multi():
    tmp = Path(tempfile.mkdtemp(prefix="mini_agent_mgr_"))
    try:
        dir1, dir2 = tmp / "skills", tmp / "skills_extra"
        _make_skill(dir1 / "sk1", "sk1", "主目录skill")
        _make_skill(dir2 / "sk2", "sk2", "扩展目录skill")

        cfg_path = tmp / "config.yaml"
        save_config({
            "api": {"base_url": "x", "api_key": "k", "model": "m"},
            "agent": {"skills_dir": ["skills", "skills_extra"]},
        }, cfg_path)
        load_config(cfg_path)

        from agent import skill_manager as sm

        # list 跨目录
        skills = sm.list_skills()
        names = {s["name"] for s in skills}
        assert names == {"sk1", "sk2"}, names
        print("[OK] list_skills 跨目录:", names)

        # _locate_skill 跨目录定位
        loc1 = sm._locate_skill("sk1")
        assert loc1 is not None and loc1[1].name == "skills", loc1
        loc2 = sm._locate_skill("sk2")
        assert loc2 is not None and loc2[1].name == "skills_extra", loc2
        print("[OK] _locate_skill 跨目录定位")

        # info_skill 跨目录
        info = sm.info_skill("sk2")
        assert Path(info["dir_path"]).name == "sk2", info
        print("[OK] info_skill 跨目录:", Path(info["dir_path"]).name)

        # _resolve_target_dir：合法 + 非法
        dirs = sm._get_skills_dirs()
        assert sm._resolve_target_dir("skills_extra", dirs).name == "skills_extra"
        assert sm._resolve_target_dir(None, dirs) == dirs[0]
        try:
            sm._resolve_target_dir("not_a_configured_dir", dirs)
            assert False, "应拒绝未配置目录"
        except sm.SkillManageError:
            print("[OK] _resolve_target_dir 拒绝未配置目录")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_config_parse()
    print()
    test_discover_multi()
    print()
    test_save_config_single_vs_multi()
    print()
    test_skill_manager_multi()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
