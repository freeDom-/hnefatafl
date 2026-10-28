# Installation, updates and recovery

[Documentation index](README.md) · [Authentication](SECURITY.md) · [Training storage](TRAINING_DATA.md)

## Local startup

Use Python 3.11+ (3.12 recommended for development), Node 22+ and uv 0.12.23.
From the checkout:

```sh
make install
make build
.venv/bin/python -m hnefatafl.accounts init-admin
bash scripts/start.sh
```

Open `http://127.0.0.1:8000`, log in, and create a game. Initial setup requests
username (default **Odin**), email, password and confirmation. All application
functionality requires login in development too. There is no default password.
Development allows only localhost/127.0.0.1 by default; do not expose Vite or a
reload server publicly. `.env` is not loaded automatically.

## Release prerequisites

Production installations download published stable **X.Y.Z** GitHub releases,
ignoring drafts/prereleases. A compatible release must publish
`hnefatafl-X.Y.Z.tar.gz`, containing the repository files at the archive root.
GitHub's release asset API must report its SHA-256 digest. The updater verifies
this digest, bounds extraction and rejects links, devices, traversal and privileged
modes. Automatic GitHub source archives and older releases lacking authentication
are refused. See [release packaging](RELEASING.md#production-updater-assets).
Neither installer publishes a release. Installation requires an already available
compatible published stable release; files on `main` alone are insufficient.

## Proxmox one-line installation

As **root on a Proxmox VE host**, from any working directory:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-install.sh)
```

No local checkout or Git executable is needed. The host requires `curl`, Python 3,
standard Debian shell/archive tools and working native `pct`, `pveam`, `pvesh`,
`pvesm`, `pveversion` and `ip` commands. It needs an active container storage pool,
template storage supporting `vztmpl`, an existing network bridge, a Debian 13
amd64 standard template in the Proxmox catalog, and outbound HTTPS/DNS access.
The container also needs access to Debian package mirrors, GitHub release APIs/assets,
Node.js, PyPI/locked Python indexes and npm. Reserve actual disk capacity for
the template, two application releases, frontend builds, training data and backups.

**Trust boundary:** the one-liner executes this project's `main` branch as root.
Review the downloaded launcher and the installer components before executing them.
The launcher uses a fixed HTTPS codeload URL for `freeDom-/hnefatafl`, with certificate
verification, time/size limits and no redirects. It validates the complete archive
layout, entry paths/types, size limits and required components, rejects links and
special files, and checks shell syntax before delegation. These checks protect
extraction; they do not authenticate main independently of HTTPS or make unreviewed
main code safe. No third-party shell code is executed.

The temporary complete checkout is removed on success, failure and handled signals.
It only bootstraps the existing native LXC installer, Debian installer and verified
stable-release deployment system. The application is never installed from main.

## Proxmox LXC installation

Alternatively, obtain and review an authenticated local checkout of your
repository. Keep GitHub tokens out of clone URLs and command arguments; use your
usual credential helper or securely transfer a reviewed checkout. Then run:

```sh
cd /path/to/reviewed/hnefatafl
bash scripts/proxmox/ct/hnefatafl.sh
```

The default path creates an unprivileged Debian 13 amd64 container, hostname
`hnefatafl`, four CPU cores, 4096 MiB RAM, 20 GiB disk on `local-lvm`, template
storage `local`, DHCP on vmbr0, autostart enabled and nesting disabled.
Choose advanced setup for container ID, CPU/RAM/disk, root/template storage, bridge,
static IPv4 CIDR and gateway. At least 2 GiB RAM and 12 GiB disk are required;
8 GiB RAM and more disk are advisable for substantial training/backups. No GPU,
privileged container, nesting or Docker is required.

The host transfers only bootstrap code. Repository, optional GitHub token, port,
public HTTPS origin, explicit trusted proxy addresses and administrator credentials
are requested inside the attached container console, including a hidden password
and confirmation. Port defaults to 8000; supply a public HTTPS origin such as
`https://tafl.example.org` and the explicit proxy IP/CIDR list (never `*`).
Prompts retain the host terminal when launched with Bash process substitution.
Secrets never pass through
Proxmox command arguments, logs or persistent container configuration. The temporary
bootstrap archive is removed; no credential-bearing temporary archive exists.
The prompted repository/token select application releases, including private
releases. For private custom bootstrap code, use an authenticated reviewed checkout;
the public launcher always obtains bootstrap code from `freeDom-/hnefatafl`.
An interrupted installation retains the container
for inspection; it never destroys a failed container automatically. Re-enter it
with `pct enter <ID>`, inspect the journal and logs, and rerun
`bash /root/hnefatafl-bootstrap/scripts/proxmox/install/hnefatafl-install.sh`
when safe to continue. Do not rerun LXC creation with an occupied ID.

