#!/usr/bin/env bash
# 文风工坊 · 一键安装
# 只装两个东西：本工具零依赖（纯标准库），MLX 用来训练。
set -euo pipefail
cd "$(dirname "$0")"

MIRROR="${MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

echo "== 1/4 检查环境 =="
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "✗ 训练功能只支持 macOS（Apple Silicon）。其他系统可以做语料，但不能训练。"
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "✗ 需要 Apple Silicon（M1/M2/M3/M4）。Intel Mac 不支持 MLX。"
  exit 1
fi
python3 --version

echo "== 2/4 创建虚拟环境 .venv-mlx =="
if [[ ! -d .venv-mlx ]]; then
  python3 -m venv .venv-mlx
fi
PIP=".venv-mlx/bin/pip"
"$PIP" install -q -U pip -i "${MIRROR}"

echo "== 3/4 安装 mlx-lm，镜像=${MIRROR} =="
"$PIP" install -i "${MIRROR}" --prefer-binary --timeout 60 mlx-lm

echo "== 4/4 校验 =="
.venv-mlx/bin/python - <<'PY'
import mlx.core, mlx_lm
print("✓ mlx", mlx.core.__version__, "| mlx-lm 已就绪")
PY

mkdir -p my-articles data/exports data/originals adapters logs
cat > .env <<EOF
HF_ENDPOINT=$HF_ENDPOINT
EOF
echo
echo "✓ 安装完成。启动：./start.sh （或 python3 app/server.py）"
echo "  模型默认从 ${HF_ENDPOINT} 下载，国内可直接用。"
