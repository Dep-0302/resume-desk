#!/usr/bin/env python3
"""Check source privacy boundaries and self-contained production bundles."""
from pathlib import Path
import plistlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.build', '.swiftpm', 'dist', '__pycache__'}
FORBIDDEN = {'development.json', 'state.json', 'resume-desk-state.json',
             'resume-desk-config.json', 'latest-report.json', 'panel-ui.json',
             'ui-diagnostics.json'}
errors = []

def inspect(path, relative):
    if path.is_symlink():
        errors.append(f'Symlink must not be published: {relative}')
        return
    if not path.is_file():
        return
    if path.name in FORBIDDEN or path.suffix in {'.db', '.sqlite', '.sqlite3', '.jsonl'}:
        errors.append(f'Runtime/source record must not be published: {relative}')
    data = path.read_bytes()
    # Check any user's home, including developer paths when CI runs as another user.
    if re.search(rb"/(?:Users|home)/[^\s/\x00\"'<>]+/", data):
        errors.append(f'Absolute home path leaked into {relative}')
    if re.search(rb'01a0[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', data):
        errors.append(f'Personal task identifier found in {relative}')

source_names = subprocess.check_output(
    ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT
).decode().split('\0')
for name in set(source_names) - {''}:
    rel = Path(name)
    allowed_roots = {'Recovery', 'scripts', 'docs', '.github', '.gitignore', 'AGENTS.md', 'CHANGELOG.md', 'NOTICE.md', 'README.md', 'VERSION', 'build.sh', '.agents', 'INSTALL_AGENT.md'}
    if rel.parts[0] == '.agents' and not name.startswith('.agents/skills/resume-desk/'):
        errors.append(f'Unexpected project skill: {name}')
    if rel.parts[0] not in allowed_roots:
        errors.append(f'File outside classic-panel release scope: {name}')
    if any(part in SKIP or part.startswith('Runtime') for part in rel.parts[:-1]):
        errors.append(f'Generated/private directory was added to Git: {name}')
    inspect(ROOT / rel, rel)

version = (ROOT / 'VERSION').read_text().strip()
if sorted(path.name for path in (ROOT / 'dist').glob('*.app')) != ['断点复原浮窗.app']:
    errors.append('dist must contain exactly the classic floating panel App')
for name, executable in [('断点复原浮窗.app', 'FloatingRecoveryPanel')]:
    app = ROOT / 'dist' / name
    if not app.is_dir():
        errors.append(f'Missing release application: {name}')
        continue
    allowed = {'Contents/Info.plist', f'Contents/MacOS/{executable}',
               'Contents/Resources/AppIcon.icns', 'Contents/Resources/Recovery/recovery.py',
               'Contents/_CodeSignature/CodeResources'}
    for path in app.rglob('*'):
        inspect(path, path.relative_to(ROOT))
        if path.is_file() and path.relative_to(app).as_posix() not in allowed:
            errors.append(f'Unexpected bundle content: {path.relative_to(ROOT)}')
    try:
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        assert info['CFBundleShortVersionString'] == version
        assert info['CFBundleExecutable'] == executable
        assert (app / 'Contents/MacOS' / executable).stat().st_mode & 0o111
        assert info['LSMinimumSystemVersion'] == '14.0'
        build_info = subprocess.check_output(['xcrun', 'vtool', '-show-build', str(app / 'Contents/MacOS' / executable)], text=True)
        assert re.search(r'\bminos\s+14\.0\b', build_info), 'Mach-O deployment target must match macOS 14.0'
        backend = app / 'Contents/Resources/Recovery/recovery.py'
        assert backend.read_bytes() == (ROOT / 'Recovery/recovery.py').read_bytes()
    except (AssertionError, KeyError, OSError) as error:
        errors.append(f'Bundle contract failed for {name}: {type(error).__name__}')
if errors:
    print('\n'.join(errors), file=sys.stderr)
    sys.exit(1)
print('PASS source privacy boundary, production resources, versions and bundled recovery cores')
