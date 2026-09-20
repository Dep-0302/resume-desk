#!/usr/bin/env python3
"""Move the built app away from the repository and exercise the bundled recovery protocol."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Recovery'))
from recovery import report, merge_panel_report
from test_panel import fixture

def run(args, success=True):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, timeout=30)
    if success and result.returncode:
        raise AssertionError(result.stderr)
    if not success:
        assert result.returncode != 0, 'Stale action was accepted'
        return
    return json.loads(result.stdout)

with tempfile.TemporaryDirectory(prefix='resume-desk-integration-') as temp:
    root = Path(temp)
    app = root / 'Moved App' / '断点复原浮窗.app'
    shutil.copytree(ROOT / 'dist/断点复原浮窗.app', app)
    data = root / 'isolated-data'
    state_dir = data / 'Recovery'
    state_dir.mkdir(parents=True)
    state = fixture(3)
    merge_panel_report(state, report(state, 'manual'))
    state_file = state_dir / 'state.json'
    state_file.write_text(json.dumps(state))
    original_hash = hashlib.sha256(state_file.read_bytes()).hexdigest()
    backend = app / 'Contents/Resources/Recovery/recovery.py'
    command = [sys.executable, '-B', backend, '--state-dir', state_dir]
    view = run(command + ['panel-read'])
    assert sum(len(group['items']) for group in view['projects']) == 3
    assert hashlib.sha256(state_file.read_bytes()).hexdigest() == original_hash
    item = view['projects'][0]['items'][0]
    action = [sys.executable, '-B', backend, '--state-dir', state_dir, 'panel-action',
              '--id', item['id'], '--action', 'take', '--token', item['action_token']]
    run(action)
    view = run(command + ['panel-read'])
    remaining = [entry for group in view['projects'] for entry in group['items']]
    assert len(remaining) == 2
    assert item['id'] not in [entry['id'] for entry in remaining]
    run(action, success=False)
    persisted = json.loads(state_file.read_text())
    assert persisted['attention_controls'][item['id']]['mode'] == 'taken'
    assert persisted['threads'][item['id']].get('feedback', {}).get('status') != 'done'
print('PASS relocated classic panel → bundled recovery backend → shared state, read-only view, take and stale-token rejection')
