# Changelog

All notable changes to this project will be documented in this file.

## [v1.0.1] - 2026-03-09

### Fixed

- **剪贴板粘贴失效** — `_set_clipboard` 改回可靠的 PowerShell 单引号方式，修复 `ConvertFrom-Json` 引号解析导致剪贴板内容为空的问题
- **Composer 窗口复用失败** — `_trigger_open_new` 全局标记使用后未重置为 `False`，导致每条消息都打开新 Composer 窗口而非在现有窗口中追加
- **剪贴板恢复竞争** — 移除 paste 后立即恢复剪贴板的逻辑，避免在 Ctrl+V 完成前剪贴板内容被覆盖

### Changed

- 简化 `_clipboard_paste()` 实现，移除不稳定的保存/恢复机制

## [v1.0.0] - 2026-03-09

### Added

- **飞书 WebSocket 长连接** — 基于 `lark-oapi` SDK 实时接收飞书消息
- **自动触发 Cursor Composer** — 键盘自动化（窗口激活、剪贴板粘贴、发送）
- **多模式支持** — Agent / Ask / Plan / Debug 四种交互模式
- **模型切换** — 支持 claude-4.6-opus 和 gpt-5.4
- **飞书指令系统** — `/mode`, `/model`, `/new`, `/clear`, `/status`, `/help`, `/context`
- **Cursor Agent 规则** — `.mdc` 规则文件自动处理 `[飞书]` 前缀消息并回复
- **PID 锁** — 防止多个 monitor 实例同时运行
- **消息合并** — 防抖窗口内多条消息合并为一次触发
- **触发失败通知** — Cursor 窗口未找到时飞书端收到提醒
- **回复超时检测** — 120 秒内无回复自动提醒用户
- **状态原子写入** — state.json 先写 `.tmp` 再 `replace`，防止并发损坏
- **环境变量配置** — App ID / Secret 从 `.env` 文件读取，不泄露到代码

[v1.0.1]: https://github.com/hanjianhua44/feishu-cursor-bridge/compare/v1.0.0...v1.0.1
[v1.0.0]: https://github.com/hanjianhua44/feishu-cursor-bridge/releases/tag/v1.0.0