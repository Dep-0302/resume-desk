#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
[[ $# == 0 ]] || { print -u2 'Usage: zsh build.sh'; exit 2; }
zsh Recovery/FloatingPanel/build.sh
