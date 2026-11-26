#!/usr/bin/env bash
# Native Debian installer. Run a reviewed authenticated checkout for private repos.
set -Eeuo pipefail
set +x
umask 077
stage='Checking Debian installation prerequisites'
failure() {
  local status=$1
  echo "$stage failed (exit $status). Inspect /etc/hnefatafl/transaction.json and journalctl -u hnefatafl -n 100 --no-pager; configuration and application data are retained." >&2
  exit "$status"
}
trap 'failure $?' ERR
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
refresh_only=0
forwarded=()
for argument in "$@"; do
  case $argument in
    --refresh-updater-only) refresh_only=1 ;;
    *) forwarded+=("$argument") ;;
  esac
done
if ((${#forwarded[@]})); then
  set -- "${forwarded[@]}"
else
  set --
fi
install_deployment_utilities() {
  stage='Installing the verified deployment utility'
  python3 - "$script_root" <<'INSTALLER_PY'
import ast, json, os, pathlib, shutil, sys, time

root = pathlib.Path(sys.argv[1])
destination_root = pathlib.Path("/usr/local/lib/hnefatafl")
support_destination = destination_root / "updater_support"
commands = (
    pathlib.Path("/usr/local/bin/hnefatafl-update"),
    pathlib.Path("/usr/local/bin/hnefatafl-backup"),
)
support_modules = (
    "online_migrations",
    "league_migrations",
    "league_policy",
    "league_recovery_migrations",
    "rating",
)
files = {
    root / "hnefatafl/deployment.py": destination_root / "deployment.py",
    root / "scripts/linux/update.sh": commands[0],
    root / "scripts/linux/backup.sh": commands[1],
}
for name in support_modules:
    files[root / f"hnefatafl/{name}.py"] = support_destination / f"{name}.py"

payload = {}
for source, destination in files.items():
    if not source.is_file() or source.is_symlink():
        raise SystemExit(f"Deployment utility source is missing or unsafe: {source}")
    content = source.read_bytes()
    if source.suffix == ".py":
        compile(content.decode("utf-8"), str(source), "exec")
    payload[source] = content

# Refuse an accidental protocol downgrade: an older utility cannot interpret the
# current transaction or retention metadata.
source_text = payload[root / "hnefatafl/deployment.py"].decode("utf-8")
abi = None
for node in ast.parse(source_text).body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "UPDATER_ABI":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                    abi = node.value.value
if abi is None or abi < 1:
    raise SystemExit("The deployment utility does not declare a supported protocol version")
state_path = pathlib.Path("/etc/hnefatafl/updater.json")
installed_abi = 1
if state_path.is_file() and not state_path.is_symlink():
    try:
        installed_abi = int(json.loads(state_path.read_text()).get("abi", 1))
    except (ValueError, TypeError):
        installed_abi = 1
if abi < installed_abi:
    raise SystemExit(
        f"Refusing to downgrade the deployment utility protocol from {installed_abi} to {abi}"
    )

destination_root.mkdir(parents=True, exist_ok=True)
os.chmod(destination_root, 0o755)
for command in commands:
    command.parent.mkdir(parents=True, exist_ok=True)

token = os.urandom(6).hex()
journal = destination_root / ".refresh.json"
entries = []
for source, destination in files.items():
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries.append(
        {
            "destination": str(destination),
            "mode": 0o755 if destination in commands else 0o644,
            "temporary": str(destination.with_name(destination.name + ".new-" + token)),
            "previous": str(destination.with_name(destination.name + ".previous-" + token)),
            "replaced": False,
            "had_previous": False,
        }
    )
journal.write_text(json.dumps({"schema": 1, "phase": "preparing", "entries": entries}))
os.chmod(journal, 0o600)
try:
    sources = list(files)
    for entry, source in zip(entries, sources):
        destination = pathlib.Path(entry["destination"])
        if destination.exists():
            shutil.copyfile(destination, entry["previous"])
            os.chmod(entry["previous"], os.stat(destination).st_mode & 0o777)
            os.chown(entry["previous"], 0, 0)
            entry["had_previous"] = True
        descriptor = os.open(entry["temporary"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload[source])
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(entry["temporary"], entry["mode"])
        os.chown(entry["temporary"], 0, 0)
    journal.write_text(json.dumps({"schema": 1, "phase": "prepared", "entries": entries}))
    for entry in entries:
        os.replace(entry["temporary"], entry["destination"])
        entry["replaced"] = True
        journal.write_text(json.dumps({"schema": 1, "phase": "replacing", "entries": entries}))
        descriptor = os.open(str(pathlib.Path(entry["destination"]).parent), os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    journal.write_text(json.dumps({"schema": 1, "phase": "committed", "entries": entries}))
except BaseException:
    for entry in entries:
        destination = pathlib.Path(entry["destination"])
        previous = pathlib.Path(entry["previous"])
        temporary = pathlib.Path(entry["temporary"])
        if entry.get("replaced") and entry.get("had_previous") and previous.exists():
            os.replace(previous, destination)
        elif entry.get("replaced"):
            destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        previous.unlink(missing_ok=True)
    journal.unlink(missing_ok=True)
    raise
for entry in entries:
    pathlib.Path(entry["previous"]).unlink(missing_ok=True)
journal.unlink(missing_ok=True)
state_path.write_text(
    json.dumps({"schema": 1, "abi": abi, "version": "installer", "refreshed_at": time.time()})
)
os.chmod(state_path, 0o600)
INSTALLER_PY
}
verify_service() {
  stage='Enabling and verifying systemd service'
  systemctl enable hnefatafl.service
  systemctl is-enabled hnefatafl
  systemctl is-active hnefatafl
}
configure_root_cli_path() {
  stage='Configuring root shell PATH'
  # Login shells read /etc/profile.d; pct enter / interactive non-login
  # Bash shells read /root/.bashrc. Keep existing PATH entries.
  install -d -m 755 /etc/profile.d
  cat > /etc/profile.d/hnefatafl-path.sh <<'EOF'
# Hnefatafl maintenance commands live in /usr/local/bin.
case ":${PATH:-}:" in
  *:/usr/local/bin:*) ;;
  *) PATH="/usr/local/bin${PATH:+:$PATH}" ;;
esac
export PATH
EOF
  chmod 644 /etc/profile.d/hnefatafl-path.sh
  if [[ ! -f /root/.bashrc ]]; then
    install -m 600 /dev/null /root/.bashrc
  fi
  if ! grep -Fxq '. /etc/profile.d/hnefatafl-path.sh' /root/.bashrc; then
    printf '\n# Hnefatafl maintenance commands in interactive root shells.\n. /etc/profile.d/hnefatafl-path.sh\n' >> /root/.bashrc
  fi
}
verify_cli_commands() {
  stage='Verifying maintenance commands and root shell PATH'
  local name mode
  for name in hnefatafl-update hnefatafl-backup; do
    [[ -f /usr/local/bin/$name && -x /usr/local/bin/$name ]] || {
      echo "Missing or non-executable maintenance command: /usr/local/bin/$name" >&2
      return 1
    }
  done
  # Start fresh root shells with PATH deliberately excluding /usr/local/bin.
  local probe='for name in hnefatafl-update hnefatafl-backup; do
    resolved=$(command -v "$name") || exit 1
    [[ $resolved == "/usr/local/bin/$name" && -x $resolved ]] || exit 1
  done'
  for mode in -lc -ic; do
    if ! env -i HOME=/root USER=root LOGNAME=root TERM=dumb PATH=/usr/bin:/bin \
        /bin/bash "$mode" "$probe" </dev/null 2>/dev/null; then
      echo "Maintenance commands are not discoverable/executable in root Bash $mode" >&2
      return 1
    fi
  done
}

install -d -m 700 /etc/hnefatafl
configure_root_cli_path
if [[ -f /etc/hnefatafl/deployment.json ]]; then
  echo 'Existing configuration and accounts retained. Running the stable-release updater.'
  exec 9>/run/lock/hnefatafl-update.lock
  flock 9
  install_deployment_utilities
  if [[ $refresh_only -eq 1 ]]; then
    verify_cli_commands
    echo 'Deployment utility refreshed. Configuration, installed state, transactions and application data were retained.'
    exit 0
  fi
  stage='Resuming stable-release installation/update'
  if [[ -f /etc/hnefatafl/installed.json ]]; then
    /usr/local/bin/hnefatafl-update "$@"
  else
    /usr/local/bin/hnefatafl-update --initialize "$@"
  fi
  verify_service
  verify_cli_commands
  exit 0
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
stage='Installing runtime dependencies'
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
stage='Installing verified Node.js runtime'
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
stage='Writing production configuration'
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
exec 9>/run/lock/hnefatafl-update.lock
flock 9
install_deployment_utilities
stage='Installing stable release, initializing administrator and verifying application readiness'
/usr/local/bin/hnefatafl-update --initialize "$@"
verify_service
verify_cli_commands
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
