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

> 国内网络建议使用镜像源加速：
>
> ```bash
> pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```
>
> 可选镜像源：
> - 清华：`https://pypi.tuna.tsinghua.edu.cn/simple`
> - 阿里：`https://mirrors.aliyun.com/pypi/simple/`
> - 腾讯：`https://mirrors.cloud.tencent.com/pypi/simple`
> - 中科大：`https://pypi.mirrors.ustc.edu.cn/simple/`


### 3. 运行

提供两种交互方式：

#### 命令行 REPL

```bash
python main.py
```

直接进入交互式对话，输入 `/exit` 退出，`/reset` 清空历史。

#### WebUI

```bash
python webui.py                 # 默认 127.0.0.1:8000
python webui.py --port 8080     # 自定义端口
```

浏览器打开 `http://127.0.0.1:8000`：

- **对话页**：输入消息与 Agent 对话，可查看思考内容（开启 `reasoning_effort` 时）和工具调用过程
- **设置页**：在线编辑 API 配置和 Agent 参数，保存后即时生效并写回 `config.yaml`

> WebUI 为单 Agent 串行处理，同一时刻只处理一条消息。对话历史仅保留在内存中，刷新页面会清空。

## 项目结构

```
mini-agent/
├── main.py                 # REPL 入口
├── webui.py                # WebUI 入口（FastAPI）
├── agent_loop.py           # Agent 自主循环 + 工具注册
├── config_loader.py        # 配置加载/保存模块
├── config.yaml             # 本地配置（已 gitignore）
├── config.yaml.example     # 示例配置
├── pip.ini.example         # pip 镜像源示例配置
├── web/                    # WebUI 前端
│   ├── index.html          # 单页前端
│   ├── app.js              # 交互逻辑
│   └── style.css           # 样式
├── docs/
│   └── webui-requirements.md  # WebUI 需求文档
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
