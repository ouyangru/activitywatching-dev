#!/usr/bin/env bash
set -euo pipefail

app_dir="${1:-${HOME}/.local/share/activity-timeline}"
cd "${app_dir}"
export ACTIVITYWATCH_DB_PATH="${ACTIVITYWATCH_DB_PATH:-${app_dir}/backend/data/activitywatch.db}"
export ACTIVITYWATCH_TIMEZONE="${ACTIVITYWATCH_TIMEZONE:-Asia/Shanghai}"
exec .venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8765

