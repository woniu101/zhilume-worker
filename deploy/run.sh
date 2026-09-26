#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
args=(--host "${ZHILUME_HOST:-127.0.0.1}" --port "${ZHILUME_PORT:-4320}" --state "${ZHILUME_STATE:-.state}" --name "${ZHILUME_WORKER_NAME:-$(hostname)}")
if [[ "${ZHILUME_ENABLE_IMAGE:-0}" == '1' ]]; then
  : "${ZHILUME_COMFY_CONFIG:?图片执行必须指定专用 ComfyUI 配置}"
  args+=(--enable-image-execution --comfy-config "$ZHILUME_COMFY_CONFIG")
fi
if [[ "${ZHILUME_ENABLE_SPEECH:-0}" == '1' ]]; then
  : "${ZHILUME_SPEECH_CONFIG:?语音执行必须指定 IndexTTS 配置}"
  args+=(--enable-speech-execution --speech-config "$ZHILUME_SPEECH_CONFIG")
fi
exec uv run --frozen --no-sync zhilume-worker "${args[@]}"
