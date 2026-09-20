# 断点接续 · 经典浮窗

按项目找回原任务和停点，在桌面的“待接续任务”浮窗中继续。

**本仓库只发布经典浮窗及其必需的回顾与提醒后端。** 01A 提供恢复核心，01B 提供桌面显示与操作，构成同一套功能。未完成的独立 App 不属于本仓库的构建或发布范围。

当前版本为 **v0.1.1 源码预览版**，预构建 App 下载包尚未提供。需要 macOS 14+、Apple Swift 命令行工具，以及其提供的 `/usr/bin/python3`。没有第三方运行时包。

## 功能

- 按项目展开已核对的待接续任务，保留原对话名称和具体停点。
- 悬停展示“上次在做／当前停点／建议下一步”，长内容可滚动。
- 点击正文返回原任务：Codex 使用任务深链，已导入的 ChatGPT 记录使用原网页。
- 勾选表示接手并暂时收起；叉号经确认后停止关注该对话，均不改写原平台任务状态。
- 顶栏可拖动，拖动时变淡，位置自动保留；随 Codex 运行显示。
- 01A 保留候选扫描、证据核对、两列表格报告、提醒节奏与实际呈现回执。

浮窗只通过 `panel-read` / `panel-action` 操作回顾队列，不调用模型，不自行扫描聊天或生成摘要。未经核对的历史不会自动成为待办。

## 构建与使用

从 [Releases](https://github.com/Dep-0302/resume-desk/releases) 下载源码，然后运行：

```sh
zsh build.sh
open 'dist/断点复原浮窗.app'
```

唯一 App 产物为 `dist/断点复原浮窗.app`，内含恢复后端，可离开仓库运行。默认数据目录为 `~/Library/Application Support/ResumeDesk/Recovery`；已有回顾数据可通过参数指定：

```sh
'dist/断点复原浮窗.app/Contents/MacOS/FloatingRecoveryPanel' --state-dir '/path/to/recovery-data'
```

退出入口在菜单栏图标中。程序不会安装登录项、每日任务或后台服务。首次没有经过核对的回顾时，列表为空。

## 回顾与提醒

由你已有的 Agent 读取候选、核对真实停点，再导入并呈现回顾。程序不内置模型、API Key 或额外付费服务。

```sh
python3 Recovery/recovery.py scan
python3 Recovery/recovery.py import-app reviewed.json
python3 Recovery/recovery.py report --mode manual
python3 Recovery/recovery.py panel-read
```

所有命令可前置 `--state-dir PATH`。进度询问后等待两天首次提醒，后续从实际呈现起每七天提醒；明确暂放、不做、已处理或忽略后停止相应提醒。定时执行由使用者自行安排。[运行规则与接口](docs/RECOVERY.md)。

## 检查

```sh
zsh scripts/check.sh
zsh scripts/check-native.sh  # 需要 macOS 图形会话，仅使用合成窗口
```

包括 Python 核心回归、搬移后的随包后端检查、拖动、悬停和布局回归。发布检查要求源码处于允许范围内，且 `dist/` 中只有经典浮窗一个 App。

源码中的 `scripts/seal-preview.sh` 仅用于新构建产物的 ad-hoc 完整性签名，随后可运行 `scripts/package.sh`。它不提供开发者身份认证或 Apple 公证；默认 CI 只执行检查，签名与打包须手动触发。

## 文件

| 路径 | 用途 |
|---|---|
| `Recovery/recovery.py` | 01A 恢复核心，共享状态的唯一写入者 |
| `Recovery/FloatingPanel/` | 01B 经典浮窗、图标与原生测试 |
| `Recovery/test_*.py` | 合成数据回归 |
| `scripts/` | 构建检查、发布范围检查及可选打包 |
| `docs/` | 架构、恢复流程和验证说明 |

个人聊天、账户设置、运行数据及未完成产品源码不进入当前发布树。旧 `v0.1.0` 混合发布已撤回，`v0.1.1` 更正发布范围。[版本记录](CHANGELOG.md) · [验证说明](docs/VERIFICATION.md) · [声明](NOTICE.md)
