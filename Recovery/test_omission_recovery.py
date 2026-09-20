"""Reported omission replay; fake tasks and temporary runtime directories only.

These checks cover record repair and downstream behavior. Conversation-level
semantic selection is reviewed separately; it is not performed by recovery.py.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from recovery import (candidates, feedback, import_app, merge_panel_report,
                      panel_view, report)


SID = '10000000-0000-0000-0000-000000000050'
QUOTE = '先列出用途，我再考虑是否安装，我还在犹豫。'
POINT = '用途和触发方式已说明，尚未决定是否安装。'
NOTE = {'id': SID, 'source_thread': SID, 'based_on': 'same-conversation',
        'text': POINT, 'resume_context': {
            'previous_focus': '了解用途后考虑是否安装。',
            'current_state': '知识问题已答，安装选择尚未决定。',
            'next_step': '建议回看清单再决定，尚未授权安装。'}}
FEEDBACK = {'id': SID, 'status': 'undecided', 'quote': QUOTE,
            'source_thread': SID, 'summary': '是否安装尚未决定。'}


def fixture():
    return {'threads': {SID: {
        'id': SID, 'title': '测试安装决策', 'provider': 'codex_local',
        'fingerprint': 'same-conversation', 'updated_at': 1, 'available': True,
        'evidence': {'question': '主动触发还是被动触发？', 'answer': '两种都有。',
                     'no_reply_after_answer': True, 'read_state': 'unknown'}}},
        'sources': {}, 'last_report': {'token': 'previous', 'at': '2026-09-01T00:00:00Z'},
        'progress_followups': {}}


def repaired():
    state = fixture()
    feedback(state, [FEEDBACK])
    import_app(state, {'review_points': [NOTE]})
    return state


class OmissionRecoveryTests(unittest.TestCase):
    def test_old_exclusion_reproduces_omission_and_removal_restores_row(self):
        state = fixture()
        self.assertEqual(len(candidates(state)[0]), 1)
        self.assertEqual(report(state, 'scheduled', exclude=[SID])['items'], [])
        feedback(state, [FEEDBACK])
        import_app(state, {'review_points': [NOTE]})
        # Repairing the note alone does not defeat an old caller-supplied exclusion.
        self.assertEqual(report(state, 'scheduled', exclude=[SID])['items'], [])
        restored = report(state, 'scheduled', exclude=[])
        self.assertEqual(restored['items'][0]['review_point'], POINT)
        self.assertEqual(restored['items'][0]['status'], 'undecided')

    def test_repeated_merge_keeps_one_item_without_read_or_delivery_receipt(self):
        state = repaired()
        receipt = copy.deepcopy(state['last_report'])
        for _ in range(2):
            merge_panel_report(state, report(state, 'scheduled'))
        self.assertEqual(list(state['panel']['items']), [SID])
        row = panel_view(state)['projects'][0]['items'][0]
        self.assertTrue(row['actionable'])
        self.assertEqual(row['resume_context'], NOTE['resume_context'])
        self.assertEqual(state['threads'][SID]['evidence']['read_state'], 'unknown')
        self.assertEqual(state['last_report'], receipt)
        self.assertEqual(state['progress_followups'], {})

    def test_explicit_stops_and_archive_still_win_after_correction(self):
        for stop in ('done', 'settled', 'in_progress', 'dismissed', 'taken', 'archived'):
            with self.subTest(stop=stop):
                state = repaired()
                merge_panel_report(state, report(state, 'manual'))
                if stop in ('dismissed', 'taken'):
                    state['attention_controls'] = {SID: {'mode': stop, 'based_on': 'same-conversation'}}
                elif stop == 'archived':
                    state['threads'][SID]['archived'] = True
                    state['reminder_preferences'] = {'skip_archived': {'value': True}}
                else:
                    feedback(state, [dict(FEEDBACK, status=stop, quote='这项已处理或明确暂停。')])
                self.assertEqual(candidates(state)[0], [])
                self.assertEqual(panel_view(state)['projects'], [])

    def test_unreviewed_qa_is_not_automatically_published(self):
        state = fixture()
        merge_panel_report(state, report(state, 'manual'))
        self.assertEqual(panel_view(state)['projects'], [])
        self.assertNotIn('feedback', state['threads'][SID])

    def test_outdated_summary_is_rejected(self):
        state = fixture()
        state['threads'][SID]['fingerprint'] = 'new-conversation'
        with self.assertRaises(ValueError):
            import_app(state, {'review_points': [NOTE]})

    def test_actual_cli_roundtrip_uses_only_temporary_state(self):
        with tempfile.TemporaryDirectory(prefix='resume-desk-omission-') as directory:
            root = Path(directory)
            (root / 'state.json').write_text(json.dumps(fixture()))
            (root / 'feedback.json').write_text(json.dumps([FEEDBACK]))
            (root / 'notes.json').write_text(json.dumps({'review_points': [NOTE]}))
            command = [sys.executable, '-B', str(Path(__file__).with_name('recovery.py')),
                       '--state-dir', str(root)]

            def run(*args):
                result = subprocess.run(command + list(args), capture_output=True,
                                        text=True, check=True)
                return json.loads(result.stdout)

            self.assertEqual(run('report', '--mode', 'scheduled', '--exclude', SID)['items'], [])
            run('feedback', str(root / 'feedback.json'))
            run('import-app', str(root / 'notes.json'))
            restored = run('report', '--mode', 'scheduled')
            self.assertEqual([item['id'] for item in restored['items']], [SID])
            self.assertIn(POINT, restored['presentation'])
            self.assertEqual(run('panel-read')['projects'][0]['items'][0]['resume_context'], NOTE['resume_context'])
            persisted = json.loads((root / 'state.json').read_text())
            self.assertEqual(persisted['last_report'], fixture()['last_report'])
            self.assertEqual(persisted['progress_followups'], {})


if __name__ == '__main__':
    unittest.main()
