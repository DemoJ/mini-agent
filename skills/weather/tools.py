"""
weather skill 的工具定义
=======================
暴露 get_tools() 返回该 skill 提供的工具字典。
框架会以 weather__<tool_name> 的命名空间注册到 Agent。
"""

import random


def _get_weather(city: str) -> dict:
    """查询城市天气（演示用，返回模拟数据）"""
    # 模拟数据
    conditions = ["晴", "多云", "阴", "小雨", "大雨", "雷阵雨", "雪"]
    cond = random.choice(conditions)
    temp = random.randint(-5, 35)
    humidity = random.randint(30, 95)

    # 简单逻辑：下雨类天气建议带伞
    need_umbrella = "雨" in cond or cond == "雷阵雨"

    return {
        "city": city,
        "condition": cond,
        "temperature_c": temp,
        "humidity_pct": humidity,
        "need_umbrella": need_umbrella,
    }


def get_tools() -> dict:
    """返回 weather skill 提供的工具，供 Agent 注册。"""
    return {
        "get_weather": {
            "description": "查询指定城市的当前天气（温度、天气状况、湿度、是否需要带伞）",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，如 北京、上海、深圳",
                    }
                },
                "required": ["city"],
            },
            "fn": _get_weather,
        }
    }
