# 发布验证范围

| 入口 | 核对内容 |
|---|---|
| `scripts/check.sh` | 后端与安装器回归、唯一浮窗构建、搬移后的随包后端、Agent 安装与核对全链路、发布范围检查 |
| `scripts/check-agent-flow.py` | 从临时 Codex 格式 SQLite／JSONL 开始，使用已安装控制器完成导出、核对、导入与验证，检查源文件未变 |
| `scripts/check-native.sh` | 20 项拖动事件回放、10 类悬停／导航检查、两轮布局与位置恢复 |
| `scripts/check-release.py` | 允许的源码范围、唯一 App、版本及 macOS 14 目标、随包后端一致、排除个人路径与运行状态 |

测试只用隔离合成来源，不能据此宣称已接入测试者的真实记录。`source_kind=custom_codex` 标明自定义来源；真实安装回执必须列明来源目录与覆盖缺口。

`verify` 检查后端核对是否完成；`launch_requested` 仅表示已请求打开。只有本次新 diagnostics 同时证明正常模式、窗口可见、后端已加载且 revision／App 版本一致，才报告 `display_verified=true`。

这些检查不等于干净 Mac 验收、签名／公证或用户已处理业务事项。实际鼠标体验、全屏／跨桌面及使用效果仍需使用者验证。每次提交的线上结果以 GitHub Actions 为准。
