# Weather Skill 字段说明

`weather__get_weather` 工具返回的 JSON 字段含义：

| 字段 | 类型 | 说明 |
|------|------|------|
| city | string | 查询的城市名 |
| condition | string | 天气状况：晴/多云/阴/小雨/大雨/雷阵雨/雪 |
| temperature_c | int | 摄氏温度 |
| humidity_pct | int | 相对湿度百分比 |
| need_umbrella | bool | 是否建议带伞（True=下雨类天气） |

## 播报建议

- 气温 < 0°C：提醒注意防寒
- 气温 > 30°C：提醒注意防暑
- need_umbrella=true：明确建议带伞
- humidity > 80%：体感可能闷热
