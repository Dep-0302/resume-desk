# 开发与验证

安装／首次试用请看 [INSTALL.md](INSTALL.md)。以下命令用于开发回归，不是用户数据接入步骤。

```sh
zsh scripts/check.sh
zsh scripts/check-native.sh
```

前者检查后端、构建和发布内容；后者需要 macOS 图形会话，会打开临时合成窗口。`Recovery/FloatingPanel/Tests/六项假数据准备.py` 仅用于制作 QA 数据，不属于正常启动流程。

发布源码范围：`Recovery/` 为现有后端与浮窗，`scripts/` 为构建／验证／打包，`docs/` 为说明。生产 App 不携带运行状态或测试任务。

可选预览包流程：

```sh
zsh scripts/seal-preview.sh
zsh scripts/package.sh
```

签名脚本只处理项目中新构建的 App。ad-hoc 签名不提供开发者身份认证，也不是 Apple 公证。默认 CI 只检查；预览封装须手动触发，Actions 产物不等于已发布的 Release 安装包。

[验证范围](VERIFICATION.md) · [接口与回顾规则](RECOVERY.md)
