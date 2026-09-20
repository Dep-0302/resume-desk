from pathlib import Path
import json,plistlib,shutil,sys,tempfile
app=Path(__file__).resolve().parents[1]; repo=app.parents[1]; sys.path.insert(0,str(app.parent))
from recovery import report,merge_panel_report
root=Path(tempfile.mkdtemp(prefix='resume-desk-six-qa-',dir='/private/tmp'));state_dir=root/'runtime';state_dir.mkdir();(root/'recovery.py').symlink_to(app.parent/'recovery.py')
qa=root/'常驻浮窗/浮窗六项验收.app';shutil.copytree(repo/'dist'/'断点复原浮窗.app',qa)
p=qa/'Contents/Info.plist';v=plistlib.loads(p.read_bytes());v['CFBundleIdentifier']='org.example.resumedesk.six-qa';v['CFBundleDisplayName']='浮窗六项验收';p.write_bytes(plistlib.dumps(v))
s={'threads':{},'sources':{}};n=0
for code,count in [(123,123),(12,12),(7,3)]:
 for k in range(count):
  sid='fa000000-0000-4000-8000-%012d'%n
  s['threads'][sid]={'id':sid,'title':'隔离任务 %d'%n,'cwd':'/demo/%03d-六项验收'%code,'fingerprint':'v1','updated_at':1700000000+n,'evidence':{'question':'继续','answer':'请确认素材','no_reply_after_answer':True},'review_note':{'text':'正文可点击，从这段文字继续。','based_on':'v1','source_thread':sid}}
  n+=1
r=report(s,'manual',limit=138);merge_panel_report(s,r)
(state_dir/'state.json').write_text(json.dumps(s,ensure_ascii=False));(state_dir/'latest-report.json').write_text(json.dumps(r,ensure_ascii=False));(state_dir/'panel-ui.json').write_text(json.dumps({'schema_version':2,'frame':{'x':150,'y':150,'width':452,'height':720}}))
ctx={'root':str(root),'state':str(state_dir),'app':str(qa),'binary':str(qa/'Contents/MacOS/FloatingRecoveryPanel'),'diag':str(root/'six.json')};Path('/tmp/resume-desk-six-context.json').write_text(json.dumps(ctx));print(json.dumps(ctx))
