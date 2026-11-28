# Troubleshooting

[Installation index](README.md) · [Installation](installation.md) · [Updating and recovery](updating.md)

## Troubleshooting and target-host validation

| Symptom | Action |
| --- | --- |
| Production startup refuses configuration | Supply HTTPS origin, exact hosts/proxies and run local init-admin |
| Login/POST returns 403 | Check preserved Host, exact Origin, CSRF header and proxy configuration |
| Missing release asset/digest | Publish the clean compatible stable asset described above |
| GitHub 401/403/404 | Check root-only token, repository Contents permission, release status and rate limit |
| Update refuses activity | Finish/cancel queued/running jobs and tournaments, refresh reconciled status and pause workers |
| Wrong/empty records | Check absolute data root, ownership and complete old-root migration |
| Interrupted transaction | Inspect transaction.json, retained releases/backups, then use --recover |
| Resource/OOM failure | Increase LXC RAM/disk or adjust service limits; keep API workers at one |
| Browser build missing | Rebuild the release; do not substitute a dev server |

If **Creating LXC** fails, this is a Proxmox host provisioning failure before
application setup. No start or application installation is attempted. For example,
`tar: /var/lib/lxc/<ID>/rootfs: Cannot open: Permission denied` concerns host-side
template extraction, not Python/application permissions. Inspect the reported CT
and storage without deleting partial state:

```sh
pct config 185
pvesm status
# When extraction reports permission denied:
namei -l /var/lib/lxc/185/rootfs
findmnt -T /var/lib/lxc
```

Older installer revisions passed their private `umask 077` into `pct create`.
Proxmox's directory creation inherited it, producing a root-owned mode-0700
`/var/lib/lxc/<ID>` that mapped container root (typically host UID 100000) could
not traverse. The installer now runs only `pct create` and `pct start` with the
normal host `umask 022`; bootstrap files and credentials keep their private mask.
This prevents that cause for new directories without changing existing permissions.

After an earlier failed attempt, a restrictive staging directory may remain even
if `pct config <ID>` reports no container. Inspect the path permissions first, or
retry the updated installer with a fresh, unused ID. Changing the caller's umask
alone does not fix an old installer that resets it internally, nor does it repair
existing directories. Proxmox itself may remove its newly allocated disk/config
when creation fails; a disk-removal message from `pct create` is that internal
rollback, not an installer request to destroy another container.

The installer does not repair unknown host permission problems, recursively change
host ownership, disable AppArmor or switch to privileged containers. Resolve the
host/storage cause before deciding how to handle the retained partial CT.

Storage status shows **actual usage**, not the sum of thin virtual disk sizes.
An LVM-thin overprovisioning warning does not mean all allocated space is already
used, but the pool can still fill. Inspect data and metadata usage with
`lvs -a -o vg_name,lv_name,lv_size,data_percent,metadata_percent` where applicable.
Monitor capacity; no pool resizing or changes to unrelated guests are performed.

If **Installing Hnefatafl** or **Verifying application service** fails, the LXC
and bootstrap remain for diagnosis; success is not reported. Inspect:

```sh
pct exec 185 -- systemctl status hnefatafl --no-pager
pct exec 185 -- journalctl -u hnefatafl -n 100 --no-pager
pct enter 185
# Inside the container, inspect /etc/hnefatafl/transaction.json before recovery.
```

Host bootstrap temporary files are cleaned even on failure. The transferred
container bootstrap remains until installation and verification succeed. After
an interruption during transfer, inspect its contents before trying to resume;
an incomplete bootstrap is not a valid recovery input.

Before internet exposure on the target Proxmox host, verify reboot/autostart,
proxy TLS/Host/client-IP behavior, Secure cookies, login/logout, USER denial of
training/tournaments, legal gameplay, real CPU training/checkpoint resumption,
private release download, failed-update recovery and an isolated backup restore.
The privileged regression fixture uses a root-sealed release, a separate
unprivileged service account, real application dependencies and a disposable
data directory. Its native-systemd CI run exercises transient preparation units
and the installed startup guard with a real application service. Target-host
validation is still required for live Proxmox provisioning, production systemd
and storage, private credentials and GPU execution. A Proxmox host was
unavailable for this validation pass. The supported nesting-disabled LXC
layout still needs verification on a target host.

On a disposable Debian 13 Proxmox container with a tested off-host copy of
the starting data, use this checklist.

- Confirm with `pct config <ID>` that the container is unprivileged and nesting
  is disabled. Use the standard installer layout and a released 0.41.1 or
  0.42 application. Record the running application version and backup and
  rollback references.
- Bootstrap with the reviewed `--refresh-updater-only` installer. Run
  `hnefatafl-update --cleanup --dry-run`, compare the proposed actions with
  the legacy snapshots, and confirm the preview leaves the layout and
  references intact. Run `hnefatafl-update --cleanup` and verify retained
  snapshots and rewritten references.
- Run a real update with uv and npm. Check the preparation units and
  `journalctl` for namespace errors, and confirm preparation can import the
  application from the sealed release. Check readiness and authenticated
  access after activation. Keep container privileges and isolation settings
  at their standard values.
- Run `hnefatafl-backup`, restart `hnefatafl.service`, and verify readiness.
  On the disposable instance, exercise a supported rollback and resume an
  interrupted operation with `hnefatafl-update --recover`. Confirm SQLite,
  retained data, model and job files, and the current release afterward.

Read the checkout's [current validation](../development/testing.md).

Profile schema 15 is additive and idempotent. It preserves schema-14 consent as
internal publication exceptions and retains old private birth years without
inventing a full birthday. New/edited populated fields publish automatically;
deploy the updated backend and client together. A stopped-writer backup must
precede upgrade. Automatic restore requires matching account columns/schema 15
and keeps current live dates and publication state. Older schema-14 snapshots
need manual reconciliation or migration of a disposable copy before use; the
automatic restore path safely refuses the mismatch. See [profile migration](../development/accounts.md).
