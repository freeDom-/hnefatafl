#!/usr/bin/env bash
# Delegate every operation to the installed, verified stable-release updater.
set -Eeuo pipefail
set +x
umask 077

fail() { echo "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run on the Proxmox VE host as root.'
for command in pct pveversion; do
  command -v "$command" >/dev/null || fail "Missing required command: $command (run on Proxmox VE)."
done
pveversion >/dev/null || fail 'Cannot verify the Proxmox VE host.'
ctid=${HNEFATAFL_CTID:-}
if [[ ${1:-} != -* && $# -gt 0 ]]; then ctid=$1; shift; fi
[[ $ctid =~ ^[1-9][0-9]{2,8}$ ]] || fail 'Supply a valid CT ID (100–999999999), or set HNEFATAFL_CTID.'
pct config "$ctid" >/dev/null || fail "Container $ctid does not exist or its configuration cannot be read. Inspect: pct list"
state=$(pct status "$ctid") || fail "Cannot read container $ctid status. Inspect: pct status $ctid"
[[ $state == 'status: running' ]] || fail "Container $ctid is stopped. Inspect: pct status $ctid; start it explicitly before updating."
# Check configuration and installed code, but allow --recover when installed.json
# is absent following an interrupted first installation.
pct exec "$ctid" -- test -f /etc/hnefatafl/deployment.json || fail "Hnefatafl configuration is missing in container $ctid. Inspect: pct enter $ctid"
pct exec "$ctid" -- test -f /usr/local/lib/hnefatafl/deployment.py || fail "Hnefatafl deployment module is missing in container $ctid. Inspect: pct enter $ctid"
pct exec "$ctid" -- test -x /usr/local/bin/hnefatafl-update || fail "Hnefatafl updater is missing in container $ctid. Inspect: pct enter $ctid"
if pct exec "$ctid" -- /usr/local/bin/hnefatafl-update "$@"; then
  exit 0
else
  status=$?
  echo "Hnefatafl updater failed in container $ctid (exit $status)." >&2
  echo "Inspect: pct exec $ctid -- journalctl -u hnefatafl -n 100 --no-pager" >&2
  echo "Inspect /etc/hnefatafl/transaction.json inside the container before using --recover or --rollback." >&2
  exit "$status"
fi
