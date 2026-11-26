# Installation, updates and recovery

[Documentation index](README.md) · [Authentication](SECURITY.md) · [Training storage](TRAINING_DATA.md)

## Local startup

Use Python 3.11+ (3.12 recommended for development), Node.js 22.12+ (or 24+) and
uv 0.12.23.
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

Production installations download published stable **X.Y** or **X.Y.Z** GitHub
releases, ignoring drafts/prereleases. A compatible release must publish
`hnefatafl-<tag>.tar.gz` (for example `hnefatafl-0.42.tar.gz`), containing the
repository files at the archive root. Release identifiers are used verbatim and a
release may omit the patch component; ordering treats `X.Y` as `X.Y.0`.
GitHub's release asset API must report its SHA-256 digest. The updater verifies
this digest, bounds extraction and rejects links, devices, traversal and privileged
modes. Automatic GitHub source archives and older releases lacking authentication
are refused. See [release packaging](RELEASING.md#production-updater-assets).
Neither installer publishes a release. Installation requires an already available
compatible published stable release; files on `main` alone are insufficient.

The committed source archive includes `LICENSE` and `THIRD_PARTY_NOTICES.md`.
The frontend license files are Git-ignored build outputs, not source-archive
members: the frontend prebuild writes `LICENSE.txt`, `THIRD_PARTY_NOTICES.txt`
and the generated `THIRD_PARTY_LICENSES.txt` into `frontend/public`, and Vite
copies them into `frontend/dist` during the normal build. Installation and update
do not require license acceptance and do not add license keys, remote activation
or telemetry. The software remains source-available under the PolyForm
Noncommercial License 1.0.0; see [Licensing](LICENSING.md) and the root
[LICENSE](../LICENSE).

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

Schema 19 adds the player-tournament settlement outbox and optional planned start
column. Schema 20 adds indexed persistent due starts and sanitized retry state,
creator quota indexes, clock/increment fields and explicit match modes. Historical
1–5-leg tournaments default to legacy parallel behavior. Schema 22 adds the
tournament's progression policy and schedule generation. Already-started cups
continue to advance independently. Modern two-game cups started after the
upgrade use synchronized rounds. The migration preserves existing brackets,
online games, clocks and history.

Registration-phase schedules and their retry metadata survive migration. The
worker may start an overdue cup if its places filled by the planned instant.
Unfilled cups stay in registration for rescheduling or a manual start. The
lifespan worker resumes committed bracket work without a browser and
reconciles terminal result revisions once at startup. Retained tournament and queue
tables participate in the same stopped-writer backup/restore boundary as their
online games; automatic restore requires matching current schemas. Preserve and
test a backup before upgrading. See [player tournaments](PLAYER_TOURNAMENTS.md).

Schema 23 adds official league game provenance, queue requests, seasonal
projections, durable settlement and recovery work, and the private administrator
audit. It extends the retained-online-state inventory. A compatible restore copies
parents before their children. The update preserves authoritative games, ratings,
tournament brackets and account progression. See [League Arena](LEAGUES.md).

Schema 24 adds permanent per-ruleset league achievement peaks and a durable
reconciliation queue. The upgrade queues bounded account/season/ruleset checks
rather than trusting cached contributions, final standings or an interrupted
rebuild. The League worker validates effective official results after pending
corrections and rebuild catch-up, then grants the corresponding existing tiers.
The queue participates in the retained-online-state inventory. Schema 25 extends
that queue with cursor, accumulated-standing and evidence-generation state so a
large history is processed in fixed-size pages. A correction that superseded a
peak before it was recorded cannot reconstruct that lost historical peak.

The migration also recognizes the earlier League development schema. It
preserves official games, live queue entries and recorded request outcomes.
Terminal preview request IDs without trustworthy entry timestamps return null
for those fields on retry.

New player-tournament creation requires explicit `sequential` or `simultaneous`
mode and always stores two side-swapped games. Update API clients to omit
`match_legs`; missing/null modes, `single` and supplied legacy counts now return
422. Existing single-game and 1–5-leg formats retain their original execution.
Full future-scheduled tournaments reject early creator starts. Administrators
can override. An active bootstrapped administrator retains creation
and management permissions before email verification. No new frontend dependency
is required for the compact calendar or lazy tournament route.

Schema 13 includes configurable online clocks alongside the
[durable online core](ONLINE.md), the
[rated-game/leaderboard pool](RATINGS.md) and immutable per-game start-rating
snapshots without reinterpreting local histories.
Admission defaults off; timeout losses default on and explicit operator pauses
remain available. Review the
[schema-13 recovery boundary](ONLINE.md#migration-backup-and-recovery) before an
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
hnefatafl-update --version X.Y.Z  # or a two-part identifier such as 0.42
hnefatafl-backup
```

Version comparison uses numeric stable versions. Explicit versions must be published
stable releases. Already-current/older selections report no newer version; use
recorded rollback for a downgrade. A release may carry a two-part identifier such
as `0.42`; the updater accepts `X.Y` and `X.Y.Z` and orders `X.Y` as `X.Y.0`.
An updater installed by an older release whose code only accepted three-part tags
cannot discover a two-part release: refresh `/usr/local/lib/hnefatafl/deployment.py`
and the `hnefatafl-update`/`hnefatafl-backup` wrappers from the verified source
archive of the target release (or rerun `scripts/linux/install.sh` from it), then
run the update. A single exclusive lock protects install,
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

### Deferred tournament recovery during activation

The updater holds that exclusive lock from before shutdown until activation,
readiness verification, commit or rollback. Player-tournament settlement must not
make the new service's lifespan require the same lock: that would create a cycle
where readiness needs a lock the updater cannot release until readiness passes.
Startup therefore attempts the one-time reconciliation synchronously. If, and only
if, it observes genuine deployment-gate contention, it leaves durable queue rows
untouched and starts the normal settlement worker. The worker retries after the
gate is released, performs the reconciliation exactly once in that worker
lifecycle, then resumes due scheduled starts and settlement. Database, integrity
and programming errors are not treated as deployment contention; a synchronous
startup attempt still raises them, and worker retries remain incomplete with
bounded backoff. No browser request, administrator action or extra restart is
required.

This preserves all existing write protection: tournament settlement, scheduled
starts, online timeouts and account mail still take the shared gate and return or
defer while the exclusive deployment gate is held. The gate is never bypassed,
disabled or replaced. An update started by an already installed `0.40.0` updater
needs no changes in the updater: it holds the same lock, starts the corrected
service and probes health as before.

The League worker starts with the online background workers. It leaves due
matchmaking, settlement and rebuild work pending while the exclusive deployment
gate is held. Deployment contention does not increment settlement failures. The
worker resumes indexed reconciliation and processing after the updater releases
the gate. Inspect the queue, pending work and rebuild status under League
administration. See [League operations](LEAGUES.md).

Readiness failures now report sanitized operator diagnostics: the original
deployment target, previous, currently probed and installed releases, failure
phase (stop/snapshot/migration/activation/startup/readiness/commit), probe and
transport category (connection refused/timeout/error, truncated response, HTTP
protocol error, invalid JSON or unexpected health response), last HTTP status,
attempt count and elapsed time, allowlisted systemd state/exit result, the failed
automatic-recovery step, and journal/transaction locations. Health probing retries
truncated and protocol responses within the existing budget. Credentials, cookies,
CSRF values, response bodies, exception payloads, configuration secrets and GitHub
tokens are never included.

These enhanced diagnostics and the rollback policy below exist only in the
corrected updater. An update initiated by the already installed `0.40.0` updater
gains the corrected application startup fix but still uses the old updater's
generic readiness message and lacks the new preflight policy.

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

`0.41.0` is a known-incompatible rollback target: its application cannot start
while the updater holds the exclusive deployment gate. The corrected updater
refuses a recorded `--rollback` or interrupted-recovery target of `0.41.0` before
stopping the service, restoring data or switching the release symlink. During
automatic recovery from a failed update it stops the failed target service, then
refuses rollback to `0.41.0` without restoring the snapshot or switching/starting
the incompatible release; the journal, backup reference, activated target and
persistent data remain for reviewed manual recovery. Ordinary rollback to `0.40.0`
remains supported where schema/data-authority rules permit, as does rollback
between corrected releases with tournament workers. The command exits nonzero with
the journal and log locations; inspect them before any manual action.

### Cross-schema automatic recovery is fail-closed

Creating an automatic pre-deployment snapshot is **not** a guarantee that an
automatic rollback can complete. After a successful migration the live database
uses a newer schema than the pre-deployment snapshot. Existing account/online
schema-authority checks refuse to replace a schema-25 live database with an older
snapshot, including schema 15, 20, 21, 22, 23 or 24. This preserves authoritative online,
tournament and security state. The corrected updater performs a read-only
compatibility preflight before stopping a healthy service for an explicit
`--rollback`; a schema mismatch is refused before service stop, data replacement or
release switching. During automatic recovery it performs the same preflight and
retains the activated target, migrated live database, verified snapshot, model and
checkpoint files, installed state and transaction journal.

The historical `0.40.0` updater also fails closed after migrating to a newer
online schema. Its `retain_online_on_restore` check reports `Online schema
changed; manual recovery is required` before replacing live authority. In that
situation the target release may
already be activated and the service stopped; the old release is not automatically
started, and the transaction remains unrecovered. Do not force a downgrade.

Supported automatic recovery:

- a failed deployment whose snapshot and live database have compatible schemas
  and whose previous release is not `0.41.0`;
- rollback between corrected releases with matching schemas and compatible data;
- the historical updater restoring a compatible snapshot when its own schema
  checks permit it.

Manual recovery is required when the live database was migrated to a newer schema
than the snapshot, when account/online columns differ, when `0.41.0` is the
previous release, or when backup verification/integrity fails. Preserve
`/etc/hnefatafl/transaction.json`, the referenced snapshot and
`/var/lib/hnefatafl.retained-*` directories. Restore the snapshot and matching old
release only after a reviewed compatibility plan; otherwise keep the corrected
target release and recover forward. There is no general database downgrade
mechanism in this hotfix.

With matching current overall schemas, recovery retains live authoritative online
matches, games, moves/results, audit, rating history, start-rating snapshots and
League authority and work queues, then pauses the timeout
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
The readiness error reports the probe/category/transport, attempts, service state
and whether that automatic recovery succeeded. If recovery itself fails, the
command exits nonzero, preserves artifacts and reports the failed recovery plus
the journal and transaction locations. Do not restart arbitrary old code against
an unknown schema. An initial failed install has no previous release and stays
stopped.

Backups contain account hashes, sessions, worker credentials and private
configuration: keep root-only and copy encrypted off the container. Container
snapshots and tested off-host restores complement application snapshots. A local
backup is not protection against loss of the Proxmox host. Never destructively
test these procedures on production data.

### League worker recovery

Open **League administration** and refresh the health projection. A running
worker reports its last check separately from the last successful processing
cycle. Idle is expected when the queues are empty. During activation, the
deployment lock defers pending writes until the updater releases the gate.

If retry counts keep rising or the oldest pending settlement keeps aging,
record the backlog, durable attempts, next retry time and any rebuilding
season and stage. Check service logs and host storage availability and access.
Take a supported backup and reproduce the failure on a disposable copy when
an application or data repair is required. Restore normal service using the
compatible update or rollback procedures above. Settlement retries and
rebuild cursors survive restart and pick up from the recorded due time.

For an incorrect official result, use the existing authorized online result
controls and their audit trail. The resulting revision enters the durable
settlement queue. If the authoritative results are sound and a projection
needs repair, request a season rebuild with the displayed revision and a
reason. An already running rebuild resumes from its saved stage once the cause
of the retry is resolved. Verify that the retry backlog clears and the season
publishes a new revision before closing the incident. League recovery derives
all points and standings from the authoritative result ledger.

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

Profile schema 15 is additive and idempotent. It preserves schema-14 consent as
internal publication exceptions and retains old private birth years without
inventing a full birthday. New/edited populated fields publish automatically;
deploy the updated backend and client together. A stopped-writer backup must
precede upgrade. Automatic restore requires matching account columns/schema 15
and keeps current live dates and publication state. Older schema-14 snapshots
need manual reconciliation or migration of a disposable copy before use; the
automatic restore path safely refuses the mismatch. See [profile migration](PROFILE.md#api-and-persistence).
