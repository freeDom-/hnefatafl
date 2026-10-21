#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
if [[ ! -x .venv/bin/python || ! -d frontend/node_modules ]]; then
  echo 'Run make install first.' >&2
  exit 1
fi
.venv/bin/python -m uvicorn hnefatafl.server:app --host 127.0.0.1 --port 8000 --reload &
api_pid=$!
cleanup() { kill "$api_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
npm --prefix frontend run dev
