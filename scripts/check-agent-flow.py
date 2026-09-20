#!/usr/bin/env python3
"""Exercise the shipped installer and review protocol from source records, not a seeded queue."""
from pathlib import Path
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(command, succeeds=True):
    result = subprocess.run([str(value) for value in command], capture_output=True,
                            text=True, timeout=180)
    if succeeds and result.returncode:
        raise AssertionError(result.stderr + result.stdout)
    if not succeeds:
        assert result.returncode != 0, 'Incomplete review was incorrectly accepted as ready'
        return None
    return json.loads(result.stdout)


def source_records(home):
    sessions = home / 'sessions'
    sessions.mkdir(parents=True)
    cases = [
        ('路线选择', '请整理两条路线让我选，先不要预订。', '路线甲走海边，路线乙走山间，请你选择路线。'),
        ('普通问答', '2 加 2 等于几？', '2 加 2 等于 4。'),
        ('暂放事项', '这个事情先不做，也不用提醒了。', '已知悉，暂时保留材料。'),
    ]
    database = home / 'state_5.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT, cwd TEXT, '
                   'rollout_path TEXT, archived INTEGER, source TEXT, updated_at REAL, first_user_message TEXT)')
        for index, (title, question, answer) in enumerate(cases):
            ident = 'a1000000-0000-4000-8000-%012d' % index
            path = sessions / ('session-%d.jsonl' % index)
            records = [
                {'type': 'session_meta', 'payload': {'id': ident, 'cwd': '/fixture/001-project'}},
                {'type': 'event_msg', 'timestamp': '2026-09-19T10:00:00Z',
                 'payload': {'type': 'task_started', 'turn_id': 'test-turn-%d' % index}},
                {'type': 'event_msg', 'timestamp': '2026-09-19T10:01:00Z',
                 'payload': {'type': 'user_message', 'message': question}},
                {'type': 'event_msg', 'timestamp': '2026-09-19T10:02:00Z',
                 'payload': {'type': 'task_complete', 'last_agent_message': answer}},
            ]
            path.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records))
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                       (ident, title, '/fixture/001-project', str(path), 0, 'cli', 1789812120 + index, question))
    return [database] + sorted(sessions.glob('*.jsonl'))


def main():
    with tempfile.TemporaryDirectory(prefix='resume-desk-agent-flow-') as temp:
        root = Path(temp).resolve()
        source_home = root / 'Codex source'
        sources = source_records(source_home)
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        install_root = root / 'customer install'
        state = root / 'customer state'
        options = ['--install-root', install_root, '--state-dir', state, '--codex-home', source_home]
        original_helper = ROOT / 'scripts/agent.py'
        installed = run([sys.executable, original_helper, 'install', *options])
        helper = Path(installed['installed_helper'])
        assert helper.is_file() and helper.is_relative_to(install_root)
        # Every remaining operation uses only the installed helper and resources.
        command = [sys.executable, helper]
        run(command + ['verify', *options], succeeds=False)
        prepared = run(command + ['prepare', '--allow-read', *options])
        packet = json.loads(Path(prepared['packet_path']).read_text())
        response_path = Path(prepared['response_path'])
        response = json.loads(response_path.read_text())
        assert len(packet['items']) == 3
        assert all(row['decision'] == 'unknown' for row in response['decisions'])
        evidence = {row['id']: row for row in packet['items']}
        for decision in response['decisions']:
            item = evidence[decision['id']]
            if item['title'] == '路线选择':
                decision.update(decision='needs_user', reason='原答复明确等待用户选择路线。',
                                evidence_quote=item['evidence']['answer'],
                                review_point='两条路线已整理，停在选择路线。',
                                resume_context={'previous_focus': '比较两条路线',
                                                'current_state': '等待选择路线',
                                                'next_step': '回原对话选择一条路线'})
            else:
                decision.update(decision='no_action', reason='普通问题已有答案，或用户明确暂放。',
                                evidence_quote=item['evidence']['question'])
        response_path.write_text(json.dumps(response, ensure_ascii=False))
        run(command + ['apply', '--file', response_path, '--allow-read', *options])
        verified = run(command + ['verify', *options])
        assert verified['source']['source_kind'] == 'custom_codex'
        assert verified['source']['source_home'] == str(source_home.resolve())
        backends = list((install_root / 'releases').glob('*/断点复原浮窗.app/Contents/Resources/Recovery/recovery.py'))
        assert len(backends) == 1
        view = run([sys.executable, '-B', backends[0], '--state-dir', state, 'panel-read'])
        items = [item for group in view['projects'] for item in group['items']]
        assert len(items) == 1 and items[0]['title'] == '路线选择'
        assert not (install_root / 'releases').joinpath('state.json').exists()
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        assert before == after, 'Source conversations were modified'
        assert all('隔离任务' not in item['title'] for item in items)
    print(json.dumps({'ok': True, 'synthetic_fixture': True, 'source_kind': 'custom_codex',
                      'result': 'synthetic protocol flow: install → scan → review → installed backend; one item; source unchanged'}))


if __name__ == '__main__':
    main()
