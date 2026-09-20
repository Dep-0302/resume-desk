#!/usr/bin/env python3
"""Authorized native GUI regression; separate bundle, synthetic state, no real actions."""
from pathlib import Path
import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parents[1]
BUILT_APP = REPO_ROOT / 'dist' / '断点复原浮窗.app'
sys.path.insert(0, str(APP_DIR.parent))
from recovery import report, merge_panel_report


def main():
    root = Path(tempfile.mkdtemp(prefix='resume-desk-layout-regression-'))
    folder = root/'runtime'; folder.mkdir()
    (root/'recovery.py').symlink_to(APP_DIR.parent/'recovery.py')
    app = root/'常驻浮窗/断点复原.app'
    shutil.copytree(BUILT_APP, app)
    plist = app/'Contents/Info.plist'
    info = plistlib.loads(plist.read_bytes())
    info['CFBundleIdentifier'] = 'org.example.resumedesk.layout-regression'
    plist.write_bytes(plistlib.dumps(info))
    state = {'threads': {}, 'sources': {}}
    for n in range(13):
        sid = 'f1000000-0000-0000-0000-%012d' % n
        code = [7, 62, 999, 123][n % 4]
        project = ('超长真实项目名称保留完整换行' * 3) if code == 123 else '演示项目'
        state['threads'][sid] = {
            'id': sid, 'title': ('演示：这个很长的原任务标题必须完整换行，不能把浮窗和操作按钮撑到屏幕之外' if n == 12 else '演示任务 %d' % n),
            'fingerprint': 'test-v1', 'cwd': '/demo/%03d-%s' % (code, project),
            'updated_at': 1700000000+n,
            'evidence': {'question': '继续演示任务', 'answer': '请确认素材顺序', 'no_reply_after_answer': True},
            'review_note': {'text': '你已经整理好素材，停在核对最终顺序；现在从这里继续，不必重新回想整段对话。' * (5 if n == 12 else 1),
                            'based_on': 'test-v1', 'source_thread': sid}}
    # Mixed short and long copy must keep the right actions on the same card edge.
    for n in (0, 4):
        state['threads']['f1000000-0000-0000-0000-%012d' % n]['review_note']['text'] = '从这里继续。'
    result = report(state, 'manual', limit=13)
    result['generated_at'] = '2026-09-14T17:00:00+00:00'
    merge_panel_report(state, result)
    (folder/'state.json').write_text(json.dumps(state, ensure_ascii=False))
    (folder/'latest-report.json').write_text(json.dumps(result, ensure_ascii=False))
    original = hashlib.sha256((folder/'state.json').read_bytes()).hexdigest()
    diag = folder/'layout.json'
    binary = app/'Contents/MacOS/FloatingRecoveryPanel'
    # A legacy 760-wide saved frame must migrate once, keeping a valid x/y.
    (folder/'panel-ui.json').write_text(json.dumps({'schema_version': 1, 'frame': {'x': 150, 'y': 150, 'width': 760, 'height': 720}}))
    results = []
    for run in range(2):
        diag.unlink(missing_ok=True)
        if run:
            saved = json.loads((folder/'panel-ui.json').read_text())
            saved['frame'].update(x=180, y=170, width=448)
            (folder/'panel-ui.json').write_text(json.dumps(saved))
        with (folder/('process-%s.log' % run)).open('w') as log:
            process = subprocess.Popen([str(binary), '--test-mode', '--state-dir', str(folder),
                '--test-codex-running', 'true', '--codex-bundle-id', 'org.example.resumedesk.layout-target',
                '--diagnostics-path', str(diag)], stdout=log, stderr=log)
            try:
                deadline = time.monotonic()+12
                snapshot = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('App exited before GUI verification: '+str(process.returncode))
                    if diag.exists():
                        try: snapshot = json.loads(diag.read_text())
                        except ValueError: pass
                        if snapshot and snapshot.get('item_count') == 13 and snapshot.get('layout', {}).get('actionButtons'):
                            break
                    time.sleep(.1)
                assert snapshot and snapshot['item_count'] == 13, 'No populated UI diagnostics'
                time.sleep(.4)
                snapshot = json.loads(diag.read_text())
                layout = snapshot['layout']
                assert snapshot['visible'] and snapshot['project_count'] == 4
                assert layout['window']['width'] == (452 if run == 0 else 448)
                # Preserve requested placement when it fits; compact CI displays
                # legitimately clamp a 720pt window to their visible work area.
                screen, window = layout['visibleScreen'], layout['window']
                requested_x, requested_y = (150, 150) if run == 0 else (180, 170)
                expected_x = min(max(requested_x, screen['x']), screen['x'] + screen['width'] - window['width'])
                expected_y = min(max(requested_y, screen['y']), screen['y'] + screen['height'] - window['height'])
                assert window['x'] == expected_x
                assert window['y'] == expected_y
                assert window['height'] == min(720, screen['height'])
                assert layout['reviewDate'] == '9月14日', 'Must use report date, not current date'
                assert len(layout['projectHeadings']) == 4
                assert any('超长真实项目名称保留完整换行' in h for h in layout['projectHeadings'])
                assert len(layout['actionButtons']) == 8
                assert layout['horizontalContentFits'] and layout['windowFitsScreen']
                for card, left, right in zip(layout['cards'], layout['actionButtons'][::2], layout['actionButtons'][1::2]):
                    assert card['titleHeight'] + 1 >= card['titleRequiredHeight']
                    # The current list intentionally shows one truncated stop line;
                    # full verified text is tested in 接续悬停回归.py, including scrolling.
                    assert 16 <= card['bodyHeight'] <= 22
                    assert abs(left['x'] - card['frame']['x'] - 12) <= 1
                    assert abs(right['x'] + right['width'] - card['frame']['x'] - card['frame']['width'] + 12) <= 1
                    for button in (left, right):
                        assert button['width'] == 30 and button['height'] == 30
                        assert abs(button['y']+15 - (card['frame']['y']+card['frame']['height']/2)) <= 1
                assert hashlib.sha256((folder/'state.json').read_bytes()).hexdigest() == original
                saved = json.loads((folder/'panel-ui.json').read_text())
                assert saved['schema_version'] == 2
                shutil.copy2(diag, folder/('run-%d.json' % run))
                shutil.copy2(diag.with_suffix('.png'), folder/('run-%d.png' % run))
                results.append({'run': run, 'result': 'passed', 'window': layout['window']})
            finally:
                if process.poll() is None: process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
                print('OWN_TEST_PROCESS_EXITED', process.pid, process.returncode)
    (folder/'result.json').write_text(json.dumps(results, indent=2))
    print('V4_LAYOUT_REGRESSION_PASS', folder)
    print(json.dumps(results))


if __name__ == '__main__': main()
