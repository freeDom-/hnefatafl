#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
if [[ ! -f frontend/dist/index.html ]]; then
  echo 'Build the browser client first: npm --prefix frontend run build' >&2
  exit 1
fi
exec .venv/bin/python -m uvicorn hnefatafl.server:app \
  --host "${HNEFATAFL_HOST:-127.0.0.1}" --port "${HNEFATAFL_PORT:-8000}" --workers 1
