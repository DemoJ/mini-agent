"""
验证 skill 三层懒加载全链路（不调用 LLM）
=========================================
验证点：
  L1 - discover_skills 只读 frontmatter，构建索引
  L2 - Agent._do_load_skill 读正文 + 注册工具（命名空间）
  L3 - weather__read_file 能读 references/，且防路径穿越
  - 幂等：重复 load_skill 不重复注册
  - system prompt 含 {skills_index} 注入
"""

import sys
from pathlib import Path

# 确保能 import 项目模块
sys.path.insert(0, str(Path(__file__).parent))

from skill_loader import discover_skills, load_skill_full, make_read_file_tool


def test_l1_discover():
    """L1：扫描 skills/ 只读 frontmatter"""
    print("=== L1 索引层测试 ===")
    registry = discover_skills("skills")
    assert "weather" in registry, "应发现 weather skill"
    info = registry["weather"]
    print(f"  name: {info.name}")
    print(f"  description: {info.description}")
    print(f"  triggers: {info.triggers}")
    print(f"  index_line: {info.index_line()}")
    assert "天气" in info.triggers, "triggers 应含 '天气'"
    assert info.loaded is False, "L1 阶段 loaded 应为 False"
    print("  [OK] L1 通过\n")
    return info


def test_l2_load_full(info):
    """L2：读完整正文 + tools.py"""
    print("=== L2 指令层测试 ===")
    loaded = load_skill_full(info)
    print(f"  instructions 前 80 字: {loaded.instructions[:80]}...")
    print(f"  工具数: {len(loaded.tools)}")
    assert "get_weather" in loaded.tools, "应含 get_weather 工具"
    tool = loaded.tools["get_weather"]
    print(f"  get_weather description: {tool['description']}")
    # 实际调用工具函数
    result = tool["fn"](city="北京")
    print(f"  调用 get_weather('北京') → {result}")
    assert "city" in result and result["city"] == "北京"
    assert info.loaded is True, "L2 加载后 loaded 应为 True"
    print("  [OK] L2 通过\n")
    return loaded


def test_l3_read_file(info):
    """L3：read_file 读取 references/"""
    print("=== L3 参考层测试 ===")
    read_tool = make_read_file_tool(info.dir_path)
    # 正常读取
    res = read_tool["fn"](path="references/usage.md")
    print(f"  读取 references/usage.md → 前 60 字: {res['content'][:60]}...")
    assert "temperature_c" in res["content"], "应读到字段说明"
    # 路径穿越防护
    bad = read_tool["fn"](path="../../config.yaml")
    print(f"  尝试越权读 ../../config.yaml → {bad}")
    assert "error" in bad, "应拦截路径穿越"
    print("  [OK] L3 通过\n")


def test_agent_integration():
    """Agent 集成：load_skill 工具 + 命名空间 + 幂等"""
    print("=== Agent 集成测试 ===")
    # 用 mock 方式构造 Agent，避免真实 LLM 调用
    from agent_loop import Agent
    agent = Agent.__new__(Agent)  # 跳过 __init__（会连 LLM）

    # 手动初始化必要字段
    agent.tools = {}  # 先建空表
    from agent_loop import _builtin_tools
    agent.tools = _builtin_tools()
    agent.skills_registry = discover_skills("skills")
    agent._loaded_skills = set()
    agent._register_load_skill_tool()
    agent.openai_tools = agent._build_openai_tools()

    # 初始工具应有 bash / finish / load_skill
    print(f"  初始工具: {list(agent.tools.keys())}")
    assert {"bash", "finish", "load_skill"} <= set(agent.tools.keys())

    # 调用 load_skill("weather")
    result_str = agent._do_load_skill("weather")
    import json
    result = json.loads(result_str)
    print(f"  load_skill('weather') → ok={result['ok']}, tools={result['registered_tools']}")
    assert result["ok"] is True
    assert "weather__get_weather" in result["registered_tools"]
    assert "weather__read_file" in result["registered_tools"]
    assert "instructions" in result, "应返回 SKILL.md 正文"

    # 验证工具已注册到实例
    assert "weather__get_weather" in agent.tools
    assert "weather__read_file" in agent.tools
    print(f"  注册后工具: {list(agent.tools.keys())}")

    # 验证 weather__get_weather 可执行
    r = agent.tools["weather__get_weather"]["fn"](city="上海")
    print(f"  调用 weather__get_weather('上海') → {r}")
    assert r["city"] == "上海"

    # 幂等：再次 load_skill 应返回已加载提示，不重复注册
    r2_str = agent._do_load_skill("weather")
    r2 = json.loads(r2_str)
    print(f"  重复 load_skill → {r2}")
    assert r2["ok"] is True
    assert "已加载" in r2["message"]
    # 工具数量不应翻倍
    weather_tools = [k for k in agent.tools if k.startswith("weather__")]
    assert len(weather_tools) == 2, f"幂等失败，工具数={len(weather_tools)}"
    print(f"  weather 工具数（幂等）: {len(weather_tools)}")

    # 验证 OpenAI tools 已刷新
    openai_names = [t["function"]["name"] for t in agent.openai_tools]
    print(f"  OpenAI tools: {openai_names}")
    assert "weather__get_weather" in openai_names

    # load_skill 未知 skill
    r3 = json.loads(agent._do_load_skill("nonexist"))
    print(f"  load_skill('nonexist') → {r3}")
    assert "error" in r3

    print("  [OK] Agent 集成通过\n")


def test_system_prompt_index():
    """system prompt 的 {skills_index} 注入"""
    print("=== system prompt 索引注入测试 ===")
    from config_loader import load_config
    cfg = load_config("config.yaml")
    sp = cfg.agent.system_prompt
    assert "{skills_index}" in sp, "system prompt 应含 {skills_index} 占位符"
    print("  [OK] 占位符存在\n")


if __name__ == "__main__":
    info = test_l1_discover()
    test_l2_load_full(info)
    test_l3_read_file(info)
    test_agent_integration()
    test_system_prompt_index()
    print("=" * 50)
    print("[ALL PASS] 全部测试通过")
