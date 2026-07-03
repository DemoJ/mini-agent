---
name: weather
description: 查询指定城市的天气信息（模拟数据，用于演示 skill 三层懒加载）
triggers:
  - 天气
  - weather
  - 下雨
  - 温度
---

# Weather Skill

你已激活天气查询能力。现在可以使用 `weather__get_weather` 工具查询任意城市的天气。

## 使用规则

1. 当用户询问某城市天气时，调用 `weather__get_weather(city=城市名)` 获取数据。
2. 拿到结果后，用自然语言向用户播报：温度、天气状况、是否需要带伞等。
3. 如需了解返回字段含义，可用 `weather__read_file(path="references/usage.md")` 读取说明文档。
4. 查询完成后调用 `finish(summary=播报内容)` 结束。

## 示例对话

用户：北京今天天气怎么样？
你：（调用 weather__get_weather(city="北京")）→ 北京今天晴，气温 22°C，无需带伞。
