# Feishu-Cursor Bridge

通过飞书（Feishu/Lark）与 Cursor AI Agent 对话的桥接工具。

在飞书中发送消息，自动转发到 Cursor Composer，由 AI Agent 处理后将结果回复到飞书。

## 功能

- **实时消息接收** — 基于飞书 WebSocket 长连接，毫秒级消息送达
- **自动触发 Cursor** — 收到飞书消息后自动激活 Cursor Composer，无需手动操作
- **多模式支持** — Agent / Ask / Plan / Debug 四种交互模式
- **模型切换** — 支持在飞书端切换 AI 模型
- **上下文管理** — 支持添加文件上下文、新建/清除对话
- **飞书指令** — 通过 `/` 前缀指令控制模式、模型、上下文等

## 架构

```
飞书用户 ──WebSocket──> monitor.py ──键盘自动化──> Cursor Composer
                            │                          │
                            │                    AI Agent 处理
                            │                          │
                       飞书 API <───── MCP 工具 ────── 回复
```

## 快速开始

详见 [USAGE.md](USAGE.md)

## 飞书指令

| 指令 | 说明 |
|------|------|
| `/mode <agent\|ask\|plan\|debug>` | 切换交互模式 |
| `/model <模型名>` | 切换 AI 模型 |
| `/new` | 新建对话（在新 Composer 中打开） |
| `/status` | 查看当前状态 |
| `/help` | 显示帮助 |
| `/clear` | 完全重置所有状态 |
| `/context <文件路径>` | 添加文件到对话上下文 |

普通文本消息直接作为 AI 对话内容。

## 文件结构

```
feishu-bridge/
├── monitor.py          # 核心：WebSocket 长连接 + 指令处理 + 自动触发
├── formatter.py        # 飞书消息格式解析与转换
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量模板（App ID / Secret）
├── state.json.example  # 状态文件模板
├── USAGE.md            # 详细使用说明
└── .cursor/rules/
    └── feishu-bridge.mdc  # Cursor Agent 规则
```

## 系统要求

- Windows 10/11（键盘自动化依赖 Windows API）
- Python 3.10+
- Cursor IDE
- 飞书企业自建应用（需启用机器人能力和消息事件订阅）

## License

MIT
