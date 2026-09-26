#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
: "${ZHILUME_SERVER:?请设置 Worker 能访问的 Server HTTPS 地址}"
args=(--server "$ZHILUME_SERVER" --state "${ZHILUME_STATE:-.state}" --name "${ZHILUME_WORKER_NAME:-$(hostname)}")
if [[ "${ZHILUME_ENABLE_IMAGE:-0}" == '1' ]]; then
  : "${ZHILUME_COMFY_CONFIG:?图片执行必须指定专用 ComfyUI 配置}"
  args+=(--enable-image-execution --comfy-config "$ZHILUME_COMFY_CONFIG")
fi
exec uv run --frozen --no-sync zhilume-worker "${args[@]}"
