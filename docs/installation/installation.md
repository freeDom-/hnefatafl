# Installation and deployment

[Documentation index](../../README.md) · [Authentication](../administration/security.md) · [Training storage](../training/models.md)

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
are refused. See [release packaging](../development/releasing/README.md#packagingupdater-requirements).
Neither installer publishes a release. Installation requires an already available
compatible published stable release; files on `main` alone are insufficient.

The committed source archive includes `LICENSE` and `THIRD_PARTY_NOTICES.md`.
The frontend license files are Git-ignored build outputs, not source-archive
members: the frontend prebuild writes `LICENSE.txt`, `THIRD_PARTY_NOTICES.txt`
and the generated `THIRD_PARTY_LICENSES.txt` into `frontend/public`, and Vite
copies them into `frontend/dist` during the normal build. Installation and update
do not require license acceptance and do not add license keys, remote activation
or telemetry. The software remains source-available under the PolyForm
Noncommercial License 1.0.0; see [Licensing](../LICENSING.md) and the root
[LICENSE](../../LICENSE).

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
pct exec 185 -- /usr/local/bin/hnefatafl-update --cleanup --dry-run
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
| `/opt/hnefatafl/tmp` | Managed archive downloads, temporary build files, dependency caches and isolated verification data |
| `/etc/hnefatafl/application.env` | Root-only production configuration, read by systemd |
| `/etc/hnefatafl/deployment.json` | Root-only repository, port, HTTPS origin, retention and storage settings |
| `/etc/hnefatafl/github.json` | Optional root-only read-only GitHub credential |
| `/etc/hnefatafl/installed.json`, `transaction.json` | Installed release, expected rollback snapshot and durable application transaction |
| `/etc/hnefatafl/.transaction-reserve` | Private 1 MiB reserve for publishing recovery metadata |
| `/etc/hnefatafl/backup-migration.json` | Temporary recovery journal for relocation of legacy backups |
| `/usr/local/lib/hnefatafl/active.json`, `generations/` | Root-owned active updater pointer and verified utility generations |
| `/var/lib/hnefatafl/hnefatafl.sqlite3` | Games, accounts, sessions, stats source, players, jobs and tournaments |
| `/var/lib/hnefatafl/models` | Inference model registry |
| `/var/lib/hnefatafl/jobs` | Existing training output/checkpoints/logs; paths remain stable |
| `/var/lib/hnefatafl/tournaments` | Retained tournament worker output |
| `/var/lib/hnefatafl/browser_runtime` | Optional staged browser-analysis runtime; content-addressed versions plus an atomic `current` symlink |
| `/var/lib/hnefatafl/browser_models` | Explicitly published browser inference artifacts, publication index and audit trail |
| `/var/lib/hnefatafl-backups` | Root-only verified full pre-migration snapshots and manual backups (sibling of the writable data root; a legacy `backups/` inside it is migrated once) |
| journald | API and service logs |

Set `HNEFATAFL_DATA_DIR` for development/test isolation. Production uses its fixed
persistent root; release directories are read-only to the application. Ruleset
Markdown remains versioned runtime code. Existing job/checkpoint paths remain
under `jobs`, rather than being moved into new artificial directories. To migrate
an old installation, stop all writers and copy the **complete** data root and
configuration first; see [backup and relocation](../training/models.md#backup-and-restoration).
Do not copy only the database or registry. Existing legacy games/players remain
administrator-only, with no invented account ownership. Schema 8 is additive and
transactional; newer unsupported schemas are refused. Schema 9 adds account
verification/token/mail/limiter state without reassigning owners; run the
[read-only collision preflight and review rollback constraints](../administration/user-management.md#migration-and-rollback)
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
test a backup before upgrading. See [player tournaments](../game/tournaments.md).

Schema 23 adds official league game provenance, queue requests, seasonal
projections, durable settlement and recovery work, and the private administrator
audit. It extends the retained-online-state inventory. A compatible restore copies
parents before their children. The update preserves authoritative games, ratings,
tournament brackets and account progression. See [League Arena](../game/leagues.md).

Schema 24 adds permanent per-ruleset league achievement peaks and a durable
reconciliation queue. The upgrade queues bounded account/season/ruleset checks
rather than trusting cached contributions, final standings or an interrupted
rebuild. The League worker validates effective official results after pending
corrections and rebuild catch-up, then grants the corresponding existing tiers.
The queue participates in the retained-online-state inventory. Schema 25 extends
that queue with cursor and accumulated-standing state. Schema 26 adds
account/season/ruleset evidence generations, league-sequence keyset indexes and
equivalent progress-tier guards for fresh and upgraded tables. A correction that
superseded a peak before it was recorded cannot reconstruct that lost historical
peak.

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
[durable online core](../administration/online-operations.md), the
[rated-game/leaderboard pool](../game/ratings.md) and immutable per-game start-rating
snapshots without reinterpreting local histories.
Admission defaults off; timeout losses default on and explicit operator pauses
remain available. Review the
[schema-13 recovery boundary](../administration/online-operations.md#recovery) before an
upgrade or rollback, and pause the timeout gate before maintenance.

Registration remains off unless explicitly enabled with valid mail configuration.
See [SMTP setup and account operations](../administration/user-management.md#enable-registration-and-mail).

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
Use [the proxy example](../../deployment/nginx.conf.example) as header guidance,
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
an enrolling account require re-enrollment. See [Remote training](../training/remote-workers.md).
Server and workers must run the same exact application release. During an ordinary
server restart, valid remote leases continue across version changes, but outdated
workers cannot claim additional work until updated or given an explicit per-worker
administrator override. No re-enrollment is needed. Keep restart downtime within
the lease timeout. See [compatibility and worker updates](../training/remote-workers.md#compatibility-and-capabilities).
The production updater's activity preflight remains unchanged:
stop/cancel local/remote jobs and tournaments and pause remote claim loops before
updates. Resume from retained checkpoints explicitly afterward; updates never
silently terminate active training. Future GPU use needs a separate validated
host/device/runtime plan; CPU-only installation does not configure passthrough.

## Browser-local analysis distribution (optional)

Browser-local analysis ships **disabled**. When enabled, the existing analysis UI may
execute locally under the deployment policy; there is no separate browser-analysis
page or player provider selector. Enabling it
requires an explicitly staged, pinned runtime and explicitly published models; an
installation without either continues to start and serve every ordinary page.

```sh
# As the data-directory owner, from the active release directory.
# Downloads only the pinned, digest-verified Pyodide 0.27.7 and ORT Web 1.22.0
# assets (or pass --asset-cache for an air-gapped, pre-verified cache), verifies
# the complete staged tree and atomically activates it.
HNEFATAFL_DATA_DIR=/var/lib/hnefatafl make browser-runtime
HNEFATAFL_DATA_DIR=/var/lib/hnefatafl make browser-runtime-status

# Publish one validated export (administrator account from the application DB).
.venv/bin/python -m hnefatafl.browser_models --data-dir /var/lib/hnefatafl \
  publish /secure/export-brandubh --account Odin --name "Brandubh v3" --replace
.venv/bin/python -m hnefatafl.browser_models --data-dir /var/lib/hnefatafl list
.venv/bin/python -m hnefatafl.browser_models --data-dir /var/lib/hnefatafl \
  revoke <publication_id> --account Odin --reason "replaced"
```

Then choose the server-owned execution policy and restart:

```ini
# Browser-first operation; no server analysis computation.
HNEFATAFL_ANALYSIS_MODE=browser_only

# Or opt-in rollout with strictly bounded server fallback.
# HNEFATAFL_ANALYSIS_MODE=browser_preferred
```

`browser_only` and `browser_preferred` enable browser runtime delivery and the
narrow CSP; `server_only` and `disabled` do not. The legacy
`HNEFATAFL_BROWSER_ANALYSIS_ENABLED=1` without `HNEFATAFL_ANALYSIS_MODE` keeps the
historical server-only behaviour and delivery, but does not activate browser-first
execution. An explicit mode always wins, and the legacy flag cannot re-enable
delivery under `server_only`/`disabled`. Existing installations therefore keep
their previous behaviour until an owner explicitly selects a browser mode.

Additional administrative limits (defaults; all server-enforced and shared by
direct and fallback requests):

```ini
# HNEFATAFL_ANALYSIS_WEBGPU=0
# HNEFATAFL_ANALYSIS_OFFLINE_SECONDS=900
# HNEFATAFL_ANALYSIS_MAX_CONCURRENT=16
# HNEFATAFL_ANALYSIS_MAX_PER_USER=4
# HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE=240
```

The supported deployment keeps one API worker, so these task-lifetime limits are
global for the service. The existing bounded analysis queue, result cache,
deduplication and overload `503`/`Retry-After` behaviour are reused. The enabled
CSP adds only `script-src 'self' 'wasm-unsafe-eval'` and `worker-src 'self'`;
disabled deployments keep the existing strict policy.
Runtime assets are served from the application origin with immutable caching and
per-identity snapshot verification; model downloads are authenticated, streamed,
`no-store`, digest-verified and may bind the immutable artifact version with
`?version=<sha256>` (stale versions return 404). Retained runtime identities keep
serving their own bytes while present; pruning makes their URLs return a controlled
404. Source-checkout (`0+local`) runtime staging requires the explicit
`--development` flag and is never treated as production compatibility.

`browser_only` rejects every server analysis computation request with
`409 analysis_server_disabled`; `browser_preferred` retains the server endpoints as
a fallback with admission controls; `disabled` suppresses optional analysis while
replay, gameplay, move validation, AI opponents, training and tournaments continue.
Policy is resolved at process start, so a mode change requires a restart. The
existing analysis-service shutdown handles queued/running work; in-flight work
cannot bypass the old process's already-loaded policy. To roll back, set
`HNEFATAFL_ANALYSIS_MODE=server_only` (or remove the variable and set
`HNEFATAFL_BROWSER_ANALYSIS_ENABLED=0`) and restart. No reverse-proxy or new
deployment requirement is introduced.

Behavior across installation, update and rollback:

- Runtime and publication data are under the persistent data root, never inside a
  release directory. The updater and rollback do not move them.
- Each runtime manifest binds the application version, encoder, driver and worker
  protocols. After an upgrade (or a rollback), the previously staged runtime is
  reported unavailable until a matching runtime is staged and activated; it is
  never executed against incompatible code. Re-run `make browser-runtime`.
- An interrupted build leaves an unusable staging directory and no new active
  runtime. Re-run `build`/`activate`; `python -m hnefatafl.browser_runtime
  --data-dir ... prune --keep 2` removes inactive versions.
- Backups: the existing stopped-writer full data-root snapshot already covers
  `browser_models` (publication index, artifacts, audit trail). A failed audit/index
  write is rolled back and reported as `PUBLICATION_IO`; retry after fixing storage.
  Restore the index
  and its content-addressed artifacts together. `browser_runtime` is reproducible
  from pinned inputs and can be excluded from a backup to save space, but then it
  must be staged again after restore.
- Recovery after an interrupted update: follow the normal `--recover` procedure.
  If the restored release does not match the staged runtime, the server starts with
  browser analysis unavailable; stage a matching runtime before re-enabling.
- Delete a published model only through `revoke` (or `publish --replace`); the
  artifact store is immutable and the audit trail is append-only. Revocation stops
  future protected downloads but cannot recall copies already downloaded.
- Do not publish private training checkpoints, optimizer state, replay buffers or
  unrelated files. Only a validated browser-compatible ONNX export can be
  published; distributing it necessarily makes the weights accessible to clients.
