#!/usr/bin/env bash
set -euo pipefail
# One implementation for bootstrap, installed CLI, upgrade and rollback.
# The Python runtime itself must be explicitly prepared by the operator.
exec "${ZHILUME_BOOTSTRAP_PYTHON:-python3}" "$(dirname "$0")/../src/zhilume_worker/releases.py" "$@"
