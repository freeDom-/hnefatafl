#!/usr/bin/env bash
# Run inside the container from a reviewed local checkout; no remote eval.
set -euo pipefail
script_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
exec bash "$script_root/scripts/linux/install.sh" "$@"
