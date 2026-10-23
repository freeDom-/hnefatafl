#!/usr/bin/env bash
# Native Debian installer. Run a reviewed authenticated checkout for private repos.
set -euo pipefail
set +x
umask 077
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 ]] || { echo 'Debian 13 is required.' >&2; exit 1; }
case $(dpkg --print-architecture) in
  amd64) node_arch=x64 ;;
  arm64) node_arch=arm64 ;;
  *) echo 'Supported architectures: amd64 and arm64.' >&2; exit 1 ;;
esac
script_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
install -d -m 700 /etc/hnefatafl
if [[ -f /etc/hnefatafl/deployment.json ]]; then
  echo 'Existing configuration and accounts retained. Running the stable-release updater.'
  install -m 644 "$script_root/hnefatafl/deployment.py" /usr/local/lib/hnefatafl/deployment.py
  install -m 755 "$script_root/scripts/linux/update.sh" /usr/local/bin/hnefatafl-update
  if [[ -f /etc/hnefatafl/installed.json ]]; then
    exec /usr/local/bin/hnefatafl-update "$@"
  fi
  exec /usr/local/bin/hnefatafl-update --initialize "$@"
fi
read -r -p 'GitHub repository [freeDom-/hnefatafl]: ' repository
repository=${repository:-freeDom-/hnefatafl}
read -r -s -p 'Read-only GitHub token (empty for public repository): ' github_token
echo
read -r -p 'Application port [8000]: ' app_port
app_port=${app_port:-8000}
read -r -p 'Public HTTPS origin (e.g. https://tafl.example.org): ' public_url
read -r -p 'Trusted reverse proxy IPs/CIDRs, comma-separated: ' trusted_proxies
[[ $app_port =~ ^[0-9]+$ && $app_port -ge 1024 && $app_port -le 65535 ]] || { echo 'Port must be 1024–65535.' >&2; exit 1; }
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl python3 python3-venv build-essential libgomp1 xz-utils
id hnefatafl >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/hnefatafl --shell /usr/sbin/nologin hnefatafl
[[ $(id -u hnefatafl) -ne 0 ]] || { echo 'The hnefatafl service account must not have UID 0.' >&2; exit 1; }
install -d -m 755 /opt/hnefatafl /opt/hnefatafl/releases /opt/hnefatafl/tools /usr/local/lib/hnefatafl
install -d -o hnefatafl -g hnefatafl -m 700 /var/lib/hnefatafl
install -d -m 700 /var/lib/hnefatafl/backups
python3 -m venv /opt/hnefatafl/tools
/opt/hnefatafl/tools/bin/pip install 'uv==0.12.23'
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"; unset github_token' EXIT
curl --fail --silent --show-error --proto '=https' --tlsv1.2 'https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt' -o "$temporary/SHASUMS256.txt"
node_file=$(python3 - "$temporary/SHASUMS256.txt" "$node_arch" <<'PY'
import pathlib, re, sys
names=[line.split()[1] for line in pathlib.Path(sys.argv[1]).read_text().splitlines()]
matches=[name for name in names if re.fullmatch(r'node-v22\.[0-9]+\.[0-9]+-linux-'+sys.argv[2]+r'\.tar\.xz',name)]
if len(matches)!=1: raise SystemExit('Cannot resolve a stable Node 22 binary')
print(matches[0])
PY
)
node_version=${node_file#node-}; node_version=${node_version%%-linux-*}
curl --fail --silent --show-error --proto '=https' --tlsv1.2 "https://nodejs.org/dist/${node_version}/${node_file}" -o "$temporary/$node_file"
python3 - "$temporary" "$node_file" <<'PY'
import hashlib, pathlib, sys
root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
expected = dict(line.split()[::-1] for line in (root/'SHASUMS256.txt').read_text().splitlines())[name]
with (root/name).open('rb') as handle:
    actual = hashlib.file_digest(handle, 'sha256').hexdigest()
if actual != expected:
    raise SystemExit('Node.js archive checksum mismatch')
PY
mkdir -p /opt/hnefatafl/tools/node
tar -xJf "$temporary/$node_file" --strip-components=1 -C /opt/hnefatafl/tools/node --no-same-owner
chmod -R a+rX /opt/hnefatafl/tools
# Credentials travel only through a pipe into a root-only JSON file.
printf '%s' "$github_token" | python3 -c 'import json,sys; from pathlib import Path; Path("/etc/hnefatafl/github.json").write_text(json.dumps({"token":sys.stdin.read()}))'
unset github_token
python3 - "$repository" "$app_port" "$public_url" "$trusted_proxies" <<'PY'
import ipaddress, json, pathlib, re, sys
from urllib.parse import urlparse
repo, port, origin, proxies = sys.argv[1:]
repo=repo.removeprefix('https://github.com/').removesuffix('.git').rstrip('/')
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise SystemExit('Invalid repository')
url=urlparse(origin)
if url.scheme!='https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment: raise SystemExit('Public URL must be an HTTPS origin, with no path')
if not proxies: raise SystemExit('Explicit trusted proxy addresses are required')
for proxy in proxies.split(','): ipaddress.ip_network(proxy.strip(),strict=False)
root=pathlib.Path('/etc/hnefatafl')
(root/'deployment.json').write_text(json.dumps({'repository':repo,'port':int(port),'public_url':origin}))
(root/'application.env').write_text(f'HNEFATAFL_ENV=production\nHNEFATAFL_PUBLIC_URL={origin}\nHNEFATAFL_ALLOWED_HOSTS={url.hostname},127.0.0.1,localhost\nHNEFATAFL_TRUSTED_PROXIES={proxies}\nHNEFATAFL_HOST=0.0.0.0\nHNEFATAFL_PORT={port}\n')
PY
chmod 600 /etc/hnefatafl/*.json /etc/hnefatafl/application.env
install -m 644 "$script_root/hnefatafl/deployment.py" /usr/local/lib/hnefatafl/deployment.py
install -m 755 "$script_root/scripts/linux/update.sh" /usr/local/bin/hnefatafl-update
install -m 755 "$script_root/scripts/linux/backup.sh" /usr/local/bin/hnefatafl-backup
/usr/local/bin/hnefatafl-update --initialize "$@"
systemctl enable hnefatafl.service
echo "Application: $public_url (port $app_port)"
python3 - <<'PY'
import json, sqlite3
from pathlib import Path
print('Installed release: ' + json.loads(Path('/etc/hnefatafl/installed.json').read_text())['version'])
with sqlite3.connect('file:/var/lib/hnefatafl/hnefatafl.sqlite3?mode=ro', uri=True) as connection:
    row=connection.execute("SELECT a.username FROM accounts a JOIN auth_meta m ON a.id=m.value WHERE m.key='bootstrapped'").fetchone()
print('Administrator: ' + (row[0] if row else 'see local account configuration'))
PY
echo 'Service: hnefatafl.service | Config: /etc/hnefatafl | Data: /var/lib/hnefatafl'
echo 'Update: hnefatafl-update | Logs: journalctl -u hnefatafl -f'
echo 'Backup: hnefatafl-backup (then copy verified backups off the container).'
