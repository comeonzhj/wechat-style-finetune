#!/bin/bash
set -u
SELF="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/公众号文风微调"

if [ ! -f "$DEST/app/server.py" ]; then
  echo "首次运行：正在把程序安装到「应用支持」目录…"
  mkdir -p "$DEST"
  cp -R "$SELF/project/." "$DEST/"
  chmod +x "$DEST/启动.command" "$DEST/setup.sh" "$DEST/start.sh" 2>/dev/null || true
fi
cd "$DEST" || exit 1
exec ./启动.command
