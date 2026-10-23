#!/usr/bin/env bash
# Community Scripts-style default/advanced workflow, implemented with native pct.
# No third-party shell code is copied or fetched at runtime.
set -euo pipefail
set +x
umask 077
if [[ $EUID -ne 0 ]] || ! command -v pct >/dev/null || ! command -v pveam >/dev/null; then
  echo 'Run on the Proxmox VE host as root.' >&2
  exit 1
fi
script_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
read -r -p 'Installation [default/advanced] (default): ' mode
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
for value in "$ct_id" "$cpu" "$ram" "$disk"; do [[ $value =~ ^[0-9]+$ ]] || { echo 'Invalid numeric resource value.' >&2; exit 1; }; done
for value in "$storage" "$template_storage" "$bridge"; do [[ $value =~ ^[A-Za-z0-9_.-]+$ ]] || { echo 'Invalid storage/bridge identifier.' >&2; exit 1; }; done
[[ $ct_id -ge 100 && $cpu -ge 1 && $ram -ge 2048 && $disk -ge 12 ]] || { echo 'Resources below supported minimum.' >&2; exit 1; }
if [[ $network != dhcp ]]; then
  [[ $network =~ ^[0-9.]+/[0-9]+$ && $gateway =~ ^[0-9.]+$ ]] || { echo 'Invalid static IPv4 configuration.' >&2; exit 1; }
fi
pveam update
template=$(pveam available --section system | awk '$2 ~ /^debian-13-standard_.*amd64.tar.zst$/ {print $2}' | sort -V | tail -1)
[[ -n $template ]] || { echo 'No Debian 13 amd64 standard template available.' >&2; exit 1; }
pveam download "$template_storage" "$template"
net="name=eth0,bridge=$bridge,ip=$network"
[[ -z $gateway ]] || net+=",gw=$gateway"
pct create "$ct_id" "$template_storage:vztmpl/$template" --hostname hnefatafl --unprivileged 1 --cores "$cpu" --memory "$ram" --swap 1024 --rootfs "$storage:$disk" --net0 "$net" --onboot 1 --features nesting=0
pct start "$ct_id"
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT
# Credentials are entered directly on the container console, never stored on PVE.
tar -czf "$temporary/bootstrap.tar.gz" -C "$script_root" scripts/linux scripts/proxmox/install hnefatafl/deployment.py
pct push "$ct_id" "$temporary/bootstrap.tar.gz" /root/hnefatafl-bootstrap.tar.gz --perms 0600
pct exec "$ct_id" -- mkdir -p /root/hnefatafl-bootstrap
pct exec "$ct_id" -- tar -xzf /root/hnefatafl-bootstrap.tar.gz -C /root/hnefatafl-bootstrap
pct exec "$ct_id" -- rm /root/hnefatafl-bootstrap.tar.gz
echo "Container $ct_id is ready. Its attached console will request repository, token and administrator credentials."
pct exec "$ct_id" -- bash /root/hnefatafl-bootstrap/scripts/proxmox/install/hnefatafl-install.sh
pct exec "$ct_id" -- rm -rf /root/hnefatafl-bootstrap
echo "LXC container ID: $ct_id | Hostname: hnefatafl | Unprivileged: yes | Autostart: yes"
pct exec "$ct_id" -- hostname -I
echo "Service: hnefatafl.service | Enter: pct enter $ct_id"
echo 'Configuration: /etc/hnefatafl | Persistent data: /var/lib/hnefatafl'
echo 'Update: hnefatafl-update | Logs: journalctl -u hnefatafl -f | Backup: hnefatafl-backup'
