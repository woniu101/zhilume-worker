#!/usr/bin/env bash
set -euo pipefail
umask 077
# Install a prebuilt wheel, including React static files. Deployment requires no Node.js.
: "${ZHILUME_WHEEL:?Specify the release wheel path}"
: "${ZHILUME_PROGRAM:?Specify a NEW program directory, separate from persistent data}"
[[ ! -e "$ZHILUME_PROGRAM" ]] || { echo 'Program directory exists; use a new versioned directory for upgrade.'; exit 1; }
command -v uv >/dev/null || { echo 'Install uv explicitly first.'; exit 1; }
uv venv --python "${ZHILUME_PYTHON:-3.12}" "$ZHILUME_PROGRAM"
uv pip install --python "$ZHILUME_PROGRAM/bin/python" "$ZHILUME_WHEEL"
echo 'Core installed. No Torch, model download, GPU inference or service startup performed.'
