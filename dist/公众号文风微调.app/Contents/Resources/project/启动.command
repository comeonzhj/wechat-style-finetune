#!/bin/bash
# 公众号文风微调 · 双击启动
# 第一次会自动准备环境（约 2 分钟），之后每次都是直接打开。

cd "$(dirname "$0")" || exit 1
PORT="${CW_PORT:-8765}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }
fail() {
  printf "\n\033[31m%s\033[0m\n\n" "$1"
  osascript -e "display dialog \"$1\" buttons {\"好\"} default button 1 with title \"启动失败\" with icon caution" 2>/dev/null
  exit 1
}

printf "\033[1m\n公众号文风微调\033[0m\n"
printf "自己的文章，训一个会说你的话的模型\n"
printf "──────────────────────────────────────────\n"

# 1. 系统检查
if [ "$(uname -s)" != "Darwin" ]; then
  fail "这个启动器只用于 macOS。其它系统请用命令行运行 python3 app/server.py"
fi
if [ "$(uname -m)" != "arm64" ]; then
  fail "训练需要 Apple Silicon（M1/M2/M3/M4）。Intel Mac 可以做语料，但不能训练。"
fi
if ! command -v python3 >/dev/null 2>&1; then
  fail "没有找到 python3。请在 python.org/downloads 下载安装后，再双击一次。"
fi

# 2. 环境（只在第一次做）
if [ ! -d .venv-mlx ]; then
  say "第一次使用，正在准备环境（约 2 分钟，走国内镜像）…"
  ./setup.sh || fail "环境准备失败。可以打开终端，进入这个文件夹手动运行 ./setup.sh 看详细报错。"
else
  say "环境已就绪"
fi

# 3. 启动
say "正在打开 http://127.0.0.1:${PORT}"
printf "（关掉这个窗口，或者按 Control+C 就停止）\n\n"
if [ "${CW_NO_OPEN:-}" != "1" ]; then
  ( sleep 1.5; open "http://127.0.0.1:${PORT}" ) &
fi
exec python3 app/server.py --port "${PORT}"
