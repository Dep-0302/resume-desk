"""Isolated tests for the Agent-installed classic floating panel.

They use a synthetic embedded backend and directories below one TemporaryDirectory;
no Codex database, personal state directory, app launch, or source checkout data is
read during these tests.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
AGENT_PATH = REPO / "scripts" / "agent.py"
SPEC = importlib.util.spec_from_file_location("resume_desk_agent", AGENT_PATH)
assert SPEC and SPEC.loader
agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = agent
SPEC.loader.exec_module(agent)


BACKEND = r'''#!/usr/bin/env python3
import json
from pathlib import Path
import sys

args = sys.argv[1:]
state = Path(args[args.index("--state-dir") + 1])
command = next(value for value in args if value in ("agent-export", "agent-apply", "agent-status", "panel-read"))
state.mkdir(parents=True, exist_ok=True)
coverage = {"indexed": 1, "read_this_pass": 1, "synthetic": True}
item = {"id": "11111111-1111-4111-8111-111111111111", "fingerprint": "fingerprint-1", "title": "synthetic", "project": "QA", "evidence": {"question": "synthetic question"}}
unknown_item = {"id": "22222222-2222-4222-8222-222222222222", "fingerprint": "fingerprint-2", "title": "synthetic unknown", "project": "QA", "evidence": {"question": "synthetic unknown question"}}
if command == "agent-export":
    if "--allow-read" not in args:
        raise SystemExit(2)
    print(json.dumps({"schema_version": 1, "batch_token": "batch-synthetic", "coverage": coverage, "items": [item, unknown_item]}))
elif command == "agent-apply":
    if "--allow-read" not in args:
        raise SystemExit(2)
    response = json.loads(Path(args[args.index("agent-apply") + 1]).read_text())
    unknown = sum(1 for decision in response["decisions"] if decision["decision"] == "unknown")
    needs_user = sum(1 for decision in response["decisions"] if decision["decision"] == "needs_user")
    if unknown:
        (state / "partial").write_text(json.dumps(response))
    else:
        (state / "ready").write_text(json.dumps(response))
    print(json.dumps({"schema_version": 1, "batch_token": response["batch_token"], "applied": {"needs_user": needs_user, "no_action": 0, "unknown": unknown}, "coverage": coverage, "panel_count": 1, "status": "needs_attention" if unknown else "ready"}))
elif command == "agent-status":
    ready = (state / "ready").exists()
    partial = (state / "partial").exists()
    source_bad = (state / "bad-provenance").exists()
    count_mismatch = (state / "count-mismatch").exists()
    if partial:
        partial_response = json.loads((state / "partial").read_text())
        partial_needs = sum(1 for decision in partial_response["decisions"] if decision["decision"] == "needs_user")
    else:
        partial_needs = 0
    stage = "ready" if ready else ("needs_attention" if partial else "awaiting_review")
    review = ({"batch_token": "batch-synthetic", "applied_at": "2026-01-01T00:00:00Z", "reviewed_count": 2, "coverage_complete": not partial, "unknown_count": 1 if partial else 0, "source_home": "/tmp/synthetic-codex", "source_kind": "custom_codex"} if (ready or partial) else None)
    source_home = None if source_bad else "/tmp/synthetic-codex"
    source_kind = None if source_bad else "custom_codex"
    panel_count = 1 if ready else partial_needs
    print(json.dumps({"schema_version": 1, "stage": stage, "coverage": dict(coverage, gaps=[{"code": "synthetic_gap"}] if partial else []), "reviewed_count": 2 if (ready or partial) else 0, "panel_count": panel_count + (1 if count_mismatch else 0), "has_pending_items": bool(panel_count), "last_review": review, "next_step": "synthetic", "source_home": source_home, "source_kind": source_kind}))
else:
    if (state / "partial").exists():
        partial_response = json.loads((state / "partial").read_text())
        displayable = any(decision["decision"] == "needs_user" for decision in partial_response["decisions"])
    else:
        displayable = (state / "ready").exists()
    print(json.dumps({"schema_version": 1, "revision": "panel-revision-1", "projects": [{"items": [item]}] if displayable else [], "notice": "synthetic"}))
'''


class AgentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="resume-desk-agent-test-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source clone"
        self.install_root = self.root / "installation with spaces"
        self.state_dir = self.root / "state with spaces"
        self.codex_home = self.root / "fake codex home"
        self.codex_home.mkdir()
        (self.source / "Recovery").mkdir(parents=True)
        (self.source / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (self.source / "build.sh").write_text("#!/bin/zsh\n", encoding="utf-8")
        (self.source / "marker").write_text("first", encoding="utf-8")
        (self.source / "INSTALL_AGENT.md").write_text("synthetic installed guide\n", encoding="utf-8")
        (self.source / "docs").mkdir()
        (self.source / "docs" / "AGENT_PROTOCOL.md").write_text("synthetic protocol\n", encoding="utf-8")
        (self.source / "Recovery" / "recovery.py").write_text(
            "# agent-export\n# agent-apply\n# agent-status\n", encoding="utf-8"
        )
        self.prerequisites = mock.patch.object(agent, "build_prerequisites", return_value={}).start()
        self.python = mock.patch.object(agent, "SYSTEM_PYTHON", Path(sys.executable)).start()
        self.build = mock.patch.object(agent, "run_build", self.fake_build).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.temporary.cleanup)

    @staticmethod
    def fake_build(build_root):
        app = build_root / "dist" / agent.APP_NAME
        backend = app / agent.APP_BACKEND
        backend.parent.mkdir(parents=True)
        backend.write_text(BACKEND, encoding="utf-8")
        executable = app / "Contents" / "MacOS" / "FloatingRecoveryPanel"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text((build_root / "marker").read_text(encoding="utf-8"), encoding="utf-8")
        executable.chmod(0o755)

    def install(self):
        return agent.install_release(self.source, self.install_root, str(self.state_dir))

    def test_same_version_is_idempotent_and_never_overwrites_different_content(self):
        first = self.install()
        self.assertTrue(first["installed"])
        second = self.install()
        self.assertFalse(second["installed"])
        self.assertEqual(first["release"], second["release"])

        (self.source / "marker").write_text("changed", encoding="utf-8")
        with self.assertRaises(agent.VersionConflict):
            self.install()

    def test_versions_are_isolated_and_existing_state_is_preserved(self):
        self.state_dir.mkdir(parents=True)
        original = b'{"user_state":"preserve"}\n'
        (self.state_dir / "state.json").write_bytes(original)
        first = self.install()
        (self.source / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        (self.source / "marker").write_text("second", encoding="utf-8")
        second = self.install()

        self.assertTrue((Path(first["release"]) / agent.APP_NAME).is_dir())
        self.assertTrue((Path(second["release"]) / agent.APP_NAME).is_dir())
        self.assertTrue((Path(second["release"]) / "INSTALL_AGENT.md").is_file())
        self.assertTrue((Path(second["release"]) / "docs" / "AGENT_PROTOCOL.md").is_file())
        self.assertEqual((self.state_dir / "state.json").read_bytes(), original)
        config = json.loads((self.install_root / agent.RECEIPT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(config["active_version"], "2.0.0")

    def test_failed_activation_keeps_prior_release_usable(self):
        first = self.install()
        (self.source / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        (self.source / "marker").write_text("second", encoding="utf-8")
        original_write = agent.atomic_write_json

        def fail_activation(path, payload, mode=0o600):
            if path.name == agent.RECEIPT_NAME:
                raise agent.PermissionDenied("synthetic receipt failure")
            return original_write(path, payload, mode=mode)

        with mock.patch.object(agent, "atomic_write_json", fail_activation):
            with self.assertRaises(agent.PermissionDenied):
                self.install()
        current = agent.load_installation(self.install_root)
        self.assertEqual(current.version, "1.0.0")
        self.assertEqual(current.release, Path(first["release"]))

    def test_unprepared_install_is_not_ready(self):
        self.install()
        result = agent.verify_installation(self.install_root, str(self.state_dir))
        self.assertFalse(result["ready"])
        self.assertTrue(any(error["code"] == "not_ready" for error in result["errors"]))

    def test_prepare_requires_permission_and_template_starts_unknown(self):
        self.install()
        installation = agent.load_installation(self.install_root)
        with self.assertRaises(agent.PermissionDenied):
            agent.prepare(installation, self.state_dir, self.codex_home, False, [])

        result = agent.prepare(installation, self.state_dir, self.codex_home, True, [])
        self.assertEqual(result["packet_path"], result["packet"])
        self.assertEqual(result["response_path"], result["response_template"])
        template_path = Path(result["response_path"])
        template = json.loads(template_path.read_text(encoding="utf-8"))
        self.assertEqual(template["decisions"][0]["decision"], "unknown")
        self.assertEqual(template["decisions"][0]["evidence_quote"], "")
        self.assertEqual(stat.S_IMODE(template_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(result["packet_path"]).stat().st_mode), 0o600)

    def test_apply_then_verify_and_launch_preserve_arguments_with_spaces(self):
        self.install()
        installation = agent.load_installation(self.install_root)
        prepared = agent.prepare(installation, self.state_dir, self.codex_home, True, [])
        response_path = Path(prepared["response_path"])
        response = json.loads(response_path.read_text(encoding="utf-8"))
        for number, decision in enumerate(response["decisions"]):
            decision.update({
                "decision": "needs_user",
                "reason": "synthetic reason",
                "evidence_quote": "synthetic question" if number == 0 else "synthetic unknown question",
                "review_point": "synthetic review point",
            })
        response_path.write_text(json.dumps(response), encoding="utf-8")
        agent.apply_review(installation, self.state_dir, response_path, True)
        verified = agent.verify_installation(self.install_root, str(self.state_dir))
        self.assertTrue(verified["ready"])

        observed = {}
        def fake_open(command):
            observed["command"] = command
            diagnostic = Path(command[-1])
            diagnostic.write_text(json.dumps({
                "test_mode": False,
                "backend_loaded": True,
                "visible": True,
                "backend_revision": "panel-revision-1",
                "app_version": "1.0.0",
                "process_id": 4242,
            }), encoding="utf-8")

        with mock.patch.object(agent, "run_open", fake_open):
            launched = agent.launch(installation, self.state_dir, "com.openai.codex", None, 0)
        self.assertTrue(launched["launch_requested"])
        self.assertTrue(launched["display_verified"])
        self.assertEqual(launched["process_id"], 4242)
        self.assertEqual(observed["command"][0:3], ["/usr/bin/open", "-n", str(installation.app)])
        self.assertIn(str(self.state_dir), observed["command"])

    def test_allow_partial_requires_flag_and_reports_partial_review(self):
        self.install()
        installation = agent.load_installation(self.install_root)
        prepared = agent.prepare(installation, self.state_dir, self.codex_home, True, [])
        response_path = Path(prepared["response_path"])
        response = json.loads(response_path.read_text(encoding="utf-8"))
        response["decisions"][0].update({
            "decision": "needs_user", "reason": "synthetic reason",
            "evidence_quote": "synthetic question", "review_point": "synthetic review point",
        })
        response_path.write_text(json.dumps(response), encoding="utf-8")
        agent.apply_review(installation, self.state_dir, response_path, True)
        verified = agent.verify_installation(self.install_root, str(self.state_dir))
        self.assertFalse(verified["ready"])
        self.assertTrue(verified["partial_eligible"])
        self.assertEqual(verified["panel"]["item_count"], 1)
        with self.assertRaises(agent.NotReady):
            agent.launch(installation, self.state_dir, "com.openai.codex", None, 0)

        def fake_open(command):
            Path(command[-1]).write_text(json.dumps({
                "test_mode": False, "backend_loaded": True, "visible": True,
                "backend_revision": "panel-revision-1", "app_version": "1.0.0", "process_id": 4343,
            }), encoding="utf-8")

        with mock.patch.object(agent, "run_open", fake_open):
            launched = agent.launch(installation, self.state_dir, "com.openai.codex", None, 0, True)
        self.assertFalse(launched["review_ready"])
        self.assertTrue(launched["partial_review"])
        self.assertIsInstance(launched["coverage"], dict)
        self.assertIn("unknown", launched["partial_note"])

    def test_allow_partial_rejects_empty_unknown_batch_and_bad_source(self):
        self.install()
        installation = agent.load_installation(self.install_root)
        prepared = agent.prepare(installation, self.state_dir, self.codex_home, True, [])
        response_path = Path(prepared["response_path"])
        agent.apply_review(installation, self.state_dir, response_path, True)
        all_unknown = agent.verify_installation(self.install_root, str(self.state_dir))
        self.assertFalse(all_unknown["partial_eligible"])
        self.assertEqual(all_unknown["panel"]["item_count"], 0)
        with self.assertRaises(agent.NotReady):
            agent.launch(installation, self.state_dir, "com.openai.codex", None, 0, True)

        response = json.loads(response_path.read_text(encoding="utf-8"))
        response["decisions"][0].update({
            "decision": "needs_user", "reason": "synthetic reason",
            "evidence_quote": "synthetic question", "review_point": "synthetic review point",
        })
        response_path.write_text(json.dumps(response), encoding="utf-8")
        # The synthetic backend accepts a second apply only to model a changed
        # current batch.  The real backend rejects stale or reused batches.
        agent.apply_review(installation, self.state_dir, response_path, True)
        (self.state_dir / "bad-provenance").write_text("1", encoding="utf-8")
        bad_source = agent.verify_installation(self.install_root, str(self.state_dir))
        self.assertFalse(bad_source["partial_eligible"])
        self.assertTrue(any(error["code"] == "source_provenance_missing" for error in bad_source["errors"]))
        with self.assertRaises(agent.NotReady):
            agent.launch(installation, self.state_dir, "com.openai.codex", None, 0, True)

    def test_panel_count_mismatch_never_reports_ready(self):
        self.install()
        installation = agent.load_installation(self.install_root)
        prepared = agent.prepare(installation, self.state_dir, self.codex_home, True, [])
        response_path = Path(prepared["response_path"])
        response = json.loads(response_path.read_text(encoding="utf-8"))
        for number, decision in enumerate(response["decisions"]):
            decision.update({
                "decision": "needs_user", "reason": "synthetic reason",
                "evidence_quote": "synthetic question" if number == 0 else "synthetic unknown question",
                "review_point": "synthetic review point",
            })
        response_path.write_text(json.dumps(response), encoding="utf-8")
        agent.apply_review(installation, self.state_dir, response_path, True)
        (self.state_dir / "count-mismatch").write_text("1", encoding="utf-8")
        verified = agent.verify_installation(self.install_root, str(self.state_dir))
        self.assertFalse(verified["ready"])
        self.assertTrue(any(error["code"] == "controller_error" for error in verified["errors"]))

    def test_installed_helper_works_after_source_clone_moves(self):
        installed = self.install()
        helper = Path(installed["installed_helper"])
        self.source.rename(self.root / "source moved")
        completed = subprocess.run(
            [sys.executable, "-B", str(helper), "prepare", "--allow-read",
             "--install-root", str(self.install_root), "--state-dir", str(self.state_dir),
             "--codex-home", str(self.codex_home)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("packet_path", payload)
        self.assertTrue(Path(payload["packet_path"]).is_file())

    def test_incompatible_source_refuses_installation(self):
        (self.source / "Recovery" / "recovery.py").write_text("print('legacy')\n", encoding="utf-8")
        with self.assertRaises(agent.IncompatibleSource):
            self.install()
        self.assertFalse(self.install_root.exists())

    def test_source_overlap_is_refused_before_build_or_write(self):
        with self.assertRaises(agent.ControllerError):
            agent.install_release(self.source, self.source / "nested install", str(self.state_dir))
        with self.assertRaises(agent.ControllerError):
            agent.install_release(self.source, self.install_root, str(self.source / "state"))
        self.assertFalse(self.install_root.exists())

    def test_doctor_database_check_reads_schema_without_exposing_thread_content(self):
        database = self.codex_home / "state_5.sqlite"
        columns = ", ".join(name + " TEXT" for name in sorted(agent.REQUIRED_CODEX_COLUMNS))
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE threads (" + columns + ")")
            connection.execute("INSERT INTO threads (id, title) VALUES (?, ?)", ("thread-id", "private title"))
            connection.commit()
        finally:
            connection.close()
        checked = agent.check_codex_database(self.codex_home)
        self.assertEqual(checked, {"ok": True, "schema": "threads"})
        self.assertNotIn("private title", json.dumps(checked))


if __name__ == "__main__":
    unittest.main()
