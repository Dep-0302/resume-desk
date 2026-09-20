# 一个恢复核心，一个桌面入口

```text
Codex 本地记录 / 已核实的 App 导入
                 │
          recovery.py（01A）
                 │
        回顾报告与共享恢复状态
                 │
      panel-read / panel-action
                 │
       经典桌面浮窗（01B）
```

`Recovery/recovery.py` 负责扫描、证据指纹、反馈、提醒和队列，使用文件锁及原子写入保护状态。浮窗源码位于 `Recovery/FloatingPanel/Sources/`，只经后端接口读写。

唯一构建产物是 `断点复原浮窗.app`，包内带同一份 `Resources/Recovery/recovery.py`。默认状态目录为用户的 `Application Support/ResumeDesk/Recovery`，也可通过 `--state-dir` 指定。不会从开发目录自动复制或覆盖个人状态。

## 状态约束

- 接续内容绑定原会话与证据指纹，过期操作被拒绝。
- 已核对的队列不限条数；某次报告缺少旧条目，不等于已处理。
- 接手表示本轮收起，忽略表示按对话持续排除；两者均不等于业务完成。
- 生成文件、实际呈现、用户处理和用户验收分别记录。
- 不读取或写入未完成独立 App 的摘要状态、来源配置或收件箱。

Git 仓库和发布包只管理这套经典浮窗。运行数据与未发布研发材料应保留在仓库外。
