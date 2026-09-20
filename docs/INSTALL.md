# 安装与使用

**推荐把 [README 中的一段话](../README.md#交给-agent-安装) 复制给 Codex。** Agent 按 [INSTALL_AGENT.md](../INSTALL_AGENT.md) 完成安装和首次回顾，无需用户手工编译或填写 JSON。

## 环境与数据

- macOS 14+、Codex 桌面版、Apple 命令行工具中的 Swift 与 `/usr/bin/python3`。缺项由 Agent 检查并说明；当前没有自带运行环境或签名／公证的 DMG。
- 默认安装到 `~/Library/Application Support/ResumeDesk/releases/版本号/`，激活回执为安装根下的 `install.json`；数据独立保存在 `Recovery/`。
- 只读本机 Codex 索引与会话记录，交由你当前的 Codex Agent 核对；不需要额外 API Key。内容处理与额度遵循你的 Codex 账号设置。
- 中间核对文件留在数据目录的 `AgentReview/`，不上传到本仓库。它们含本机对话节选，请勿公开分享。

## 打开与刷新

保持 Codex 桌面版运行。安装后 Agent 会给出 App 和已安装控制器的实际路径。默认可以让 Agent 运行：

```sh
python3 "$HOME/Library/Application Support/ResumeDesk/releases/0.2.0/agent.py" launch
```

刷新时让 Agent 按安装目录中的 `INSTALL_AGENT.md` 执行 prepare → 核对 → apply → verify → launch。**打开 App 不会自动完成一次新回顾**；不默认创建定时任务或登录项。自定义安装／数据目录须沿用安装时的选项。

| 情况 | 操作 |
|---|---|
| 空列表 | 让 Agent 区分尚未接入、核对未完成与确实无待办 |
| 部分记录无法核对 | Agent 说明缺口后可先打开已核实条目，未核对部分不冒充无待办 |
| 出现“隔离任务” | 退出 QA 窗口，核对 App 路径与数据目录，不删除真实状态 |
| 没有窗口 | 保持 Codex 运行，检查 launch 的 display_verified 与具体原因 |
| 核对后提示来源变化 | 重新 prepare 并核对，不能改指纹绕过校验 |
| 缺工具／系统拦截 | 按明确缺项处理；系统拦截时在“系统设置 → 隐私与安全性”手动决定是否放行，不关闭保护 |

勾选仅表示接手；忽略经确认后持续排除。退出使用菜单栏图标。

## 更新与卸载

让 Agent 从新版源码重新 install；新版保存在独立版本目录，已有数据不覆盖。相同版本重复安装不覆盖 App。运行中的旧浮窗应先从菜单栏退出，再启动新版。

卸载时先退出浮窗，再让 Agent 列出安装目录供你确认。删除版本目录即可移除程序；`Recovery/` 是个人回顾数据，只有明确要求才删除。当前不写登录项或后台服务。
