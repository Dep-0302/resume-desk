"""Contract checks for the explicit, local-only Agent review bridge.

Every source in this file is a temporary SQLite index plus synthetic JSONL.  It
never opens a real Codex home or a personal ResumeDesk state directory.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from recovery import AGENT_EVIDENCE_LIMIT, assess_attention, fingerprint, parse_records


HERE = Path(__file__).resolve().parent
PROGRAM = HERE / 'recovery.py'
FIRST = '10000000-0000-0000-0000-000000000001'
SECOND = '10000000-0000-0000-0000-000000000002'


def record(role, text, stamp, phase=None):
    payload = {'type': 'message', 'role': role, 'content': [{'text': text}]}
    if phase:
        payload['phase'] = phase
    return {'type': 'response_item', 'timestamp': stamp, 'payload': payload}


def write_session(path, question, answer='', running=False, leading_bytes=0):
    records = [record('user', question, '2026-09-20T10:00:00Z')]
    if leading_bytes:
        records.insert(0, {'type': 'response_item', 'timestamp': '2026-09-20T09:59:00Z',
                           'payload': {'type': 'function_call', 'arguments': 'x' * leading_bytes}})
    if answer:
        records.append(record('assistant', answer, '2026-09-20T10:01:00Z', 'final_answer'))
    if running:
        records.append({'type': 'event_msg', 'timestamp': '2026-09-20T10:02:00Z',
                        'payload': {'type': 'task_started', 'turn_id': 'running-turn'}})
    path.write_text('\n'.join(json.dumps(item, ensure_ascii=False) for item in records) + '\n')


def make_home(root, rows):
    home = root / 'codex'
    sessions = home / 'sessions'
    sessions.mkdir(parents=True)
    connection = sqlite3.connect(home / 'state_5.sqlite')
    connection.execute('create table threads(id,title,cwd,rollout_path,archived,source,updated_at,first_user_message)')
    for index, row in enumerate(rows):
        path = sessions / (row['id'] + '.jsonl')
        write_session(path, row.get('question', '请确认下一步。'), row.get('answer', ''), row.get('running', False))
        connection.execute('insert into threads values (?,?,?,?,?,?,?,?)', (
            row['id'], row.get('title', '测试任务'), '/tmp/project', str(path),
            int(row.get('archived', False)), row.get('source', 'cli'), index + 1,
            row.get('question', '请确认下一步。')))
    connection.commit()
    connection.close()
    return home


def append_row(home, row, updated_at=99):
    path = home / 'sessions' / (row['id'] + '.jsonl')
    write_session(path, row.get('question', '请确认下一步。'), row.get('answer', ''), row.get('running', False))
    connection = sqlite3.connect(home / 'state_5.sqlite')
    connection.execute('insert into threads values (?,?,?,?,?,?,?,?)', (
        row['id'], row.get('title', '测试任务'), '/tmp/project', str(path),
        int(row.get('archived', False)), row.get('source', 'cli'), updated_at,
        row.get('question', '请确认下一步。')))
    connection.commit()
    connection.close()


class AgentReviewTests(unittest.TestCase):
    def invoke(self, state_dir, *args, check=True, env=None):
        command = [sys.executable, '-B', str(PROGRAM), '--state-dir', str(state_dir), *args]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if check and result.returncode:
            self.fail('command failed:\n' + result.stderr)
        return result

    def output(self, state_dir, *args, **kwargs):
        return json.loads(self.invoke(state_dir, *args, **kwargs).stdout)

    def export(self, state_dir, home):
        return self.output(state_dir, 'agent-export', '--home', str(home), '--allow-read')

    def apply(self, state_dir, payload):
        path = state_dir / 'decisions.json'
        path.write_text(json.dumps(payload, ensure_ascii=False))
        return self.output(state_dir, 'agent-apply', str(path), '--allow-read')

    @staticmethod
    def decision(item, kind, **extra):
        result = {'id': item['id'], 'fingerprint': item['fingerprint'], 'decision': kind,
                  'reason': extra.pop('reason', '已逐字核对当前对话。'),
                  'evidence_quote': extra.pop('evidence_quote', '')}
        result.update(extra)
        return result

    def test_export_requires_explicit_read_permission_before_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            missing = root / 'missing-codex-home'
            result = self.invoke(state_dir, 'agent-export', '--home', str(missing), check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn('--allow-read', result.stderr)
            self.assertNotIn('无法读取 Codex 本地索引', result.stderr)

    def test_batch_must_contain_every_exported_id_once_without_partial_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            exported = self.export(state_dir, make_home(root, [
                {'id': FIRST, 'question': '请确认甲。', 'answer': '可以先核对。'},
                {'id': SECOND, 'question': '请确认乙。', 'answer': '可以先核对。'},
            ]))
            before = hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest()
            incomplete = {'schema_version': 1, 'batch_token': exported['batch_token'],
                          'decisions': [self.decision(exported['items'][0], 'no_action', evidence_quote='可以先核对。')]}
            path = state_dir / 'decisions.json'
            path.write_text(json.dumps(incomplete, ensure_ascii=False))
            result = self.invoke(state_dir, 'agent-apply', str(path), '--allow-read', check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn('完整覆盖', result.stderr)
            self.assertEqual(hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest(), before)

    def test_stale_source_rejects_whole_batch_without_partial_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            home = make_home(root, [
                {'id': FIRST, 'question': '请确认甲。', 'answer': '回答甲。'},
                {'id': SECOND, 'question': '请确认乙。', 'answer': '回答乙。'},
            ])
            exported = self.export(state_dir, home)
            before = hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest()
            write_session(home / 'sessions' / (FIRST + '.jsonl'), '请确认甲。', '新版回答甲。')
            payload = {'schema_version': 1, 'batch_token': exported['batch_token'], 'decisions': [
                self.decision(item, 'needs_user', evidence_quote='请确认甲。', review_point='甲任务仍在等待你的明确确认。')
                if item['id'] == FIRST else self.decision(item, 'no_action', evidence_quote='回答乙。')
                for item in exported['items']
            ]}
            path = state_dir / 'decisions.json'
            path.write_text(json.dumps(payload, ensure_ascii=False))
            result = self.invoke(state_dir, 'agent-apply', str(path), '--allow-read', check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn('重新导出整批', result.stderr)
            self.assertEqual(hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest(), before)

    def test_change_after_legacy_2400_character_excerpt_rejects_old_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            home = make_home(root, [{'id': FIRST, 'question': '请核对长答复。', 'answer': 'a' * 2400 + '原结尾'}])
            exported = self.export(state_dir, home)
            self.assertFalse(exported['items'][0]['evidence']['answer_truncated'])
            before = hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest()
            write_session(home / 'sessions' / (FIRST + '.jsonl'), '请核对长答复。', 'a' * 2400 + '新结尾')
            payload = {'schema_version': 1, 'batch_token': exported['batch_token'], 'decisions': [
                self.decision(exported['items'][0], 'no_action', evidence_quote='a' * 80)
            ]}
            path = state_dir / 'long-stale.json'
            path.write_text(json.dumps(payload, ensure_ascii=False))
            result = self.invoke(state_dir, 'agent-apply', str(path), '--allow-read', check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn('重新导出整批', result.stderr)
            self.assertEqual(hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest(), before)

    def test_complete_answer_beyond_2400_can_be_reviewed_without_legacy_quote_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            tail = '明确无需用户处理'
            exported = self.export(state_dir, make_home(root, [
                {'id': FIRST, 'question': '长答复是否结束？', 'answer': 'a' * 2500 + tail},
            ]))
            item = exported['items'][0]
            self.assertIn(tail, item['evidence']['answer'])
            self.assertFalse(item['evidence']['answer_truncated'])
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'],
                                            'decisions': [self.decision(item, 'no_action', evidence_quote=tail)]})
            self.assertEqual(result['stage'], 'ready')

    def test_needs_user_can_quote_complete_agent_evidence_after_legacy_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            tail = '请确认是否继续下一步'
            exported = self.export(state_dir, make_home(root, [
                {'id': FIRST, 'question': '长答复是否要确认？', 'answer': 'a' * 2500 + tail},
            ]))
            item = exported['items'][0]
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'], 'decisions': [
                self.decision(item, 'needs_user', evidence_quote=tail,
                              review_point='长答复已说明后续选择，当前等待你确认是否继续。')
            ]})
            self.assertEqual(result['stage'], 'ready')
            self.assertTrue(result['has_pending_items'])

    def test_forged_agent_source_digest_is_rejected_by_attention_assessment(self):
        evidence = parse_records([
            record('user', '请核对来源。', '2026-09-20T10:00:00Z'),
            record('assistant', 'a' * 2500 + '请确认。', '2026-09-20T10:01:00Z', 'final_answer'),
        ])
        state = {'threads': {FIRST: {'id': FIRST, 'fingerprint': fingerprint(evidence), 'evidence': evidence}},
                 'sources': {}}
        with self.assertRaises(ValueError):
            assess_attention(state, [{'id': FIRST, 'based_on': fingerprint(evidence),
                                      'source_thread': FIRST, 'decision': 'needs_user', 'reason': '伪造来源。',
                                      'pending_quote': '请确认。', 'review_point': '等待你确认下一步。',
                                      'agent_source_digest': 'forged'}])

    def test_agent_excerpt_over_budget_requires_unknown_and_never_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            exported = self.export(state_dir, make_home(root, [
                {'id': FIRST, 'question': '这条很长的答复还有遗漏吗？', 'answer': 'a' * (AGENT_EVIDENCE_LIMIT + 1)},
            ]))
            item = exported['items'][0]
            self.assertTrue(item['evidence']['answer_truncated'])
            self.assertFalse(exported['coverage']['complete'])
            invalid = {'schema_version': 1, 'batch_token': exported['batch_token'],
                       'decisions': [self.decision(item, 'no_action', evidence_quote='a' * 80)]}
            path = state_dir / 'over-budget.json'
            path.write_text(json.dumps(invalid, ensure_ascii=False))
            self.assertIn('只能标记 unknown', self.invoke(state_dir, 'agent-apply', str(path), '--allow-read', check=False).stderr)
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'],
                                            'decisions': [self.decision(item, 'unknown')]})
            self.assertEqual(result['stage'], 'needs_attention')
            self.assertIn('agent_evidence_truncated', [gap['code'] for gap in result['coverage']['gaps']])

    def test_normal_question_answer_is_reviewed_but_not_put_in_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            exported = self.export(state_dir, make_home(root, [
                {'id': FIRST, 'question': '这个命令怎么用？', 'answer': '用 --help 可以查看参数。'},
            ]))
            item = exported['items'][0]
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'],
                                            'decisions': [self.decision(item, 'no_action', evidence_quote='用 --help 可以查看参数。')]})
            self.assertEqual(result['stage'], 'ready')
            self.assertEqual(result['panel_count'], 0)
            self.assertTrue(result['last_review'])
            self.assertEqual(result['source_kind'], 'custom_codex')
            self.assertEqual(result['last_review']['source_home'], str((root / 'codex').resolve()))
            self.assertEqual(self.output(state_dir, 'panel-read')['projects'], [])
            before = hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest()
            repeated = self.invoke(state_dir, 'agent-apply', str(state_dir / 'decisions.json'), '--allow-read', check=False)
            self.assertEqual(repeated.returncode, 2)
            self.assertIn('已提交', repeated.stderr)
            self.assertEqual(hashlib.sha256((state_dir / 'state.json').read_bytes()).hexdigest(), before)

    def test_needs_user_with_current_quote_is_the_only_new_panel_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            exported = self.export(state_dir, make_home(root, [
                {'id': FIRST, 'question': '请确认是否继续发布。', 'answer': '我已准备好，等待你的确认。'},
            ]))
            item = exported['items'][0]
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'], 'decisions': [
                self.decision(item, 'needs_user', evidence_quote='请确认是否继续发布。',
                              review_point='发布材料已准备，当前等待你确认是否继续。')
            ]})
            self.assertEqual(result['stage'], 'ready')
            self.assertTrue(result['has_pending_items'])
            self.assertEqual(result['applied']['needs_user'], 1)
            panel = self.output(state_dir, 'panel-read')
            self.assertEqual(panel['projects'][0]['items'][0]['id'], FIRST)
            self.assertIn('等待你确认', panel['projects'][0]['items'][0]['review_point'])

    def test_unknown_keeps_unknown_and_cannot_make_status_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            exported = self.export(state_dir, make_home(root, [
                {'id': FIRST, 'question': '我该从哪里继续？', 'answer': '已有一些背景。'},
            ]))
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'],
                                            'decisions': [self.decision(exported['items'][0], 'unknown')]})
            self.assertEqual(result['stage'], 'needs_attention')
            self.assertEqual(result['last_review']['unknown_count'], 1)
            self.assertEqual(result['panel_count'], 0)

    def test_new_unreviewed_source_is_a_coverage_gap_not_a_ready_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            home = make_home(root, [{'id': FIRST, 'question': '如何运行？', 'answer': '运行命令即可。'}])
            exported = self.export(state_dir, home)
            append_row(home, {'id': SECOND, 'question': '新的对话怎么办？', 'answer': '请先核对。'})
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'],
                                            'decisions': [self.decision(exported['items'][0], 'no_action', evidence_quote='运行命令即可。')]})
            self.assertEqual(result['stage'], 'needs_attention')
            self.assertFalse(result['coverage']['complete'])
            self.assertIn('source_inventory_changed', [gap['code'] for gap in result['coverage']['gaps']])

    def test_bounded_tail_excerpt_can_be_complete_for_this_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            home = make_home(root, [{'id': FIRST, 'question': '如何继续？', 'answer': '先运行检查。'}])
            write_session(home / 'sessions' / (FIRST + '.jsonl'), '如何继续？', '先运行检查。', leading_bytes=300000)
            exported = self.export(state_dir, home)
            self.assertTrue(exported['coverage']['complete'])
            self.assertEqual(exported['coverage']['excerpt_count'], 1)
            self.assertNotIn('arguments', exported['items'][0]['evidence'])
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'],
                                            'decisions': [self.decision(exported['items'][0], 'no_action', evidence_quote='先运行检查。')]})
            self.assertEqual(result['stage'], 'ready')
            self.assertIn('有界节选', result['coverage']['scope'])

    def test_missing_final_answer_is_a_gap_and_must_stay_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            exported = self.export(state_dir, make_home(root, [{'id': FIRST, 'question': '我该继续吗？'}]))
            item = exported['items'][0]
            self.assertFalse(item['evidence_complete'])
            self.assertFalse(exported['coverage']['complete'])
            invalid = {'schema_version': 1, 'batch_token': exported['batch_token'],
                       'decisions': [self.decision(item, 'no_action', evidence_quote='我该继续吗？')]}
            path = state_dir / 'invalid.json'
            path.write_text(json.dumps(invalid, ensure_ascii=False))
            self.assertIn('只能标记 unknown', self.invoke(state_dir, 'agent-apply', str(path), '--allow-read', check=False).stderr)
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'],
                                            'decisions': [self.decision(item, 'unknown')]})
            self.assertEqual(result['stage'], 'needs_attention')
            self.assertIn('missing_current_evidence', [gap['code'] for gap in result['coverage']['gaps']])

    def test_real_empty_source_can_be_ready_after_an_empty_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            exported = self.export(state_dir, make_home(root, []))
            self.assertEqual(exported['items'], [])
            self.assertTrue(exported['coverage']['complete'])
            result = self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'], 'decisions': []})
            self.assertEqual(result['stage'], 'ready')
            self.assertEqual(result['last_review']['reviewed_count'], 0)
            self.assertTrue(result['last_review']['coverage_complete'])

    def test_taken_reopens_only_after_new_fingerprint_but_dismiss_stays_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            home = make_home(root, [{'id': FIRST, 'question': '请确认初版。', 'answer': '等待确认。'}])
            exported = self.export(state_dir, home)
            item = exported['items'][0]
            self.apply(state_dir, {'schema_version': 1, 'batch_token': exported['batch_token'], 'decisions': [
                self.decision(item, 'needs_user', evidence_quote='请确认初版。', review_point='初版已经完成，等待你确认下一步。')
            ]})
            shown = self.output(state_dir, 'panel-read')['projects'][0]['items'][0]
            self.output(state_dir, 'panel-action', '--id', FIRST, '--action', 'take', '--token', shown['action_token'])
            persisted = json.loads((state_dir / 'state.json').read_text())
            persisted['attention_controls'][FIRST].pop('agent_source_digest')
            (state_dir / 'state.json').write_text(json.dumps(persisted, ensure_ascii=False))
            unchanged = self.export(state_dir, home)
            self.assertEqual(unchanged['items'], [])
            self.assertEqual(unchanged['coverage']['excluded']['taken_current'], 1)
            write_session(home / 'sessions' / (FIRST + '.jsonl'), '请确认新版。', '等待新版确认。')
            after_take = self.export(state_dir, home)
            self.assertEqual([entry['id'] for entry in after_take['items']], [FIRST])

            fresh = after_take['items'][0]
            self.apply(state_dir, {'schema_version': 1, 'batch_token': after_take['batch_token'], 'decisions': [
                self.decision(fresh, 'needs_user', evidence_quote='请确认新版。', review_point='新版内容已经准备，等待你确认是否继续。')
            ]})
            pending_batch = self.export(state_dir, home)
            shown = self.output(state_dir, 'panel-read')['projects'][0]['items'][0]
            self.output(state_dir, 'panel-action', '--id', FIRST, '--action', 'dismiss', '--token', shown['action_token'])
            pending_payload = {'schema_version': 1, 'batch_token': pending_batch['batch_token'], 'decisions': [
                self.decision(pending_batch['items'][0], 'needs_user', evidence_quote='请确认新版。',
                              review_point='新版内容已经准备，等待你确认是否继续。')
            ]}
            path = state_dir / 'stale-control.json'
            path.write_text(json.dumps(pending_payload, ensure_ascii=False))
            rejected = self.invoke(state_dir, 'agent-apply', str(path), '--allow-read', check=False)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn('浮窗修改', rejected.stderr)
            write_session(home / 'sessions' / (FIRST + '.jsonl'), '请确认第三版。', '等待第三版确认。')
            after_dismiss = self.export(state_dir, home)
            self.assertEqual(after_dismiss['items'], [])
            self.assertEqual(after_dismiss['coverage']['excluded']['permanently_dismissed'], 1)

    def test_new_source_suffix_does_not_leave_old_agent_pending_blocking_no_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / 'state'
            home = make_home(root, [{'id': FIRST, 'question': '长答复还有待办吗？',
                                     'answer': 'a' * 2400 + '旧结尾'}])
            first = self.export(state_dir, home)
            self.apply(state_dir, {'schema_version': 1, 'batch_token': first['batch_token'], 'decisions': [
                self.decision(first['items'][0], 'needs_user', evidence_quote='长答复还有待办吗？',
                              review_point='旧来源中有待确认事项，等待你决定。')
            ]})
            write_session(home / 'sessions' / (FIRST + '.jsonl'), '长答复还有待办吗？', 'a' * 2400 + '新结尾无需处理')
            second = self.export(state_dir, home)
            self.assertEqual([item['id'] for item in second['items']], [FIRST])
            self.apply(state_dir, {'schema_version': 1, 'batch_token': second['batch_token'], 'decisions': [
                self.decision(second['items'][0], 'no_action', evidence_quote='新结尾无需处理')
            ]})
            row = self.output(state_dir, 'panel-read')['projects'][0]['items'][0]
            self.assertFalse(row['actionable'])

    def test_unreadable_source_fails_clearly_when_read_is_authorized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.invoke(root / 'state', 'agent-export', '--home', str(root / 'absent'), '--allow-read', check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn('无法读取 Codex 本地索引', result.stderr)


if __name__ == '__main__':
    unittest.main()
