# Changelog

## 0.1.1 — 2026-09-20

Correct the release scope to the classic floating panel (01B) and required recovery/reminder backend (01A).

- Remove the unfinished standalone App, its separate core, CLI, assets, Swift package and tests from the current Git tree.
- Build, validate and optionally package exactly one App: 断点复原浮窗.app.
- Preserve the recovery backend, evidence/attention contracts, native drag and hover behavior, and existing regression coverage.
- Check the relocated classic App's bundled backend directly; reject source files and App bundles outside the release scope.
- Withdraw the mixed v0.1.0 release. Existing Git commit history remains an audit trail; no history rewrite is performed.

This is a source preview. No prebuilt App asset, Developer ID signature or Apple notarization is included.
