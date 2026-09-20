# 断点接续 · 经典浮窗

面向 **macOS＋Codex 桌面版**，按项目找回待接续任务，查看停点，点击返回原对话。

## 交给 Agent 安装

把下面这段复制给 Codex，剩下的由 Agent 完成：

> 请安装 https://github.com/Dep-0302/resume-desk ，按仓库 INSTALL_AGENT.md 检查环境、安装经典浮窗，并只读接入我本机的 Codex 记录。逐条核对真实停点，验证后打开浮窗；不要生成演示任务，保留已有数据，告诉我实际结果和缺项。

Agent 会完成 **检查 → 安装 → 核对记录 → 导入 → 验证 → 打开**。用户只处理必要授权和环境缺项，不需要手工编译或填写 JSON。

**[Agent 操作入口](INSTALL_AGENT.md) · [安装与使用](docs/INSTALL.md) · [最新 Release](https://github.com/Dep-0302/resume-desk/releases/tag/v0.2.0)**

> [!IMPORTANT]
> **v0.2.0 是 Agent 辅助安装版。** 需要 macOS 14+、Codex 桌面版及 Apple 命令行工具（Swift、`/usr/bin/python3`）。暂未提供签名／公证的 App 或 DMG；安装工具不会绕过系统保护。核对使用你的 Codex 账号和额度。

## 使用

- 按项目查看任务；悬停看“上次／停点／下一步”，点击返回原对话。
- 勾选表示本轮接手，叉号经确认后停止关注；均不代表业务已完成。
- 后续对 Agent 说“刷新断点接续，核对我的 Codex 记录”，按已安装目录中的说明再次核对。

正常首次使用不会出现假任务。**“隔离任务”或重复测试文案属于 QA 窗口**，不能作为已连接自己记录的证据。没有待办可以为空；读取失败或核对不完整会明确报出，已核实条目可以在说明缺口后先使用。

默认按版本安装到 `~/Library/Application Support/ResumeDesk/releases/`，回顾数据在同目录的 `Recovery/`。只读源记录，不改原对话；不自动设置登录项、定时任务或安装全局 Skill。完整流程已用隔离的 Codex 格式记录验证，干净 Mac 与真实使用反馈仍需持续验证。

[回顾规则](docs/RECOVERY.md) · [开发与测试](docs/DEVELOPMENT.md) · [验证范围](docs/VERIFICATION.md) · [后续计划](docs/ROADMAP.md) · [更新记录](CHANGELOG.md)
