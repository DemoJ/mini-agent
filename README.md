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
├── config.yaml             # 本地配置（已 gitignore）
├── config.example.yaml     # 示例配置
├── pyproject.toml          # 项目元数据与打包配置
├── agent/                  # 核心包
│   ├── __init__.py         # 包入口，导出 Agent / Config 等
│   ├── agent_loop.py       # Agent 自主循环 + 工具执行分发
│   ├── config_loader.py    # 配置加载/保存模块
│   ├── skill_loader.py     # Skill 三层懒加载（索引/指令/参考）
│   ├── skill_manager.py    # Skill 管理（安装/更新/删除/列表/详情）
│   └── tools/              # 工具注册
│       ├── __init__.py     # 导出 get_builtin_tools / get_skill_tool_defs
│       ├── builtin.py      # 内置工具：bash / finish
│       └── skill_tools.py  # skill 工具 schema：load_skill / list / install / update / delete / info
├── test/                   # 单元测试
│   └── test_skill_manager.py
├── web/                    # WebUI 前端
│   ├── index.html          # 单页前端
│   ├── app.js              # 交互逻辑
│   └── style.css           # 样式
├── prompt/
│   ├── system.md           # 系统提示词
│   └── user.md             # 用户提示词模板
└── skills/                 # 已安装 skill 目录（每个子目录是一个 skill）
```

## 扩展

### 注册新工具

工具按类型分到 `agent/tools/` 下：

- **无状态内置工具**（如 `bash`、`finish`）：加到 `agent/tools/builtin.py` 的 `get_builtin_tools()` 返回字典中。
- **需要访问 Agent 状态的工具**：在 `agent/tools/skill_tools.py` 的 `get_skill_tool_defs()` 中定义 schema（`fn` 置 `None`），然后在 `agent/agent_loop.py` 的 `_execute_tool_call()` 中添加分发分支，调用对应的 `_do_*` 方法。

```python
# agent/tools/builtin.py —— 无状态工具
def my_tool_func(arg: str) -> dict:
    ...

def get_builtin_tools() -> dict:
    return {
        "bash": { ... },
        "finish": { ... },
        "my_tool": {
            "description": "工具描述",
            "parameters": { "type": "object", "properties": { ... }, "required": [...] },
            "fn": my_tool_func,
        },
    }
```

### Skill 管理

Skill 是可按需加载的能力包，每个 skill 是 `skills/` 下的一个子目录，至少含 `SKILL.md`（frontmatter + 指令正文），可选 `tools.py`（工具定义）和 `references/`（参考文档）。

**命令行管理**（`agent/skill_manager.py`）：

```bash
# 列出已安装 skill
python -m agent.skill_manager list

# 从 git 仓库安装（name 可选，默认从 URL 推断）
python -m agent.skill_manager install https://github.com/foo/my-skill
python -m agent.skill_manager install https://github.com/foo/my-skill --name my-skill

# 更新某个 skill（git fetch + reset）
python -m agent.skill_manager update my-skill

# 删除某个 skill
python -m agent.skill_manager delete my-skill -y

# 查看详情
python -m agent.skill_manager info my-skill
```

**HTTP API**（`webui.py` 启动后可用）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/skills` | 列出所有已安装 skill |
| GET | `/api/skills/{name}` | 查询单个 skill 详情 |
| POST | `/api/skills/install` | 安装 skill，body: `{"url": "...", "name": "...", "force": false}` |
| POST | `/api/skills/update` | 更新 skill，body: `{"name": "..."}` |
| DELETE | `/api/skills/{name}` | 删除 skill |

安装/更新/删除后会自动重建 Agent，刷新 skill 索引。

**安全**：skill 名只允许字母、数字、下划线、短横线（1-64 字符），杜绝路径穿越；删除前二次确认目标在 `skills_dir` 内。

**创建自己的 skill**：在 `skills/` 下新建目录，至少写一个 `SKILL.md`：

```
skills/my-skill/
├── SKILL.md        # 必需：frontmatter + 指令正文
├── tools.py        # 可选：暴露 get_tools() 返回工具字典
└── references/     # 可选：参考文档，skill 内可通过 read_file 工具读取
```

`SKILL.md` frontmatter 格式：

```yaml
---
name: my-skill
description: 一句话描述这个 skill 能做什么
triggers: [关键词1, 关键词2]
---
这里是给 LLM 看的完整指令正文……
```
