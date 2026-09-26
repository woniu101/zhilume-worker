#!/usr/bin/env bash
# Dedicated local ComfyUI backend. No model downloads or source updates.
set -euo pipefail
: "${ZHILUME_COMFY_ROOT:?指定专用 ComfyUI 目录}"
: "${ZHILUME_COMFY_PYTHON:?指定镜像中已安装 Torch/CUDA 的 Python 绝对路径}"
[[ -f "$ZHILUME_COMFY_ROOT/main.py" ]] || { echo 'ComfyUI main.py 不存在' >&2; exit 1; }
[[ -x "$ZHILUME_COMFY_PYTHON" ]] || { echo 'ComfyUI Python 不可执行' >&2; exit 1; }
cd -- "$ZHILUME_COMFY_ROOT"
# The verified Qwen graphs use built-in nodes only. PyTorch SDPA avoids the
# base image's xFormers mask/kernel incompatibility on RTX 5090 (sm_120).
exec "$ZHILUME_COMFY_PYTHON" -u main.py --listen 127.0.0.1 \
  --port "${ZHILUME_COMFY_PORT:-8188}" --use-pytorch-cross-attention \
  --disable-xformers --disable-all-custom-nodes
