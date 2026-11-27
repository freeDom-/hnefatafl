#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 -I -B /usr/local/lib/hnefatafl/deployment.py "$@"
