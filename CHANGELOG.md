# Changelog

## 0.2.0 — 2026-09-20

Agent-assisted installation for macOS + Codex desktop.

- Add a concise copyable installation prompt, repository-local Skill and deterministic install/review controller.
- Install versioned classic App bundles while preserving recovery data and a reusable installed controller.
- Export bounded local Codex evidence for Agent review; validate complete batches, source freshness and attention decisions before atomic import.
- Separate installation, review readiness and native display verification; never substitute QA data for user records.
- Add isolated installer, protocol and source-to-panel regression coverage.

Requires Apple command-line tools. No signed/notarized binary, bundled runtime, global Skill or automatic scheduler is provided.

## 0.1.2 — 2026-09-20

Documentation-only source preview; application behavior is unchanged.

- Make the source-preview status and missing installer/Agent integration explicit on the homepage.
- Add concise current trial instructions, a manual Codex handoff prompt, and a clearly labeled future roadmap.
- Separate QA/developer commands from installation, and explain test data versus real recovery.
- Keep the classic panel plus required backend as the only product in this repository.

## 0.1.1 — 2026-09-20

Correct the release scope to the classic floating panel (01B) and required recovery/reminder backend (01A).

- Remove the unfinished standalone App, its separate core, CLI, assets, Swift package and tests from the current Git tree.
- Build, validate and optionally package exactly one App: 断点复原浮窗.app.
- Preserve the recovery backend, evidence/attention contracts, native drag and hover behavior, and existing regression coverage.
- Check the relocated classic App's bundled backend directly; reject source files and App bundles outside the release scope.
- Withdraw the mixed v0.1.0 release. Existing Git commit history remains an audit trail; no history rewrite is performed.

This is a source preview. No prebuilt App asset, Developer ID signature or Apple notarization is included.
