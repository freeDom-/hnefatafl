#!/usr/bin/env bash
# Community Scripts-style default/advanced workflow, implemented with native pct.
# No third-party shell code is copied or fetched at runtime.
set -Eeuo pipefail
set +x
umask 077
if [[ $EUID -ne 0 ]]; then
  echo 'Run on the Proxmox VE host as root.' >&2
  exit 1
fi
for command in pct pveam pvesh pvesm pveversion ip python3 tar gzip awk sort tail mktemp rm tee dirname bash; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command (run on Proxmox VE)." >&2; exit 1; }
done
pveversion >/dev/null || { echo 'Cannot verify the Proxmox VE host.' >&2; exit 1; }
stage='Validating host configuration'
ct_id=
temporary=
created=0
cleanup() { if [[ -n $temporary ]]; then rm -rf -- "$temporary"; fi; }
failure() {
  local status=$1
  echo "$stage failed (exit $status). No containers or storage will be destroyed." >&2
  if [[ $stage == 'Creating LXC' ]]; then
    echo "Proxmox host provisioning failed before application installation. Retain the partial state for diagnosis." >&2
    echo "Inspect: pct config $ct_id; pvesm status" >&2
    if [[ -f $temporary/create.log ]] && [[ $(<"$temporary/create.log") == *'Permission denied'* ]]; then
      echo "Template extraction or storage access was denied on the Proxmox host." >&2
      echo "Inspect: namei -l /var/lib/lxc/$ct_id/rootfs; findmnt -T /var/lib/lxc" >&2
    fi
  elif [[ $created == 1 ]]; then
    echo "Container $ct_id is retained. Inspect: pct status $ct_id; pct config $ct_id" >&2
    if [[ $stage == 'Installing Hnefatafl' || $stage == 'Verifying application service' ]]; then
      echo "Inspect: pct exec $ct_id -- systemctl status hnefatafl --no-pager" >&2
      echo "Logs: pct exec $ct_id -- journalctl -u hnefatafl -n 100 --no-pager" >&2
      echo "Bootstrap retained at /root/hnefatafl-bootstrap when installation fails. Enter with pct enter $ct_id; inspect /etc/hnefatafl/transaction.json before retrying or using hnefatafl-update --recover." >&2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'failure $?' ERR
trap 'echo "Installation interrupted." >&2; failure 130' INT
trap 'echo "Installation interrupted." >&2; failure 143' TERM
script_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
read -r -p 'Installation [default/advanced] (default): ' mode
[[ ${mode:-default} == default || $mode == advanced ]] || { echo 'Choose default or advanced.' >&2; exit 1; }
ct_id=$(pvesh get /cluster/nextid)
cpu=4; ram=4096; disk=20; storage=local-lvm; template_storage=local; bridge=vmbr0; network=dhcp; gateway=
if [[ ${mode:-default} == advanced ]]; then
  read -r -p "Container ID [$ct_id]: " value; ct_id=${value:-$ct_id}
  read -r -p "CPU cores [$cpu]: " value; cpu=${value:-$cpu}
  read -r -p "Memory MiB [$ram]: " value; ram=${value:-$ram}
  read -r -p "Disk GiB [$disk]: " value; disk=${value:-$disk}
  read -r -p "Container storage [$storage]: " value; storage=${value:-$storage}
  read -r -p "Template storage [$template_storage]: " value; template_storage=${value:-$template_storage}
  read -r -p "Bridge [$bridge]: " value; bridge=${value:-$bridge}
  read -r -p 'IPv4 CIDR [dhcp]: ' value; network=${value:-dhcp}
  if [[ $network != dhcp ]]; then read -r -p 'Gateway IPv4: ' gateway; fi
fi
for value in "$ct_id" "$cpu" "$ram" "$disk"; do [[ $value =~ ^[1-9][0-9]{0,8}$ ]] || { echo 'Invalid numeric resource value (no leading zeros; at most nine digits).' >&2; exit 1; }; done
for value in "$storage" "$template_storage" "$bridge"; do [[ $value =~ ^[A-Za-z][A-Za-z0-9_.-]*$ && ${#value} -le 64 ]] || { echo 'Invalid storage/bridge identifier.' >&2; exit 1; }; done
[[ $ct_id -ge 100 && $cpu -ge 1 && $ram -ge 2048 && $disk -ge 12 ]] || { echo 'Resources below supported minimum.' >&2; exit 1; }
[[ $cpu -le 8192 && ${#bridge} -le 15 ]] || { echo 'CPU cores must be at most 8192; bridge names at most 15 characters.' >&2; exit 1; }
if [[ $network != dhcp ]]; then
  python3 - "$network" "$gateway" <<'PY'
import ipaddress, re, sys
try:
    if not re.fullmatch(r'[0-9.]+/[0-9]{1,2}', sys.argv[1]):
        raise ValueError('numeric CIDR prefix is required')
    interface = ipaddress.IPv4Interface(sys.argv[1])
    gateway = ipaddress.IPv4Address(sys.argv[2])
    if interface.ip.is_unspecified or interface.ip.is_multicast or gateway.is_unspecified or gateway.is_multicast:
        raise ValueError('use unicast addresses')
except ValueError as error:
    raise SystemExit(f'Invalid static IPv4 configuration: {error}') from None
PY
fi
resources=$(pvesh get /cluster/resources --type vm --output-format json)
printf '%s' "$resources" | python3 -c 'import json,sys; ids={int(v["vmid"]) for v in json.load(sys.stdin)}; sys.exit("Container/VM ID already exists; choose an unused ID." if int(sys.argv[1]) in ids else 0)' "$ct_id"
ip link show dev "$bridge" >/dev/null
for content in rootdir vztmpl; do
  pool=$storage
  [[ $content != vztmpl ]] || pool=$template_storage
  pool_status=$(pvesm status --storage "$pool" --content "$content")
  printf '%s\n' "$pool_status"
  if ! printf '%s\n' "$pool_status" | awk -v pool="$pool" '$1 == pool && $3 == "active" {found=1} END {exit !found}'; then
    echo "Validating host configuration failed: storage $pool is unavailable or cannot store $content. Inspect: pvesm status" >&2
    exit 1
  fi
  printf '%s\n' "$pool_status" | awk -v pool="$pool" '$1 == pool {sub(/%$/, "", $7); if ($7+0 >= 90) print "WARNING: actual storage usage is at least 90% on " pool "; inspect free capacity before continuing."}' >&2
done
echo 'Storage figures above report actual usage. LVM-thin virtual disk allocations can exceed physical capacity; monitor data/metadata usage and keep free capacity for builds, training and backups.'
echo 'Read-only thin-pool diagnostic (if LVM-thin is used): lvs -a -o vg_name,lv_name,lv_size,data_percent,metadata_percent'
temporary=$(mktemp -d -t hnefatafl-pve-install.XXXXXXXX)
stage='Refreshing Debian template catalog'
pveam update
stage='Selecting Debian template'
template=$(pveam available --section system | awk '$2 ~ /^debian-13-standard_.*amd64.tar.zst$/ {print $2}' | sort -V | tail -1)
[[ -n $template ]] || { echo 'No Debian 13 amd64 standard template available.' >&2; exit 1; }
stage='Downloading Debian template'
# Reuse an existing cached template to avoid unnecessary host disk consumption.
cached=$(pveam list "$template_storage")
if ! printf '%s\n' "$cached" | awk -v name="$template_storage:vztmpl/$template" '$1 == name {found=1} END {exit !found}'; then
  pveam download "$template_storage" "$template"
fi
net="name=eth0,bridge=$bridge,ip=$network"
[[ -z $gateway ]] || net+=",gw=$gateway"
stage='Creating LXC'
# Proxmox creates staging directories with the inherited umask. Its mapped
# container root must traverse them; keep 077 for our private files, but use the
# normal host mask for provisioning. This never changes existing permissions.
if (umask 022; exec pct create "$ct_id" "$template_storage:vztmpl/$template" --hostname hnefatafl --unprivileged 1 --cores "$cpu" --memory "$ram" --swap 1024 --rootfs "$storage:$disk" --net0 "$net" --onboot 1 --features nesting=0) 2>&1 | tee "$temporary/create.log"; then
  created=1
else
  status=$?
  failure "$status"
fi
stage='Starting LXC'
(umask 022; exec pct start "$ct_id")
stage='Transferring bootstrap'
# Credentials are entered directly on the container console, never stored on PVE.
tar -czf "$temporary/bootstrap.tar.gz" -C "$script_root" scripts/linux scripts/proxmox/install hnefatafl/deployment.py
pct push "$ct_id" "$temporary/bootstrap.tar.gz" /root/hnefatafl-bootstrap.tar.gz --perms 0600
pct exec "$ct_id" -- mkdir -p /root/hnefatafl-bootstrap
pct exec "$ct_id" -- tar -xzf /root/hnefatafl-bootstrap.tar.gz -C /root/hnefatafl-bootstrap
pct exec "$ct_id" -- rm /root/hnefatafl-bootstrap.tar.gz
echo "Container $ct_id provisioned. Its attached console will request repository, token and administrator credentials."
stage='Installing Hnefatafl'
pct exec "$ct_id" -- bash /root/hnefatafl-bootstrap/scripts/proxmox/install/hnefatafl-install.sh "$@"
stage='Verifying application service'
pct exec "$ct_id" -- systemctl is-enabled hnefatafl
pct exec "$ct_id" -- systemctl is-active hnefatafl
pct exec "$ct_id" -- python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, "/usr/local/lib/hnefatafl"); from deployment import Deployment, protected_json; Deployment(protected_json(Path("/etc/hnefatafl/deployment.json"))).probe()'
pct exec "$ct_id" -- python3 -c 'import json; from pathlib import Path; print("Installed release: " + json.loads(Path("/etc/hnefatafl/installed.json").read_text())["version"]); print("Application port: " + str(json.loads(Path("/etc/hnefatafl/deployment.json").read_text())["port"]))'
stage='Cleaning container bootstrap'
pct exec "$ct_id" -- rm -rf /root/hnefatafl-bootstrap
echo 'Hnefatafl installation verified: release readiness and authentication probes passed; service enabled and active.'
echo "LXC container ID: $ct_id | Hostname: hnefatafl | Unprivileged: yes | Autostart: yes"
pct exec "$ct_id" -- hostname -I
echo "Service: hnefatafl.service | Enter: pct enter $ct_id"
echo 'Configuration: /etc/hnefatafl | Persistent data: /var/lib/hnefatafl'
echo "Update: pct exec $ct_id -- hnefatafl-update | Backup: pct exec $ct_id -- hnefatafl-backup"
echo "Logs: pct exec $ct_id -- journalctl -u hnefatafl -f"
echo 'Use the internal container IP above with the configured application port. Public HTTPS access requires your existing reverse proxy, DNS and firewall configuration.'
