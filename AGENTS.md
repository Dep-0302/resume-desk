# ResumeDesk — classic floating panel only

Read README.md and docs/ARCHITECTURE.md first.

- Release scope is the classic “待接续任务” floating panel (01B) and its required recovery/reminder backend (01A).
- Keep recovery logic in Recovery/recovery.py and UI code in Recovery/FloatingPanel. Do not add the unfinished standalone App, its separate core/CLI, assets, Swift package or unrelated source adapters.
- recovery.py is the only writer of shared state.json. The panel uses panel-read/panel-action and never writes that state directly.
- Preserve user attention decisions: taken is not business completion; dismissed stays excluded. Never infer receipt or completion from silence.
- Installation and first-use tasks follow INSTALL_AGENT.md; use scripts/agent.py and the repository-local .agents/skills/resume-desk/SKILL.md. Do not run QA fixtures or GUI regressions as a substitute for connecting the user's real records.
- Tests use temporary synthetic data, never personal queues.
- Keep shipped capabilities separate from docs/ROADMAP.md. The Agent installer and repository-local Skill exist. Bundled runtime, global Skill/plugin installation, setup wizard and signed/notarized downloads remain future work.
- Do not commit conversations, runtime directories, credentials, account settings, personal paths or task IDs.
- Build only dist/断点复原浮窗.app. Do not alter installed apps, startup items, system permissions or security settings.
- Run zsh scripts/check.sh and the GUI regressions before release. Keep checks scoped to this one product.
- Report code, tests, publication and user acceptance separately.
