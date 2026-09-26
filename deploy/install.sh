#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo '请先安装 uv，再运行本脚本。'; exit 1; }
# Worker uses its own Python; never modify the image's ComfyUI Python environment.
uv sync --frozen --python 3.12
uv run --no-sync zhilume-prepare
echo 'Worker 安装完成；没有启动 ComfyUI、GPU 或接单进程。'
