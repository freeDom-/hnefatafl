#!/usr/bin/env bash
# Trusted main-branch bootstrap; application releases still use the safe updater.
set -Eeuo pipefail
set +x
umask 077

[[ $EUID -eq 0 ]] || { echo 'Run on the Proxmox VE host as root.' >&2; exit 1; }
for command in pct pveam pvesh pveversion curl python3 mktemp rm bash; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command (run on Proxmox VE)." >&2; exit 1; }
done
pveversion >/dev/null || { status=$?; echo 'Cannot verify the Proxmox VE host.' >&2; exit "$status"; }
work=$(mktemp -d -t hnefatafl-pve-bootstrap.XXXXXXXX)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
stage='Downloading installer files'
failure() {
  local status=$1
  echo "$stage failed (exit $status). Temporary bootstrap files will be removed." >&2
  exit "$status"
}
trap 'failure $?' ERR

# This fixed URL needs no redirect. Refuse redirects rather than trusting a new host.
url='https://codeload.github.com/freeDom-/hnefatafl/tar.gz/refs/heads/main'
http_status=$(curl --fail --silent --show-error --retry 3 --proto '=https' \
  --tlsv1.2 --connect-timeout 20 --max-time 180 --max-filesize 134217728 \
  --write-out '%{http_code}' --output "$work/source.tar.gz" "$url")
[[ $http_status == 200 ]] || { echo 'Downloading installer files failed: expected HTTP 200; redirects are refused.' >&2; exit 1; }

stage='Validating bootstrap archive'
python3 - "$work/source.tar.gz" "$work/repo" <<'PY'
import pathlib
import shutil
import sys
import tarfile

archive, destination = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
try:
    if archive.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('compressed archive exceeds 128 MiB')
    with tarfile.open(archive, 'r:gz') as source:
        entries = []
        seen = {}
        total = 0
        for member in source:
            parts = member.name.rstrip('/').split('/')
            if (parts[0] != 'hnefatafl-main'
                    or any(part in {'', '.', '..'} for part in parts)
                    or '\\' in member.name
                    or any(ord(c) < 32 or ord(c) == 127 for c in member.name)
                    or not (member.isfile() or member.isdir())
                    or member.size < 0
                    or member.mode & 0o7000 or member.name.rstrip('/') in seen
                    or (len(parts) == 1 and not member.isdir())):
                raise ValueError('unsafe archive entry')
            seen[member.name.rstrip('/')] = member.isdir()
            total += member.size
            if len(seen) > 20000 or total > 512 * 1024 * 1024:
                raise ValueError('archive exceeds extraction limits')
            entries.append((member, parts[1:]))
        for name in seen:
            parts = name.split('/')
            if any(seen.get('/'.join(parts[:i])) is False for i in range(1, len(parts))):
                raise ValueError('archive file conflicts with directory')
        required = {
            'scripts/proxmox/ct/hnefatafl.sh',
            'scripts/proxmox/install/hnefatafl-install.sh',
            'scripts/linux/install.sh', 'scripts/linux/update.sh', 'scripts/linux/backup.sh',
            'hnefatafl/deployment.py',
        }
        files = {'/'.join(parts) for member, parts in entries if member.isfile()}
        if not required <= files:
            raise ValueError('missing required installer components')
        # Validate the entire archive before writing anything. Never restore owners,
        # permissions or links from the archive; the private directory starts empty.
        destination.mkdir(mode=0o700)
        for member, parts in entries:
            if not parts:
                if not member.isdir():
                    raise ValueError('archive root must be a directory')
                continue
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source.extractfile(member) as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
except (OSError, EOFError, ValueError, tarfile.TarError) as error:
    raise SystemExit(f'Validating bootstrap archive failed: {error}') from None
PY
stage='Validating installer shell syntax'
for script in "$work/repo/scripts/proxmox/ct/hnefatafl.sh" \
    "$work/repo"/scripts/proxmox/install/*.sh "$work/repo"/scripts/linux/*.sh; do
  bash -n "$script"
done
stage='Proxmox installation'
cd -- "$work/repo"
bash scripts/proxmox/ct/hnefatafl.sh "$@"
