#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
repo_root=${script_dir:h:h}
source_file="$script_dir/Sources/FloatingRecoveryApp.swift"
plist_file="$script_dir/Info.plist"
iconset_dir="$script_dir/Assets/AppIcon.iconset"
icon_builder="$script_dir/Tools/make_icns.py"
recovery_source="$repo_root/Recovery/recovery.py"
app_path="$repo_root/dist/断点复原浮窗.app"
module_cache="${MODULE_CACHE:-${TMPDIR:-/tmp}/resume-desk-floating-panel-module-cache}"
target_triple="$(uname -m)-apple-macosx14.0"
stage_root="$(mktemp -d "${TMPDIR:-/tmp}/resume-desk-floating-panel.XXXXXX")"
stage_app="$stage_root/断点复原浮窗.app"
binary_path="$stage_app/Contents/MacOS/FloatingRecoveryPanel"
icon_path="$stage_app/Contents/Resources/AppIcon.icns"
recovery_path="$stage_app/Contents/Resources/Recovery/recovery.py"

cleanup() {
  rm -rf "$stage_root"
}
trap cleanup EXIT

[[ -f "$source_file" ]] || { print -u2 "缺少 Swift 源文件：$source_file"; exit 1; }
[[ -f "$plist_file" ]] || { print -u2 "缺少 Info.plist：$plist_file"; exit 1; }
[[ -d "$iconset_dir" ]] || { print -u2 "缺少 App 图标资源：$iconset_dir"; exit 1; }
[[ -f "$icon_builder" ]] || { print -u2 "缺少 App 图标打包器：$icon_builder"; exit 1; }
[[ -f "$recovery_source" ]] || { print -u2 "缺少恢复后端：$recovery_source"; exit 1; }

mkdir -p "$stage_app/Contents/MacOS" "$stage_app/Contents/Resources/Recovery" "$module_cache"
xcrun swiftc \
  "$source_file" \
  -parse-as-library \
  -o "$binary_path" \
  -target "$target_triple" \
  -gnone \
  -debug-prefix-map "$repo_root=/ResumeDesk" \
  -framework AppKit \
  -framework Foundation \
  -Xlinker -S \
  -module-cache-path "$module_cache"
cp "$plist_file" "$stage_app/Contents/Info.plist"
/usr/bin/python3 "$icon_builder" "$iconset_dir" "$icon_path"
cp "$recovery_source" "$recovery_path"
chmod 755 "$binary_path"

mkdir -p "$repo_root/dist"
rm -rf "$app_path"
mv "$stage_app" "$app_path"

print "已构建：$app_path"
