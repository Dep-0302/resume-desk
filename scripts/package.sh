#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
python3 scripts/check-release.py
for app in dist/断点复原浮窗.app; do
  /usr/bin/codesign --verify --deep --strict "$app"
done
version=$(<VERSION)
architecture=$(uname -m)
archive="ResumeDesk-${version}-macOS-${architecture}-preview.zip"
stage=$(mktemp -d "${TMPDIR:-/tmp}/resume-desk-release.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
mkdir -p "$stage/ResumeDesk"
ditto dist/断点复原浮窗.app "$stage/ResumeDesk/断点复原浮窗.app"
cp README.md NOTICE.md "$stage/ResumeDesk/"
ditto -c -k --norsrc --keepParent "$stage/ResumeDesk" "dist/$archive"
(cd dist && shasum -a 256 "$archive" > SHA256SUMS.txt)
print "dist/$archive"
