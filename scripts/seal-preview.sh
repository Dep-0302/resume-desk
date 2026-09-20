#!/bin/zsh
# Ad-hoc integrity seal for NEW project build artifacts only; no identity certificate.
set -euo pipefail
cd "${0:A:h:h}"
python3 scripts/check-release.py
for app in dist/断点复原浮窗.app; do
  [[ -d "$app" && ! -L "$app" ]] || { print -u2 "Invalid build artifact: $app"; exit 1; }
  /usr/bin/codesign --force --sign - "$app"
  /usr/bin/codesign --verify --deep --strict "$app"
done
print 'Ad-hoc integrity seals verified; these are not Developer ID signatures or notarization.'
