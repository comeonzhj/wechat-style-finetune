#!/usr/bin/env bash
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
echo "文风工坊 → http://127.0.0.1:${PORT}"
( sleep 1.2; command -v open >/dev/null && open "http://127.0.0.1:$PORT" ) &
exec python3 app/server.py --port "$PORT"
