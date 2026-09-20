import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from recovery import (assess_attention, candidates, import_app, merge_panel_report,
                      panel_action, panel_view, parse_records, report, feedback,
                      navigation_for)


def fixture(count=12):
    state = {'threads': {}, 'sources': {}}
    for n in range(count):
        sid = '10000000-0000-0000-0000-%012d' % n
        state['threads'][sid] = {
            'id': sid, 'title': '原任务 %d' % n, 'fingerprint': 'v1',
            'cwd': '/work/%03d-project' % (n % 2 + 1), 'updated_at': 1700000000+n,
            'evidence': {'question': '我想继续这个项目', 'answer': '需要你确认下一步',
                         'no_reply_after_answer': True},
            'review_note': {'text': '你想继续做；现在停在确认下一步。', 'based_on': 'v1',
                            'source_thread': sid}}
    return state


def first_item(view):
    return view['projects'][0]['items'][0]


class PanelTests(unittest.TestCase):
    def test_original_navigation_is_source_aware_and_rejects_invalid_ids(self):
        sid = '10000000-0000-0000-0000-000000000001'
        self.assertEqual(navigation_for({'id': sid, 'provider': 'chatgpt'}),
                         {'kind': 'chatgpt', 'url': 'https://chatgpt.com/c/' + sid})
        self.assertEqual(navigation_for({'id': sid, 'provider': 'codex_local'}),
                         {'kind': 'codex', 'url': 'codex://threads/' + sid})
        self.assertIsNone(navigation_for({'id': '../new?prompt=run', 'provider': 'chatgpt'}))

    def test_chatgpt_source_groups_do_not_merge_with_unassigned_codex(self):
        s = fixture(3)
        ids = list(s['threads'])
        for e in s['threads'].values():
            e['cwd'] = None
        s['threads'][ids[0]]['provider'] = 'chatgpt'
        s['threads'][ids[1]]['provider'] = 'chatgpt'
        s['threads'][ids[1]]['project_id'] = 'cloud-reading'
        s['projects'] = {'cloud-reading': {'label': '实际阅读项目', 'projectKind': 'chatgpt'}}
        r = report(s, 'manual')
        merge_panel_report(s, r)
        before = copy.deepcopy(s)
        view = panel_view(s)
        self.assertEqual(s, before)
        self.assertEqual(len(view['projects']), 3)
        cloud = [p for p in view['projects'] if p['source_kind'] == 'chatgpt']
        self.assertEqual({p['label'] for p in cloud}, {'ChatGPT · 实际阅读项目', 'ChatGPT · 独立对话'})
        self.assertEqual(s['threads'][ids[1]]['project_id'], 'cloud-reading')
        self.assertEqual(len([p for p in view['projects'] if p['source_kind'] == 'codex']), 1)

    def test_queued_source_metadata_refreshes_without_rewriting_attention(self):
        s, r = self.published(1)
        old = first_item(panel_view(s))
        sid = old['id']
        s['threads'][sid].update(provider='chatgpt', cwd=None)
        before = copy.deepcopy(s)
        new = first_item(panel_view(s))
        self.assertEqual(s, before)
        self.assertEqual(new['project']['name'], '独立对话')
        self.assertEqual(new['navigation']['url'], 'https://chatgpt.com/c/' + sid)
        self.assertNotEqual(old['action_token'], new['action_token'])
        with self.assertRaises(ValueError):
            panel_action(s, r, sid, 'take', old['action_token'])
        self.assertNotIn('attention_controls', s)

    def published(self, count=2):
        s=fixture(count);r=report(s,'manual');merge_panel_report(s,r)
        return s,r

    def act(self,s,r,mode):
        item=first_item(panel_view(s,r))
        panel_action(s,r,item['id'],mode,item['action_token'])
        return item['id']

    def test_legacy_bootstrap_does_not_write_or_import_all_candidates(self):
        s=fixture(20);r=report(s,'manual',limit=10);before=copy.deepcopy(s)
        view=panel_view(s,r)
        self.assertEqual(sum(len(p['items']) for p in view['projects']),10)
        self.assertEqual(s,before)

    def test_unreviewed_excerpts_are_not_published(self):
        s=fixture(2)
        for e in s['threads'].values():e.pop('review_note')
        r=report(s,'manual');merge_panel_report(s,r)
        self.assertEqual(panel_view(s)['projects'],[])

    def test_default_candidates_report_and_panel_are_unlimited(self):
        s=fixture(71)
        self.assertEqual(len(candidates(s)[0]),71)
        r=report(s,'manual')
        self.assertEqual(len(r['items']),71)
        merge_panel_report(s,r)
        self.assertEqual(sum(len(p['items']) for p in panel_view(s)['projects']),71)
        # An explicitly requested partial result remains available for tests.
        self.assertEqual(len(report(s,'manual',limit=12)['items']),12)

    def test_cli_unlimited_preserves_suppression_and_review_gate(self):
        for mode in ('manual','scheduled'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                folder=Path(temp);s=fixture(71);ids=list(s['threads'])
                s['attention_controls']={ids[0]:{'mode':'dismissed','based_on':'v1'},
                                         ids[1]:{'mode':'taken','based_on':'v1'}}
                s['reminder_preferences']={'skip_archived':{'value':True},
                                          'skip_dialogue_organization':{'value':True}}
                s['threads'][ids[2]]['archived']=True
                s['threads'][ids[3]]['title']='整理 Codex 对话'
                feedback(s,[{'id':ids[4],'status':'done','quote':'已处理','source_thread':'test'}])
                s['threads'][ids[5]].pop('review_note')
                (folder/'state.json').write_text(json.dumps(s))
                cmd=[sys.executable,'-B',str(Path(__file__).with_name('recovery.py')),
                     '--state-dir',str(folder),'report','--mode',mode]
                result=json.loads(subprocess.check_output(cmd))
                saved=json.loads((folder/'state.json').read_text())
                self.assertEqual(len(result['items']),66)
                shown={i['id'] for p in panel_view(saved)['projects'] for i in p['items']}
                self.assertEqual(len(shown),65)
                self.assertTrue(set(ids[:6]).isdisjoint(shown))
                self.assertEqual(saved['attention_controls'],s['attention_controls'])
                self.assertEqual(saved['reminder_preferences'],s['reminder_preferences'])
                self.assertEqual(saved['threads'][ids[4]]['feedback'],s['threads'][ids[4]]['feedback'])

    def test_daily_batches_accumulate_and_deduplicate_without_losing_old_items(self):
        s=fixture(15);r=report(s,'manual',limit=10);merge_panel_report(s,r)
        first={i['id'] for i in r['items']}
        second=report(s,'manual',limit=10,exclude=first);merge_panel_report(s,second)
        self.assertEqual(sum(len(p['items']) for p in panel_view(s)['projects']),15)
        merge_panel_report(s,second)
        self.assertEqual(len(s['panel']['items']),15)

    def test_take_hides_both_surfaces_without_marking_complete(self):
        s,r=self.published();sid=self.act(s,r,'take')
        self.assertNotIn(sid,{i['id'] for i in candidates(s)[0]})
        self.assertNotIn('feedback',s['threads'][sid])
        self.assertEqual(s['attention_controls'][sid]['mode'],'taken')
        view = panel_view(s)
        self.assertEqual(sum(len(p['items']) for p in view['projects']),1)
        self.assertIn('codex:' + sid.lower(), view['suppressed_ids'])

    def test_suppressed_ids_cover_all_legacy_hidden_records_and_reopen_safely(self):
        s, r = self.published(4)
        ids = list(s['threads'])
        taken = self.act(s, r, 'take')
        dismissed = next(item for group in panel_view(s)['projects'] for item in group['items'])
        panel_action(s, r, dismissed['id'], 'dismiss', dismissed['action_token'])
        s['threads'][dismissed['id']]['provider'] = 'chatgpt'
        feedback_id, preference_id = [sid for sid in ids if sid not in (taken, dismissed['id'])]
        feedback(s, [{'id': feedback_id, 'status': 'done', 'quote': '已处理', 'source_thread': 'fixture'}])
        s['threads'][preference_id]['archived'] = True
        s['reminder_preferences'] = {'skip_archived': {'value': True, 'at': '2026-09-20T12:00:00+00:00'}}
        hidden_only = 'f0000000-0000-0000-0000-000000000001'
        s['threads'][hidden_only] = dict(s['threads'][taken], id=hidden_only)
        s.setdefault('attention_controls', {})[hidden_only] = {'mode': 'dismissed', 'based_on': 'v1'}
        s['threads'][hidden_only]['available'] = False
        view = panel_view(s)
        self.assertEqual(set(view['suppressed_ids']), {
            'codex:' + taken.lower(),
            'chatgpt:' + dismissed['id'].lower(),
            'codex:' + feedback_id.lower(),
            'codex:' + preference_id.lower(),
            'codex:' + hidden_only,
        })
        controls = {item['identity']: item for item in view['suppressed_controls']}
        self.assertEqual(set(controls), set(view['suppressed_ids']))
        self.assertEqual(controls['codex:' + taken.lower()]['mode'], 'until_new_review')
        self.assertIsNotNone(controls['codex:' + taken.lower()]['at'])
        self.assertEqual(controls['chatgpt:' + dismissed['id'].lower()]['mode'], 'permanent')
        self.assertEqual(controls['codex:' + feedback_id.lower()]['mode'], 'until_new_review')
        self.assertEqual(controls['codex:' + preference_id.lower()]['mode'], 'permanent')
        self.assertEqual(controls['codex:' + hidden_only]['at'], None)
        entry = s['threads'][taken]
        entry['fingerprint'] = 'v2'
        entry['evidence']['answer'] = '请确认新版'
        assess_attention(s, [{'id': taken, 'based_on': 'v2', 'source_thread': taken,
                              'decision': 'needs_user', 'reason': '新版待确认',
                              'pending_quote': '请确认新版', 'review_point': '现在等待确认新版。'}])
        reopened = panel_view(s)
        self.assertNotIn('codex:' + taken.lower(), reopened['suppressed_ids'])
        self.assertNotIn('codex:' + taken.lower(), {item['identity'] for item in reopened['suppressed_controls']})

    def test_dismiss_is_permanent_across_new_fingerprints_but_not_other_threads(self):
        s,r=self.published();sid=self.act(s,r,'dismiss')
        s['threads'][sid]['fingerprint']='v2'
        s['threads'][sid]['feedback']={'status':'unread','based_on':'v2'}
        s['threads'][sid]['attention_assessment']={'decision':'needs_user','based_on':'v2'}
        self.assertNotIn(sid,{i['id'] for i in candidates(s)[0]})
        other=next(e for k,e in s['threads'].items() if k!=sid)
        other['cwd']=s['threads'][sid]['cwd']
        self.assertEqual(len(candidates(s)[0]),1)
        self.assertEqual(sum(len(p['items']) for p in panel_view(s)['projects']),1)

    def test_stale_click_cannot_acknowledge_new_work(self):
        s,r=self.published();item=first_item(panel_view(s));before=copy.deepcopy(s)
        s['threads'][item['id']]['fingerprint']='v2'
        with self.assertRaises(ValueError):panel_action(s,r,item['id'],'take',item['action_token'])
        self.assertNotIn('attention_controls',s)
        shown=next(i for p in panel_view(s)['projects'] for i in p['items'] if i['id']==item['id'])
        self.assertFalse(shown['actionable'])
        self.assertEqual(shown['review_point'],item['review_point'])

    def test_duplicate_click_does_not_change_other_state(self):
        s,r=self.published();item=first_item(panel_view(s))
        panel_action(s,r,item['id'],'take',item['action_token']);after=copy.deepcopy(s)
        with self.assertRaises(ValueError):panel_action(s,r,item['id'],'dismiss',item['action_token'])
        self.assertEqual(s,after)

    def test_same_fingerprint_reworded_row_invalidates_old_click(self):
        s,r=self.published();old=first_item(panel_view(s));sid=old['id']
        s['threads'][sid]['review_note']['text']='现在新增一处需要你核对的内容'
        merge_panel_report(s,report(s,'manual'))
        current=next(i for p in panel_view(s)['projects'] for i in p['items'] if i['id']==sid)
        self.assertNotEqual(old['action_token'],current['action_token'])
        with self.assertRaises(ValueError):panel_action(s,r,sid,'take',old['action_token'])

    def test_resume_context_round_trips_to_readonly_panel_and_expires_with_evidence(self):
        s,r=self.published();old=first_item(panel_view(s));sid=old['id']
        for entry in s['threads'].values():
            entry['provider']='codex_local'
        import_app(s, {'review_points': [{'id': sid, 'text': '你想继续做；现在停在确认下一步。',
            'based_on': 'v1', 'source_thread': sid, 'resume_context': {
                'previous_focus': '确认原任务范围', 'current_state': None,
                'next_step': '打开原任务继续确认'}}]})
        # Context-only imports need no new daily report: panel-read projects the
        # current evidence-bound note over the existing reviewed queue.
        current=next(i for p in panel_view(s)['projects'] for i in p['items'] if i['id']==sid)
        self.assertEqual(current['resume_context'], {'previous_focus': '确认原任务范围',
            'current_state': None, 'next_step': '打开原任务继续确认'})
        self.assertNotEqual(old['action_token'],current['action_token'])
        with self.assertRaises(ValueError):panel_action(s,r,sid,'take',old['action_token'])
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            (folder/'state.json').write_text(json.dumps(s))
            (folder/'latest-report.json').write_text(json.dumps(r))
            before=(folder/'state.json').read_bytes()
            command=[sys.executable, '-B', str(Path(__file__).with_name('recovery.py')),
                     '--state-dir', str(folder), 'panel-read']
            cli_row=next(i for p in json.loads(subprocess.check_output(command))['projects']
                         for i in p['items'] if i['id']==sid)
            self.assertEqual(cli_row['resume_context'], current['resume_context'])
            self.assertEqual((folder/'state.json').read_bytes(), before)
        s['threads'][sid]['fingerprint']='v2'
        stale=next(i for p in panel_view(s)['projects'] for i in p['items'] if i['id']==sid)
        self.assertNotIn('resume_context',stale)
        self.assertFalse(stale['actionable'])

    def test_take_reappears_only_after_verified_new_attention_and_updates_same_row(self):
        s,r=self.published();sid=self.act(s,r,'take');e=s['threads'][sid]
        e['fingerprint']='v2';e['evidence']['answer']='请确认新版分镜'
        self.assertNotIn(sid,{i['id'] for i in candidates(s)[0]})
        assess_attention(s,[{'id':sid,'based_on':'v2','source_thread':sid,'decision':'needs_user',
                            'reason':'新版待确认','pending_quote':'请确认新版分镜','review_point':'你已经推进；现在需要确认新版分镜。'}])
        # Even if the daily chat omits it from its top ten, the known panel row is updated.
        merge_panel_report(s,report(s,'manual',exclude=[sid]))
        rows=[i for p in panel_view(s)['projects'] for i in p['items'] if i['id']==sid]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['fingerprint'],'v2')
        self.assertTrue(rows[0]['actionable']);self.assertIn('新版分镜',rows[0]['review_point'])

    def test_assistant_says_done_is_not_progress_evidence(self):
        s,r=self.published();sid=first_item(panel_view(s))['id'];e=s['threads'][sid]
        e['evidence']['answer']='已完成，不需要你处理'
        change={'id':sid,'based_on':'v1','source_thread':sid,'decision':'progressed_no_pending',
                'reason':'助手称完成','no_pending_user_action':True,
                'progress_evidence':{'kind':'user_progress','quote':'已完成'}}
        with self.assertRaises(ValueError):assess_attention(s,[change])
        self.assertEqual(sum(len(p['items']) for p in panel_view(s)['projects']),2)

    def test_user_progress_or_actual_file_event_can_support_daily_hide(self):
        for proof_kind in ['user_progress','file_change']:
            s,r=self.published();sid=first_item(panel_view(s))['id'];e=s['threads'][sid]
            e['evidence']['answer']='已推进，本轮没有后续事项'
            if proof_kind=='user_progress':
                e['evidence']['question']='我已经推进了这一步';proof={'kind':proof_kind,'quote':'我已经推进了这一步'}
            else:
                e['evidence'].update(turn_id='turn-1',lifecycle='task_complete')
                e['evidence']['progress_markers']=[{'id':'change-1','kind':'file_change','turn_id':'turn-1'}];proof={'kind':proof_kind,'event_id':'change-1'}
            assess_attention(s,[{'id':sid,'based_on':'v1','source_thread':sid,'decision':'progressed_no_pending',
                                'reason':'本轮已推进且没有待用户决策','no_pending_user_action':True,
                                'no_pending_quote':'本轮没有后续事项','progress_evidence':proof}])
            self.assertNotIn(sid,{i['id'] for i in candidates(s)[0]})
            view = panel_view(s)
            self.assertEqual(sum(len(p['items']) for p in view['projects']),1)
            control = next(item for item in view['suppressed_controls'] if item['identity'] == 'codex:' + sid.lower())
            self.assertEqual(control['mode'], 'until_new_review')
            self.assertIsNotNone(control['at'])
            self.assertNotIn('feedback',e)

    def test_unknown_assessment_does_not_hide_existing_item(self):
        s,r=self.published();sid=first_item(panel_view(s))['id']
        assess_attention(s,[{'id':sid,'based_on':'v1','source_thread':sid,'decision':'unknown','reason':'证据不足'}])
        self.assertEqual(sum(len(p['items']) for p in panel_view(s)['projects']),2)

    def test_explicit_new_pending_feedback_reopens_taken_row_and_repeated_feedback_is_idempotent(self):
        s,r=self.published();sid=first_item(panel_view(s))['id']
        change={'id':sid,'status':'unread','quote':'还没看','source_thread':'source'}
        feedback(s,[change]);self.act(s,r,'take')
        before=copy.deepcopy(s['threads'][sid]['feedback']);feedback(s,[change])
        self.assertEqual(s['threads'][sid]['feedback'],before)
        self.assertNotIn(sid,{i['id'] for i in candidates(s)[0]})
        s['threads'][sid]['fingerprint']='v2';change['quote']='这条新回复我还没看';feedback(s,[change])
        self.assertIn(sid,{i['id'] for i in candidates(s)[0]})
        merge_panel_report(s,report(s,'manual',exclude=[sid]))
        row=next(i for p in panel_view(s)['projects'] for i in p['items'] if i['id']==sid)
        self.assertEqual(row['fingerprint'],'v2');self.assertIn('新回复',row['review_point'])

    def test_unknown_recheck_does_not_hide_reopened_pending_row(self):
        s,r=self.published();sid=self.act(s,r,'take');s['threads'][sid]['fingerprint']='v2'
        assess_attention(s,[{'id':sid,'based_on':'v2','source_thread':sid,'decision':'needs_user',
                            'reason':'仍需确认','pending_quote':'需要你确认下一步','review_point':'现在等待你确认新版'}])
        assess_attention(s,[{'id':sid,'based_on':'v2','source_thread':sid,'decision':'unknown','reason':'这次证据不足'}])
        merge_panel_report(s,report(s,'manual'))
        self.assertIn(sid,{i['id'] for p in panel_view(s)['projects'] for i in p['items']})

    def test_user_pending_and_interrupted_turn_block_auto_hide_even_with_positive_quote(self):
        for question,lifecycle in [('我已经推进这一步，但下一步还需要我确认新版','task_complete'),
                                   ('我已经推进这一步','turn_aborted'),('我已经推进这一步','task_started')]:
            s,r=self.published();sid=first_item(panel_view(s))['id'];e=s['threads'][sid]
            e['evidence'].update(question=question,answer='本轮没有后续事项',turn_id='t',lifecycle=lifecycle,
                                 progress_markers=[{'id':'m','turn_id':'t','kind':'file_change'}])
            with self.assertRaises(ValueError):assess_attention(s,[{'id':sid,'based_on':'v1','source_thread':sid,
                'decision':'progressed_no_pending','reason':'冲突不能忽略','no_pending_user_action':True,
                'no_pending_quote':'本轮没有后续事项','progress_evidence':{'kind':'file_change','event_id':'m'}}])

    def test_negation_plans_conditions_and_questions_never_prove_actual_progress(self):
        for text in ['我没有完成，还没开始','我还没处理，先放着','我计划明天推进',
                     '如果我已经做完了会怎样','我已经做完了吗','我以为我已经做完了，其实没有',
                     '我希望我已经处理好了']:
            s,r=self.published();sid=first_item(panel_view(s))['id'];s['threads'][sid]['evidence']['question']=text
            change={'id':sid,'based_on':'v1','source_thread':sid,'decision':'progressed_no_pending',
                    'reason':'不能靠否定句自动收起','no_pending_user_action':True,
                    'progress_evidence':{'kind':'user_progress','quote':text}}
            with self.assertRaises(ValueError,msg=text):assess_attention(s,[change])
            self.assertEqual(sum(len(p['items']) for p in panel_view(s)['projects']),2)

    def test_native_progress_marker_contains_no_patch_content_and_resets_with_next_user(self):
        records=[{'type':'event_msg','payload':{'type':'task_started','turn_id':'turn-1'}},
                 {'type':'event_msg','timestamp':'2026-09-14T00:00:00Z','payload':{'type':'user_message','message':'继续执行'}},
                 {'type':'event_msg','timestamp':'2026-09-14T00:01:00Z','payload':{'type':'item_completed','item':{'id':'c1','type':'FileChange','status':'completed','changes':{'secret-path':{'content':'secret-data'}}}}}]
        ev=parse_records(records);self.assertEqual(ev['progress_markers'][0]['id'],'c1');self.assertNotIn('secret',json.dumps(ev))
        records.append({'type':'event_msg','payload':{'type':'user_message','message':'下一件事'}})
        self.assertEqual(parse_records(records)['progress_markers'],[])

    def test_new_turn_cannot_reuse_previous_file_change_and_pending_blocks_auto_hide(self):
        records=[{'type':'event_msg','payload':{'type':'task_started','turn_id':'t1'}},
                 {'type':'event_msg','payload':{'type':'item_completed','item':{'id':'old-change','type':'FileChange','status':'completed','changes':{'file':{}}}}},
                 {'type':'event_msg','payload':{'type':'task_started','turn_id':'t2'}},
                 {'type':'event_msg','payload':{'type':'task_complete','last_agent_message':'请你确认新版'}}]
        self.assertEqual(parse_records(records)['progress_markers'],[])
        s,r=self.published();sid=first_item(panel_view(s))['id'];e=s['threads'][sid]
        e['evidence'].update(turn_id='t2',progress_markers=[{'id':'new-change','kind':'file_change','turn_id':'t2'}],answer='请你确认新版')
        with self.assertRaises(ValueError):assess_attention(s,[{'id':sid,'based_on':'v1','source_thread':sid,
            'decision':'progressed_no_pending','reason':'存在真实改动也不能忽略待确认','no_pending_user_action':True,
            'progress_evidence':{'kind':'file_change','event_id':'new-change'}}])

    def test_cli_read_is_readonly_and_two_actions_persist_without_lost_update(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);s,r=self.published();(folder/'state.json').write_text(json.dumps(s));(folder/'latest-report.json').write_text(json.dumps(r))
            cmd=[sys.executable,'-B',str(Path(__file__).with_name('recovery.py')),'--state-dir',str(folder)]
            before=(folder/'state.json').read_bytes();view=json.loads(subprocess.check_output(cmd+['panel-read']))
            self.assertEqual((folder/'state.json').read_bytes(),before)
            items=[i for p in view['projects'] for i in p['items']]
            processes=[subprocess.Popen(cmd+['panel-action','--id',i['id'],'--action',action,'--token',i['action_token']],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for i,action in zip(items,['take','dismiss'])]
            for process in processes:
                out,err=process.communicate();self.assertEqual(process.returncode,0,err)
            saved=json.loads((folder/'state.json').read_text());self.assertEqual(len(saved['attention_controls']),2)
            self.assertEqual(json.loads(subprocess.check_output(cmd+['panel-read']))['projects'],[])

    def test_first_daily_run_preserves_legacy_rows_before_replacing_report(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);s=fixture(15);old=report(s,'manual',limit=10)
            (folder/'state.json').write_text(json.dumps(s));(folder/'latest-report.json').write_text(json.dumps(old))
            cmd=[sys.executable,'-B',str(Path(__file__).with_name('recovery.py')),'--state-dir',str(folder),'report','--mode','manual']
            for item in old['items']:cmd+=['--exclude',item['id']]
            subprocess.run(cmd,check=True,capture_output=True)
            saved=json.loads((folder/'state.json').read_text())
            self.assertEqual(len(saved['panel']['items']),15)


if __name__=='__main__':unittest.main()
