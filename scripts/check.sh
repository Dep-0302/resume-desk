#!/bin/zsh
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
cd "${0:A:h:h}"
python3 -m unittest discover -s Recovery -p 'test_*.py'
zsh build.sh
python3 scripts/check-integration.py
python3 scripts/check-release.py
