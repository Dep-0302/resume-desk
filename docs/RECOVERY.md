# 回顾与提醒运行方法

这是 01A 的可移植执行入口，搭配 01B 经典浮窗使用。它不包含原开发者的私人任务、固定定时器或账号设置。

## 每次执行

1. `scan` 只读本机 Codex 索引和有界原文。来源覆盖不足时保留缺口，需要时由使用者的 Agent 补充其可访问的 App 记录。
2. 核对候选及已有队列的真实停点。检查原请求、助手回答、实际改动与是否仍需用户处理；不能只看最近一句“已完成”。普通已答问答、可选收尾建议不自动成为待办。
3. 用 `import-app` 写入已核实元数据、回顾点与 `attention_assessments`；使用当前指纹、原会话 ID 和可匹配的原文证据。缺失事实保留未知。
4. `report --mode manual` 生成按项目分组的两列表格；定时工作用 `--mode scheduled`，遵守到期和静默规则。符合条件的条目全部保留，无默认数量上限。
5. 实际向使用者呈现完整报告后，再使用该次返回的 token 执行 `ack`；若下次补登记，应带真实 `--presented-at`。只生成文件不能登记已呈现。
6. 经典浮窗通过 `panel-read` 读取已核对队列。用户主动操作使用 `panel-action --id … --action take|dismiss --token …`。

所有命令都支持前置 `--state-dir PATH`，不指定则使用用户的 `Application Support/ResumeDesk/Recovery`。

```sh
python3 Recovery/recovery.py scan --home "$HOME/.codex"
python3 Recovery/recovery.py import-app reviewed.json
python3 Recovery/recovery.py report --mode manual
python3 Recovery/recovery.py panel-read
```

`reviewed.json` 是 Agent 核对后的接口输入，不能直接把原始扫描结果当作已经审核的结论。数据结构与真实验证例子见 `Recovery/test_recovery.py`、`Recovery/test_panel.py` 和 `Recovery/test_omission_recovery.py`。

## 不丢事项，也不制造待办

- 核对整段事项是否闭合。某一步已完成，不足以排除同一事项里仍待决定、授权或验证的部分。
- 复用旧排除判断前，重新核对当前证据及未完成部分。证据不足时保留未知，不能只因指纹相同就自动沿用旧排除。
- 进度询问后满两天首次提醒；从实际呈现起每满七天续提醒。暂放、不做、已处理等明确反馈停止对应提醒；普通沉默不算用户接受。
- `take` 表示已接手，本轮收起，不写成业务完成；之后需新的明确待处理证据才能恢复。`dismiss` 按会话身份持续排除，普通导入不能复活。
- 显式用户偏好通过 `preferences` 保存。例如归档与对话列表整理的排除，应由使用者选择，不搬用原开发者的个人偏好。
- `resume_context` 的 `previous_focus/current_state/next_step` 分别表示上次目标、当前停点与建议；每项绑定同一会话及当前指纹。

## 呈现

每个项目一张两列表格，左列保留原任务标题与打开入口，右列写具体停点与回忆线索。一条任务一行。不要把多个对话压成项目总评，也不要让使用者在另一组按钮中找对应入口。

浮窗展示同一份内容。ChatGPT 记录打开原网页，Codex 打开本地任务；不能仅因两种记录都采用 UUID 就混用深链。

## 定时与迁移

本项目提供运行能力，不安装定时器。可在已有 Agent 中安排上述流程，时间由使用者决定；无有意义变化或未到提醒时间时保持安静。

已有队列继续通过原 `recovery.py` 和显式状态目录连接。先核对版本和已有反馈再决定是否迁移；该公开首版不自动读取、复制、覆盖原开发目录的个人状态。
