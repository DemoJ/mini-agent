# mini-agent

一个最小的 Agent Loop 实现，使用 OpenAI SDK 调用兼容 API（OpenAI、Azure、ollama、vLLM 等）。

## 设计

这是一个**自主 Agent** —— 每一轮用户消息，Agent 自主决定：

1. **调用工具**（如 `bash`） → 获取信息、执行操作 → 继续思考
2. **产生回复** → 向用户说话，本轮结束
3. **调用 `finish`** → 完成任务，给出最终总结，本轮结束

没有硬性的步数限制打断它，Agent 自己判断何时完成。

## 快速开始

### 1. 配置

```bash
cp config.yaml.example config.yaml
```

编辑 `config.yaml`，至少填写 `api_key`：

```yaml
api:
  base_url: "https://api.openai.com/v1"
  api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxx"
  model: "gpt-4o"
```

### 2. 安装依赖

```bash
pip install -e .
```

### 3. 运行

```bash
python main.py
```

直接进入交互式对话，输入 `/exit` 退出，`/reset` 清空历史。

## 项目结构

```
mini-agent/
├── main.py                 # 入口，REPL 交互
├── agent_loop.py           # Agent 自主循环 + 工具注册
├── config_loader.py        # 配置加载模块
├── config.yaml             # 本地配置（已 gitignore）
├── config.yaml.example     # 示例配置
├── prompt/
│   ├── system.md           # 系统提示词
│   └── user.md             # 用户提示词模板
└── .gitignore
```

## 扩展

在 `agent_loop.py` 的 `TOOLS` 字典中注册新工具即可：

```python
TOOLS: dict[str, dict[str, Any]] = {
    "bash": { ... },
    "finish": { ... },
    "my_tool": {
        "description": "工具描述",
        "parameters": { "type": "object", "properties": { ... }, "required": [...] },
        "fn": my_tool_func,
    },
}
```
