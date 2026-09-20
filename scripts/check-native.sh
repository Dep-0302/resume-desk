#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
export PYTHONDONTWRITEBYTECODE=1
# Requires a logged-in macOS graphical session. Uses synthetic, isolated windows.
for test in 拖动透明状态回归 接续悬停回归 布局回归; do
  python3 -B "Recovery/FloatingPanel/Tests/$test.py"
done