Success is printed only after the installed release's host-header-aware health,
authenticated permission/catalog and anonymous rejection probes pass, and systemd
is both enabled and active. The summary reports release, CT ID, internal IP and
configured port. The public HTTPS URL requires your existing reverse proxy, DNS
and firewall configuration; the installer changes none of these, your Fritzbox
or your TLS certificates.

Host administration examples (replace **185** with your container ID):

```sh
pct enter 185
pct exec 185 -- systemctl status hnefatafl
pct exec 185 -- journalctl -u hnefatafl -f
pct exec 185 -- /usr/local/bin/hnefatafl-update --check
pct exec 185 -- /usr/local/bin/hnefatafl-backup
```

The installer configures `/usr/local/bin` in root's login and interactive Bash PATH and verifies both maintenance commands. Proxmox `pct exec` does not necessarily read shell startup files, so host examples use absolute paths. Open a new shell to pick up PATH changes after a repair.

`hnefatafl.service` runs as the dedicated unprivileged `hnefatafl` user, restarts
on unexpected failure and starts at boot. `/etc/hnefatafl` holds protected
configuration, `/var/lib/hnefatafl` holds persistent data and backups, and
`/opt/hnefatafl` holds replaceable release code and tools.

This is an independent Community Scripts-style launcher with separate host and
container layers. Its defaults, advanced resource flow and update separation were
reviewed against the current [Immich launcher](https://github.com/community-scripts/ProxmoxVE/blob/main/ct/immich.sh),
[container installer](https://github.com/community-scripts/ProxmoxVE/blob/main/install/immich-install.sh),
[Community Scripts core](https://github.com/community-scripts/core) and
[project site](https://community-scripts.org/). Native `pct`/`pveam` avoid coupling
a private application to upstream dynamic helper interfaces. No upstream shell
code is copied or downloaded for execution.

## Standalone Debian installation

Run this on a dedicated Debian 13 amd64/arm64 system as root:

```sh
cd /path/to/reviewed/hnefatafl
bash scripts/linux/install.sh
# To select a published stable release:
# bash scripts/linux/install.sh --version X.Y.Z
```

It installs Python/venv/compiler/CPU runtime dependencies, pinned uv, and the latest
stable Node 22 patch binary verified against Node's published SHA-256 manifest.
It creates an unprivileged `hnefatafl` user, builds using `uv sync --frozen --extra train --extra dev`
and `npm ci`, runs the release's security tests, freezes code ownership, migrates
SQLite, prompts for Odin's credentials, installs systemd, and probes readiness
plus authenticated permissions and rejection of anonymous API access.

The public HTTPS origin and trusted proxy addresses are mandatory for production.
No reverse proxy, DNS, TLS certificate or external firewall is installed/changed.
Repeated setup preserves configuration, accounts and passwords. If the first
installation failed before completion, rerun setup; an interrupted transaction may
first require `hnefatafl-update --recover` after inspecting its journal.

## Data and configuration

| Location | Contents |
| --- | --- |
| `/opt/hnefatafl/releases/X.Y.Z-<id>` | Replaceable code, frozen venv and frontend build |
| `/opt/hnefatafl/current` | Atomically switched active release symlink |
| `/opt/hnefatafl/tools` | Root-owned uv and Node build tools |
| `/etc/hnefatafl/application.env` | Root-only production configuration, read by systemd |
| `/etc/hnefatafl/deployment.json` | Root-only repository, port and HTTPS origin |
| `/etc/hnefatafl/github.json` | Optional root-only read-only GitHub credential |
| `/etc/hnefatafl/installed.json`, `transaction.json` | Installed version and durable recovery journal |
| `/var/lib/hnefatafl/hnefatafl.sqlite3` | Games, accounts, sessions, stats source, players, jobs and tournaments |
| `/var/lib/hnefatafl/models` | Inference model registry |
| `/var/lib/hnefatafl/jobs` | Existing training output/checkpoints/logs; paths remain stable |
| `/var/lib/hnefatafl/tournaments` | Retained tournament worker output |
| `/var/lib/hnefatafl/backups` | Root-only verified full pre-migration snapshots |
| journald | API and service logs |

Set `HNEFATAFL_DATA_DIR` for development/test isolation. Production uses its fixed
persistent root; release directories are read-only to the application. Ruleset
Markdown remains versioned runtime code. Existing job/checkpoint paths remain
under `jobs`, rather than being moved into new artificial directories. To migrate
an old installation, stop all writers and copy the **complete** data root and
configuration first; see [backup and relocation](TRAINING_DATA.md#consistent-backup-and-restoration).
Do not copy only the database or registry. Existing legacy games/players remain
administrator-only, with no invented account ownership. Schema 8 is additive and
transactional; newer unsupported schemas are refused. Schema 9 adds account
verification/token/mail/limiter state without reassigning owners; run the
[read-only collision preflight and review rollback constraints](REGISTRATION.md#migration-and-rollback-boundary)
before a separately authorized upgrade. No production migration is implied by
development validation.

Schema 12 includes the [durable online core](ONLINE.md), the
[rated-game/leaderboard pool](RATINGS.md) and immutable per-game start-rating
snapshots without reinterpreting local histories.
Admission defaults off and timeout finalization starts paused, with a separate
unapproved production outage-policy prerequisite. Review the
[schema-12 recovery boundary](ONLINE.md#migration-backup-and-recovery) before an
upgrade or rollback, and pause the timeout gate before maintenance.

Registration remains off unless explicitly enabled with valid mail configuration.
See [SMTP setup and account operations](REGISTRATION.md#enable-and-configure-mail).

## Private GitHub authentication

Use a fine-grained read-only token restricted to the repository, with Contents
read and Metadata read permissions. The installer reads it without echo and writes
`/etc/hnefatafl/github.json` with mode 0600, in a 0700 directory. It is used only
for GitHub release metadata/assets and never authenticates the app. Redirects
remove Authorization before leaving api.github.com; download URLs must remain
HTTPS on explicit GitHub asset hosts. Do not include tokens in URLs, argv or Git.
To rotate/remove the token, edit that protected JSON file locally as root; a public
repository works with an empty token. Access/rate-limit failures are reported
without printing credentials or private response bodies.

## Reverse proxy and access

Only the configured application port (default 8000) should be reachable by your
existing HTTPS proxy. Restrict LXC ingress to the proxy using your existing network
controls. There are no exposed database/training-management service ports.
Use [the proxy example](../deployment/nginx.conf.example) as header guidance,
integrated into your existing HTTPS virtual host.

Set the external origin and exact hostnames in `/etc/hnefatafl/application.env`:

```text
HNEFATAFL_ENV=production
HNEFATAFL_PUBLIC_URL=https://tafl.example.org
HNEFATAFL_ALLOWED_HOSTS=tafl.example.org,127.0.0.1,localhost
HNEFATAFL_TRUSTED_PROXIES=10.0.0.2
HNEFATAFL_HOST=0.0.0.0
HNEFATAFL_PORT=8000
```

The proxy must preserve the public Host, overwrite X-Forwarded-For with verified
client information, and overwrite X-Forwarded-Proto with `https`. Never use `*`
for trusted proxies. Only Uvicorn's configured proxy IPs/CIDRs can supply forwarded
identity/protocol information. Public cookies remain Secure regardless of upstream
HTTP transport. Origin is exact and CORS is disabled. Login must use the same
HTTPS origin as the frontend. Security headers restrict framing, sniffing, sources,
referrers and caching. No WebSocket/SSE routes currently exist; upgrade attempts
are rejected. Future streams require explicit authorization.

## Service and remote training

```sh
systemctl status hnefatafl
systemctl restart hnefatafl
journalctl -u hnefatafl -f
systemctl edit hnefatafl
```

The service runs without root, restarts on failure, starts after reboot, writes
only persistent state and uses a 90-second graceful stop timeout. Local training
and tournament workers remain subprocesses in the same service cgroup, with
CPU/memory/task limits. Adjust container CPU/RAM and systemd `MemoryMax`/`CPUQuota`
for workloads. Keep one API worker: SQLite sessions support multiple workers,
but process/catalog ownership is not a distributed scheduler.

Administrators retain remote training UI, logs, checkpoints, cancellation,
model transfers and worker enrollment. Regular users retain gameplay against
existing AI, without training/tournament access. Remote workers connect outbound
through HTTPS, with scoped credentials and job leases. Old worker tokens lacking
an enrolling account require re-enrollment. See [Remote training](REMOTE_TRAINING.md).
Server and workers must run the same exact application release. During an ordinary
server restart, valid remote leases continue across version changes, but outdated
workers cannot claim additional work until updated or given an explicit per-worker
administrator override. No re-enrollment is needed. Keep restart downtime within
the lease timeout. See [compatibility and worker updates](REMOTE_TRAINING.md#application-compatibility-and-upgrades).
The production updater's activity preflight remains unchanged:
stop/cancel local/remote jobs and tournaments and pause remote claim loops before
updates. Resume from retained checkpoints explicitly afterward; updates never
silently terminate active training. Future GPU use needs a separate validated
host/device/runtime plan; CPU-only installation does not configure passthrough.

## Updating

From the Proxmox host, use the installed updater directly or the standalone wrapper:

```bash
pct exec 185 -- /usr/local/bin/hnefatafl-update
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --check
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --list
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --version 0.22.1
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --backup
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --rollback
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --recover
```

CT 185 and version 0.22.1 are examples. The wrapper requires an explicit CT ID
(or exported `HNEFATAFL_CTID`); it never discovers/selects a container automatically
and does not start stopped containers. It checks installation/updater availability,
forwards arguments without shell evaluation and returns the updater's exit status.
It executes project main-branch launcher code as root under the same trust boundary
as installation, but downloads no application code itself. Updates retain the
existing published stable release selection, digest checks, activity gates,
snapshots, migrations, readiness checks, rollback and recovery behavior.

As root, from any working directory:

```sh
hnefatafl-update --check
hnefatafl-update --list
hnefatafl-update
hnefatafl-update --version X.Y.Z
hnefatafl-backup
```

Version comparison uses numeric stable versions. Explicit versions must be published
stable releases. Already-current/older selections report no newer version; use
recorded rollback for a downgrade. A single exclusive lock protects install,
update, backup and recovery. The target builds in its own final release directory,
so absolute venv paths remain valid and the active release is untouched on build
failure. Dependency lockfiles, security tests and frontend builds must pass first.

Before activation, active/queued training and tournaments are refused. After
stopping the service, activity is checked again, then a full data/config snapshot
is made and verified with file SHA-256 hashes and SQLite integrity checking.
Migrations run before atomic symlink activation. The service restarts and must
pass health, authenticated account/permission/catalog probes and anonymous rejection.
Only then is the release recorded as installed. Old releases and snapshots remain;
no automatic pruning deletes your data. Ensure disk space for two releases and a
complete data snapshot, particularly large checkpoints.

An OS file lock drains admitted HTTP mutations before activity is checked. During
activation, recovery and backup, new mutations receive 503; health/authenticated
read probes remain available. The lock's inode survives data restoration. This
prevents a new training request from racing the update's graceful service stop.

## Rollback and recovery

```sh
hnefatafl-update --rollback
# After inspecting an interrupted update journal:
hnefatafl-update --recover
```

Rollback stops writers and restores the **matching pre-deployment full data
snapshot** with the previous release, rather than running old code on a new schema.
Current account/password/role/status state is retained when account schemas match;
sessions are invalidated and restored worker credentials are revoked. Re-login
and re-enroll workers. A changed/incompatible account schema requires explicit
manual recovery; the updater refuses to overwrite security state blindly.

With matching online schema 11, recovery retains current live authoritative online
matches, games, moves/results, audit and rating history, then pauses the timeout
gate with an audit entry. It does not rewind accepted online play to the snapshot. A changed
online schema/column set refuses automatic root replacement, even when account
columns match. Snapshot-only recovery without live authority cannot recover newer
progress, but still revokes saved capabilities and pauses timeouts. See
[online recovery](ONLINE.md#migration-backup-and-recovery) before resuming.

Data created after the snapshot is kept in a sibling
`/var/lib/hnefatafl.retained-<id>` directory for inspection and manual reconciliation.
Other local game/model data is not silently merged. Application configuration
is preserved; each snapshot also contains its prior protected configuration.
Failed migration/readiness normally restores the previous code/data and starts it.
If recovery itself fails, the command exits nonzero, preserves artifacts and prints
journal/log locations. Do not restart arbitrary old code against an unknown schema.
An initial failed install has no previous release and stays stopped.

Backups contain account hashes, sessions, worker credentials and private
configuration: keep root-only and copy encrypted off the container. Container
snapshots and tested off-host restores complement application snapshots. A local
backup is not protection against loss of the Proxmox host. Never destructively
test these procedures on production data.

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
Live Proxmox provisioning, target systemd, private credentials and GPU execution
require target-host validation; unit mocks and local browser tests do not establish
them. Read [current validation](VALIDATION.md) for this checkout's recorded checks.
