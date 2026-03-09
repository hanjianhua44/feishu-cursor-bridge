# Feishu-Cursor Bridge 使用说明

## 目录

- [概述](#概述)
- [前置条件](#前置条件)
- [第一步：创建飞书企业自建应用](#第一步创建飞书企业自建应用)
- [第二步：配置 Cursor MCP](#第二步配置-cursor-mcp)
- [第三步：配置 Cursor Agent 规则](#第三步配置-cursor-agent-规则)
- [第四步：安装依赖并启动](#第四步安装依赖并启动)
- [日常使用](#日常使用)
- [飞书指令参考](#飞书指令参考)
- [工作模式说明](#工作模式说明)
- [常见问题](#常见问题)

---

## 概述

Feishu-Cursor Bridge 是一个桥接工具，让你可以通过飞书直接与 Cursor IDE 中的 AI Agent 对话。你在飞书中发送的消息会自动转发到 Cursor Composer，AI Agent 处理完毕后将结果回复到飞书。

**典型使用场景：**
- 在手机端通过飞书远程操控电脑上的 Cursor 执行编码任务
- 不在电脑前时，用飞书向 AI Agent 下达指令
- 让 AI Agent 执行文件操作、运行命令、发送邮件等，结果直接推送到飞书

## 前置条件

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 |
| Python | 3.10 或更高版本 |
| Cursor IDE | 已安装并能正常使用 Agent 功能 |
| 飞书账号 | 企业版，具有创建自建应用的权限 |

## 第一步：创建飞书企业自建应用

### 1.1 创建应用

1. 登录 [飞书开放平台](https://open.feishu.cn/app)
2. 点击「创建企业自建应用」
3. 填写应用名称（如 "Cursor Bridge"），上传图标
4. 记录生成的 **App ID** 和 **App Secret**

### 1.2 添加机器人能力

1. 进入应用 → 「添加应用能力」
2. 勾选「机器人」并保存

### 1.3 配置权限

进入「权限管理」，申请并开启以下权限：

- `im:chat` — 获取群组信息
- `im:chat:readonly` — 读取群组信息
- `im:message` — 发送消息
- `im:message:readonly` — 读取消息
- `im:resource` — 获取消息中的资源文件

### 1.4 配置事件订阅

1. 进入「事件订阅」
2. 订阅方式选择「**使用长连接接收事件**」（推荐）
3. 添加事件：`im.message.receive_v1`（接收消息）
4. 保存配置

> **注意**：使用长连接模式无需配置公网域名，只需本地运行 monitor.py 即可。

### 1.5 发布应用

1. 进入「版本管理与发布」
2. 创建新版本，确认可用范围包含你自己
3. 提交审核并发布

### 1.6 与机器人开始对话

发布后，在飞书中搜索你的机器人名称，向它发送一条消息（如 "你好"）。这会建立 P2P 聊天通道。

## 第二步：配置 Cursor MCP

在你的工作区目录下编辑 `.cursor/mcp.json`，添加飞书 MCP 服务：

```json
{
  "mcpServers": {
    "feishu_bridge": {
      "command": "npx",
      "args": [
        "-y", "@larksuiteoapi/lark-mcp", "mcp",
        "-a", "<你的 App ID>",
        "-s", "<你的 App Secret>"
      ]
    }
  }
}
```

将 `<你的 App ID>` 和 `<你的 App Secret>` 替换为第一步中获取的值。

保存后重启 Cursor，确认在 MCP 工具列表中看到 `feishu_bridge` 相关工具。

## 第三步：配置 Cursor Agent 规则

将 `.cursor/rules/feishu-bridge.mdc` 文件放置到你的工作区中。这个规则文件告诉 Cursor Agent：

1. 识别 `[飞书]` 前缀的消息
2. 按照指定模式（agent/ask/plan/debug）处理
3. 处理完成后通过 MCP 工具将回复发送回飞书
4. 对来自飞书的消息自动授权所有操作

**关键配置项**（在规则文件中需要修改）：

- `state.json` 的路径 — 改为你实际部署的路径
- `feishu_bridge` 服务名 — 需要与 `mcp.json` 中的名称一致

## 第四步：安装依赖并启动

### 4.1 安装 Python 依赖

```bash
cd feishu-bridge
pip install -r requirements.txt
```

### 4.2 修改配置

编辑 `monitor.py` 中的应用凭据：

```python
APP_ID = "<你的 App ID>"
APP_SECRET = "<你的 App Secret>"
```

### 4.3 初始化 state.json

首次运行前，确认 `state.json` 内容如下（或删除该文件让程序自动创建）：

```json
{
  "chat_id": "",
  "user_open_id": "",
  "current_mode": "agent",
  "current_model": "claude-4.6-opus",
  "last_processed_ts": "",
  "pending_messages": [],
  "context_files": [],
  "conversation_history": [],
  "open_new_composer": true
}
```

### 4.4 启动监控

```bash
python monitor.py
```

看到以下日志说明启动成功：

```
Starting Feishu Bridge (WebSocket long connection)...
Mode: agent | Model: claude-4.6-opus
Connecting to Feishu WebSocket...
```

> **提示**：建议将 monitor.py 配置为开机自启或使用后台运行方式，确保持续监听。

### 4.5 验证

1. 确保 Cursor IDE 已打开并且 Composer 可用
2. 在飞书中向机器人发送一条消息，如 "你好"
3. 观察：
   - monitor.py 控制台显示收到消息
   - Cursor 自动打开 Composer 并输入消息
   - AI Agent 处理后回复出现在飞书中

## 日常使用

### 基本对话

直接在飞书中向机器人发送文本消息即可。消息会被转发到 Cursor Agent，处理结果自动回复到飞书。

### 切换模式

```
/mode agent    → Agent 模式：AI 自主执行，可读写文件、运行命令
/mode ask      → Ask 模式：只读分析，不做任何修改
/mode plan     → Plan 模式：先生成计划，确认后再执行
/mode debug    → Debug 模式：专注排查和修复问题
```

### 切换模型

```
/model claude-4.6-opus   → 切换到 Claude 4.6 Opus
/model gpt-5.4           → 切换到 GPT-5.4
```

### 新建对话

```
/new    → 清除上下文，保留模式和模型设置，下条消息在新 Composer 中打开
/clear  → 完全重置：清除所有状态，恢复默认模式和模型
```

### 添加文件上下文

```
/context src/utils.py     → 让 AI Agent 在处理时额外读取该文件
```

## 飞书指令参考

| 指令 | 参数 | 说明 |
|------|------|------|
| `/mode` | `agent` / `ask` / `plan` / `debug` | 切换交互模式 |
| `/model` | 模型名称 | 切换 AI 模型 |
| `/new` | 无 | 新建对话（保留设置） |
| `/clear` | 无 | 完全重置 |
| `/status` | 无 | 查看当前状态 |
| `/help` | 无 | 显示帮助信息 |
| `/context` | 文件路径 | 添加文件到上下文 |

## 工作模式说明

### Agent 模式（默认）

AI Agent 以完全自主的方式工作，可以：
- 读取和修改文件
- 运行终端命令
- 安装依赖
- 发送邮件
- 执行任何编码任务

适合日常开发任务和自动化操作。

### Ask 模式

只读模式，AI 只进行分析和回答，不会修改任何文件或运行命令。

适合代码审查、问题咨询、方案讨论。

### Plan 模式

AI 先生成执行计划发回飞书，等你确认后再执行。

1. 发送任务描述
2. AI 回复执行计划
3. 回复 "执行" 或 "确认" 开始执行

适合复杂任务、需要审核的操作。

### Debug 模式

AI 聚焦于问题排查和修复，会优先分析错误原因，给出修复方案并执行。

适合 Bug 修复、错误排查。

## 常见问题

### Q: 飞书机器人没有消息输入框？

确认已配置事件订阅（`im.message.receive_v1`），且订阅方式为「长连接」。配置完成后需创建新版本并发布。

### Q: 消息发到飞书但 Cursor 没反应？

1. 检查 monitor.py 是否在运行，日志中是否显示收到消息
2. 确认 Cursor IDE 窗口已打开
3. 确认 Cursor 标题栏包含 "Cursor" 字样（用于窗口查找）

### Q: Cursor 打开了但没有发送消息？

键盘自动化可能受到输入法干扰。确保：
- Windows 默认输入法可正常切换
- PowerShell 的 `Set-Clipboard` 命令可用

### Q: AI 回复没有出现在飞书中？

1. 检查 `.cursor/mcp.json` 中 `feishu_bridge` 配置是否正确
2. 确认 Cursor MCP 工具列表中有飞书相关工具
3. 检查 `.cursor/rules/feishu-bridge.mdc` 规则文件是否存在且配置正确

### Q: 收到重复回复？

检查是否有多个 monitor.py 进程在运行：

```bash
# Windows
tasklist | findstr python
# 如有多个，杀掉多余的
taskkill /F /PID <pid>
```

### Q: 如何后台运行 monitor.py？

可以使用以下方式：

```bash
# 方式一：使用 pythonw（无窗口）
pythonw monitor.py

# 方式二：使用 nohup（如果有 Git Bash）
nohup python monitor.py &

# 方式三：注册为 Windows 服务（推荐生产环境）
```

---

如有其他问题，请在 [GitHub Issues](https://github.com/hanjianhua44/feishu-cursor-bridge/issues) 中反馈。
