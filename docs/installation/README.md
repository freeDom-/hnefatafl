# Installation and operations

The canonical installation and recovery guide is
[installation.md](installation.md). It covers local production startup, the
Proxmox one-line installer, Debian/LXC installation, systemd, reverse proxy,
updates, storage retention, backups, rollback and target-host troubleshooting.

| Task | Start here |
| --- | --- |
| Understand prerequisites and release assets | [Release prerequisites](installation.md#release-prerequisites) |
| Install in a Proxmox VE container | [Proxmox one-line installation](installation.md#proxmox-one-line-installation) |
| Install on Debian or an existing LXC | [Standalone Debian installation](installation.md#standalone-debian-installation) |
| Configure environment variables | [Configuration](configuration.md) |
| Run behind HTTPS | [Reverse proxy and access](installation.md#reverse-proxy-and-access) |
| Update, retain releases or roll back | [Updating](installation.md#updating), [rollback and recovery](installation.md#rollback-and-recovery) |
| Back up or move an instance | [Training data and backup](../training/models.md#consistent-backup-and-restoration), [manual backups](installation.md#manual-backups-and-recovery-transactions) |
| Manage disk usage | [Storage retention and cleanup](installation.md#storage-retention-preflight-and-cleanup) |
| Diagnose a failed install or update | [Troubleshooting](installation.md#troubleshooting-and-target-host-validation) |

## Supported installation shapes

| Shape | Status |
| --- | --- |
| Local CPU install from a checkout | Supported; see [quick start](../getting-started/quick-start.md) |
| Proxmox VE one-line Debian 13 LXC | Supported; verified installer behaviour is recorded in release notes |
| Debian 13 host or existing LXC with systemd | Supported; systemd/reverse-proxy examples require target-host validation |
| Docker | Not provided |
| Windows/macOS production service | Not documented; development play works |

The installer installs a published stable `X.Y.Z` release with a verified archive
digest. Files on `main` alone are not a valid application installation. The
Proxmox bootstrap itself comes from `main`, so review it before running it as
root.

## Data is separate from releases

Application code, the virtual environment and the persistent data directory have
different lifecycles. Updating or rolling back a release must never overwrite
`HNEFATAFL_DATA_DIR`. Back up the complete data directory, including SQLite
databases, checkpoints, model copies and studies; see
[backup and restoration](../training/models.md#consistent-backup-and-restoration).
