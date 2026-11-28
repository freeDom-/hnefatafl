# Updating, retention and recovery

[Installation index](README.md) · [Installation](installation.md) · [Configuration](configuration.md)

## Updating

From the Proxmox host, use the installed updater directly or the standalone wrapper:

```bash
pct exec 185 -- /usr/local/bin/hnefatafl-update
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --check
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --list
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --version 0.22.1
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --backup
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --cleanup --dry-run
bash <(curl -fsSL https://raw.githubusercontent.com/freeDom-/hnefatafl/main/scripts/proxmox/ct/hnefatafl-pve-update.sh) 185 --cleanup
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
hnefatafl-update --cleanup --dry-run
hnefatafl-update --cleanup
hnefatafl-update --refresh-updater
hnefatafl-backup
```

Version comparison uses numeric stable versions. Explicit versions must be published
stable releases. Already-current/older selections report no newer version; use
recorded rollback for a downgrade. A release may carry a two-part identifier such
as `0.42`; the updater accepts `X.Y` and `X.Y.Z` and orders `X.Y` as `X.Y.0`.
An updater installed by an older release whose code only accepted three-part tags
cannot discover a two-part release, and an older updater also lacks storage
preflight and retention. For an existing installation, refresh the utility from a
reviewed distribution containing these controls before the first update. The same
procedure applies to installations already running `0.41.1` or `0.42`:

```sh
# From a reviewed checkout or extracted verified release archive:
bash scripts/linux/install.sh --refresh-updater-only
/usr/local/bin/hnefatafl-update --cleanup --dry-run
/usr/local/bin/hnefatafl-update
```

`--refresh-updater-only` installs a stable launcher at
`/usr/local/lib/hnefatafl/deployment.py` and the maintenance command wrappers. The
launcher selects the generation recorded in `active.json`. Each generation
contains the updater and its schema-aware restore modules. Publication compiles
Python, checks wrapper syntax, imports the complete standalone generation and
synchronizes it to disk before activating it with one atomic pointer update.
The previous working generation remains available. Each generation declares
the updater ABI and the minimum launcher protocol it needs. Launcher changes
come from the verified archive or reviewed installer. A compatible new
launcher is installed atomically while the old generation still runs, then
the pointer is switched. A flat legacy updater keeps its original entry point
until the first generation is ready to use.

An existing installation can stay on its current ABI or advance one ABI at a
time after generation bootstrap. The candidate must be able to read its
predecessor's transaction and installed-state formats. The publisher checks
the recorded metadata with the candidate before switching. The systemd startup
guard can therefore use the new utility while the preceding updater still
owns the deployment transaction. Unsupported transitions require an intervening
compatible utility. A compatible launcher protocol can likewise advance one
version at a time, including when the active generation still needs the
previous protocol.

The two maintenance commands use a fixed wrapper contract. The update
command executes the stable launcher with isolated Python, and the backup
command delegates to it with `--backup`. Verified releases carry the canonical
wrapper bytes. The publisher accepts the historical flat update wrapper during
bootstrap and refuses other wrapper changes. A future contract change would
need a reviewed migration that keeps both command forms compatible with the
installed launcher throughout replacement.

If either installed wrapper is damaged or has lost its executable mode, use
`bash <extracted>/scripts/linux/install.sh --refresh-updater-only` from a reviewed
checkout or digest-verified release. The installer takes the updater lock,
verifies the utility generation, and restores each canonical command atomically.
An interrupted repair can be run again from the same trusted source.

If the installed launcher or its active generation is damaged, rerun
`bash <extracted>/scripts/linux/install.sh --refresh-updater-only` from the same
or a supported newer verified release. This entry point takes the updater lock
before replacing the launcher and authenticates the replacement files without
importing the damaged utility. Then run `hnefatafl-update --recover` if the
updater reports an unfinished operation.

This installer mode leaves the service, application data and `/etc/hnefatafl`
application configuration, installed state and deployment journal alone. It uses
the same operation lock as `hnefatafl-update --refresh-updater`, which obtains
utility files directly from a release archive verified against its GitHub digest.
When installing a supported historical application release, a current launcher
can retain its working updater generation.

For a nearly full Debian or Proxmox LXC filesystem, download the release asset
`hnefatafl-X.Y.Z.tar.gz`, verify its SHA-256 against the GitHub release API, and
extract it into a temporary directory. Run
`bash <extracted>/scripts/linux/install.sh --refresh-updater-only`, then preview
retention and run `hnefatafl-update --cleanup` before the update. In a Proxmox
container, transfer the reviewed bootstrap with `pct push` or `pct exec`. Use
`hnefatafl-update --recover` if the updater reports an interrupted deployment or
backup migration.

The target builds directly at its permanent release path, so the virtual
environment, generated console shebangs and activation scripts keep stable
absolute paths. Publication is the atomic creation of `.release.json`; a directory
without that completion marker is incomplete and is never activated. Before the
marker is written, the updater seals the release as root and verifies Python,
uvicorn, the Hnefatafl console command, account migrations and the real service
import as the application account. The dependency installs, frontend build,
security tests and sealed-release checks run in transient systemd units. Each
preparation gets its own writable `HNEFATAFL_DATA_DIR`, `HOME`, `TMPDIR` and
dependency caches under `/opt/hnefatafl/tmp`. The transient units hide the
production data root. Temporary SQLite databases, jobs, models and caches are
discarded after verification.

Before activation, active/queued training and tournaments are refused. After
stopping the service, activity is checked again, then a full data/config snapshot
is made and verified with file SHA-256 hashes and SQLite integrity checking.
Migrations run before atomic symlink activation. The service restarts and must
pass health, authenticated account/permission/catalog probes and anonymous rejection.
Only then is the release recorded as installed. See the next section for automatic
release/backup retention and storage preflight.

## Storage retention, preflight and cleanup

Application releases and snapshots are replaceable recovery artifacts, not user
data. `/var/lib/hnefatafl/hnefatafl.sqlite3`, `models`, `jobs`, `tournaments`,
uploads, accounts and `/etc/hnefatafl` configuration are never cleanup targets.

After every successful update, backup or recovery (and after the pre-deployment
snapshot), the updater retains at most **three release directories** and **three
verified backups** by default. Existing installations need no manual change:
missing settings use these defaults in memory, while `/etc/hnefatafl/deployment.json`
remains untouched. Configure limits and safety margins there as root:

```json
{
  "repository": "freeDom-/hnefatafl",
  "port": 8000,
  "public_url": "https://tafl.example.org",
  "retention": {"releases": 3, "backups": 3},
  "storage": {
    "backup_directory": "/srv/hnefatafl-backups",
    "headroom_mib": 256,
    "snapshot_margin_percent": 15,
    "cache_headroom_mib": 512,
    "minimum_free_inodes": 2048,
    "release_estimate_mib": 2048,
    "prune_build_dependencies": true
  },
  "recovery": {
    "readiness_attempts": 30,
    "readiness_interval_seconds": 1,
    "recovery_readiness_attempts": 30
  }
}
```

Unknown keys are preserved. Invalid values are refused instead of being silently
coerced. `backup_directory` is optional: the default is the root-owned sibling
`/var/lib/hnefatafl-backups`, outside the application-writable data root. Set it to
place snapshots on a separate filesystem. Preflight evaluates the filesystem
that will hold the snapshots. Automatic pre-deployment snapshots and manual
`hnefatafl-backup` backups share the same three-backup budget.

The first update, backup, recovery or executing cleanup on an older installation
migrates verified backups
from `/var/lib/hnefatafl/backups`. The migration records each source and destination
identity before moving it. On the same filesystem it renames the existing
snapshot. Across filesystems it first applies the shared retention policy to
verified snapshots from both locations, including older snapshots protected by
rollback or recovery metadata. It records the selected copies, eligible deletions
and directory identities before changing storage. Preflight estimates bytes
and inodes for just the retained copies and operational headroom. Verified
destination snapshots beyond retention can make room for the copies. The
migration verifies each destination copy before changing its reference or
removing the source. It prunes other eligible legacy snapshots only after
verification of the retained destination set.

Name conflicts get a deterministic distinct destination. A retention preview
uses the same descriptor-relative checks and shows moves, retained copies with
their estimated storage needs, and snapshots eligible for removal without copying.
If the destination cannot accommodate the retained set, free safe destination
space or expand its filesystem before retrying. Older
incomplete legacy snapshots can be removed from the legacy directory when
their ownership, contents and lack of references are established. Invalid
or ambiguous artifacts remain in place for inspection and contribute no
reclaimable space. If interrupted, `hnefatafl-update --recover` reconciles both
locations and finishes the pending reference updates.

**Protected artifacts are never removed to satisfy a limit:**

- the active release referenced by `/opt/hnefatafl/current`;
- the release and snapshot recorded as the committed rollback target in
  `installed.json`;
- references in an unfinished deployment/recovery `transaction.json` (including
  its recorded prior committed rollback state);
- a release or snapshot currently being prepared, activated, verified or restored;
- a newly requested manual backup for the cleanup pass that follows it.

If more protected artifacts exist than the configured limit, cleanup reports a
retention exception and deletes nothing protected. Releases are ordered by their
recorded preparation/activation time (falling back to directory time for older
installations), not by semantic version alone: an explicitly selected older
release or a rollback reactivation remains a valid newest deployment. Multiple
directories for the same version are reduced to the protected or newest known-good
copy, and a protected copy is never removed merely because a same-version copy
exists.

A verified backup has a complete data/configuration tree, full manifest coverage,
matching SHA-256 hashes and a readable SQLite database where one is present.
Current deployment snapshots use versioned `snapshot.json` metadata with a unique
snapshot and transaction ID, purpose, archive SHA-256, release version and
directory identity, database schema, timestamp and updater ABI. The installed
state and transaction journal store the expected association. A rollback checks
that association against the actual target and snapshot before service downtime.
Genuine legacy snapshots referenced by trusted deployment metadata can still be
verified and restored. Incomplete or malformed association metadata is refused.

Within one operation, content hashes and SQLite checks are reused while the
validated artifact identity and file metadata remain stable. Root-owned staging
records identify downloads, release preparation, pending utility files,
snapshots and restores through publication. Under the operation lock, cleanup
can remove abandoned staging when its ownership and identity still match.
Preparation workspaces, unfinished releases and pending utilities share an
operation identifier. Before removing them, the updater disables pending starts,
stops their recorded transient units and checks the resulting systemd state.
If termination cannot be confirmed, all affected artifacts remain available and
cleanup reports the blocker. Run cleanup again after the units have stopped.
The dry run leaves services and files alone. Unrecognized artifacts, invalid
records and unfinished transaction references remain available for recovery
or inspection.

### Preflight and failure handling

Before downloading a release, the updater estimates extraction, the frozen
virtual environment, npm dependencies, the frontend build and deployment
metadata. It also estimates the archive and the temporary build, cache and
verification peak under `/opt/hnefatafl/tmp`. The archive remains on disk
through extraction and is removed before the build. The preflight counts
those temporary phases by their peak and accounts for updater files at their
staging and installation destinations. Historical release and temporary
workspace peaks, directory and file counts, allocated blocks and configurable
headroom are included.
A separately mounted, root-controlled temporary root is supported. Managed
artifact trees cannot contain nested mounts.

Before stopping the service, the updater reserves space for a completed
snapshot and checks the peak of the SQLite conversion and the possible
recovery. Recovery can require a full restore candidate, database migration
growth, a private copy of live SQLite authority, merge workspace and
transaction metadata. The original data remains on disk if it becomes the
retained recovery directory. Bytes and inodes are checked on the filesystem
that will hold each allocation. The backup root can be on a different
filesystem from the live data and restore staging. The live data root and
its parent must share a filesystem for the atomic swaps during restore.
Filesystems without inode reporting still enforce the byte requirement.

Snapshot creation, restore staging and cross-filesystem backup migration
estimate an independent copy for each filename. Three names that share a
500 MiB inode need room for about 1,500 MiB of copied data. The estimate also
charges the full apparent size of sparse files because a copy can allocate
their holes. It allows for per-file rounding using the destination's reported
allocation unit and adds directory, manifest and SQLite overhead. The same
copy calculation is used for the aggregate legacy migration plan and for
individual copies. Same-filesystem backup relocation still renames the files.

The updater rechecks capacity after retention cleanup, after preparation
and after shutdown before writing the snapshot. A later restore rechecks
the actual snapshot and live database sizes. If safe retention or old cache
maintenance cannot free enough space, predictable shortages are refused
before downtime. Unexpected storage failures report the affected phase and
recovery status. Quotas, filesystem metadata, shared extents, concurrent
allocations and thin-provisioned host storage can affect actual capacity.

The dependency and frontend builds also record peak and final release sizes.
After sealing, the updater removes build-only `frontend/node_modules`,
Vite, test and bytecode caches. The frozen Python environment, `frontend/dist`,
tests and deployment templates remain. The development extra supplies the
release security tests. Operators can set `prune_build_dependencies` to
`false` to keep a rebuild-capable release. uv and npm cache maintenance
uses their supported commands for older caches on the persistent data volume.
Release sealing verifies the hardlinks created by npm when all names for an
inode are inside `frontend/node_modules`. A link outside that tree prevents
publication. This keeps sealing from changing an external file through a link.

#### Preparation measurement

Three real preparations used the locked Python and npm dependencies for the
reviewed 0.42 application source, uv 0.12.23, Node 24.19.0 and Python 3.12.16
on Debian 13.8 in a disposable two-CPU, 4 GiB Docker container. The release
archive was read locally. Network figures are received bytes on the isolated
container interface. Disk peaks are the largest allocated-byte measurements
after preparation steps. RSS is the largest child process measurement.

| Preparation | Wall time | Network received | Temporary disk peak | Release disk peak | User / system CPU | Peak child RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| First, empty caches | 86.90 s | 298,379,631 B | 990 MiB | 1,232 MiB | 73.62 / 24.50 s | 728 MiB |
| Repeat, empty caches | 89.43 s | 298,373,203 B | 990 MiB | 1,232 MiB | 74.77 / 26.53 s | 727 MiB |
| Repeat, experimental warm caches | 87.00 s | 5,072 B | 990 MiB | 1,232 MiB | 75.34 / 17.66 s | 729 MiB |

The running application answered all 825 health probes during the repeat
cold preparation and all 802 during the warm experiment. The median latency
was 3.79 ms cold and 4.02 ms warm. The ten-second idle baseline had 94
successful probes at a 3.31 ms median. The largest observed latencies were
218 ms cold, 240 ms warm and 55 ms idle. These probes used a local Unix
socket and covered health, with production gameplay and proxy performance
outside the measurement.

The warm experiment reduced download volume by about 298 MB and saved
2.43 seconds of wall time in this sample. The deployment uses disposable
caches to avoid retaining roughly 990 MiB and sharing writable build files
between preparations. The experiment used a private disposable cache.
The Docker test dropped privileges to the service account. Native systemd
is exercised separately in CI.

### Administrative cleanup

```sh
hnefatafl-update --cleanup --dry-run
hnefatafl-update --cleanup
```

Dry-run lists managed releases and backups, protection reasons, retention
candidates, validation outcomes and estimated apparent reclamation. The allocated
block estimate counts each hardlinked inode once and excludes blocks still
linked outside the scanned artifact. A sparse file may account for few of those
blocks. The executing form measures the actual filesystem free-space change
and maintains dependency caches. `--check`,
`--list` and `--cleanup --dry-run` leave files and configuration untouched and read
an existing deployment lock without creating or truncating it. They report when
`--recover` is required. The executing cleanup resumes legacy relocation as
needed and holds the exclusive updater lock.

Post-commit retention runs after the application write gate is released. Games
can accept mutations while historical snapshots are verified. Cleanup failures
are reported separately from a completed deployment. Destructive cleanup requires
valid installed and transaction metadata. It validates the owner, permissions,
ancestry and mount topology of each managed root and deletes entries relative to
open directory descriptors. Retained persistent directories stay available for
manual recovery. Staging that contains the deployment gate or belongs to an
unfinished transaction remains protected.

### Releases versus persistent data and off-host copies

Removing a release removes replaceable code, its virtual environment and built
frontend; it never touches the live database, WAL/SHM files, accounts, games,
replays, tournaments, jobs, checkpoints, models, uploads or configuration. Keep at
least one independently tested off-host backup. Local retention protects against
accumulation, not against loss of the container, Proxmox pool or host. Container
free-space checks also cannot guarantee unallocated capacity in an underlying
Proxmox LVM-thin pool: monitor `lvs -a -o vg_name,lv_name,lv_size,data_percent,metadata_percent`
and `pvesm status` on the host.

An exclusive application gate drains admitted HTTP mutations. New mutations receive
503 until the gate is released. Health and authenticated read probes remain
available. The updater validates the gate through a no-follow descriptor and
rejects unsafe file types, ownership, hardlinks and mount redirects. Restore moves
the same locked inode into the verified candidate before publishing it.

### Manual backups and recovery transactions

#### Failed first installation

If the first installation creates SQLite and later fails, recovery can restore
a database-free initial snapshot when the protected journal and verified
snapshot agree on the installation, release and absence of earlier database
authority. The failed attempt's full data tree is retained under
`/var/lib/.retained-<id>`, including its SQLite database, provisioned accounts
and any sidecars. The service stays stopped. The updater reports the retained
path and leaves the installer ready for a retry. Provision administrator
credentials for the retry and keep any credentials from the earlier attempt
private with its retained database.

Run `hnefatafl-update --recover` if the updater was interrupted or automatic
recovery did not complete. The command can resume the data-directory swap
and finalize the journal. An established installation with live SQLite
authority still requires a verified snapshot containing a compatible
database.

`hnefatafl-backup` and `hnefatafl-update --backup` preflight storage before service
shutdown, then recheck actual free space before copying. The journal records the
original service state before shutdown. If it was running, the updater starts it
and checks readiness and authentication after a successful snapshot or a failed
backup attempt. A service that was already stopped stays stopped. A verified
backup is preserved if service recovery fails. The command reports backup and
service errors separately. After an abrupt interruption or failed restart, run
`hnefatafl-update --recover` to restore the recorded service state. Retention runs
with the write gate released and protects a newly verified manual backup.

Restore records the original data root, verified staging root and gate inode. It
retains the original as `.retained-<id>` and then publishes the verified candidate.
The systemd startup check requires the journal's authorized release and data
root. A final journal record makes the restored directory authoritative before
marker removal. Recovery can retry marker cleanup or a pending installed-state
commit. The standalone updater retains live account and online authority using
its verified schema-aware restore modules.

Automatic update and restore require the persistent data root to reside on its
parent filesystem without nested mounts. A bind mount at or below the data root,
including a file mount, is refused before downtime. A manual backup can read a
data root mounted as one filesystem when it contains no nested mounts. Backups
can reside on their own configured filesystem.

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
administration. See [League operations](../game/leagues.md).

Readiness verification uses a bounded, configurable attempt/interval window; recovery
uses its own configured window rather than raising one global timeout. The error
message distinguishes a still-starting unit from repeated failure, reports allowlisted
systemd state and restart count, and only reports successful automatic recovery after
health, authenticated permission/catalog and anonymous-rejection checks pass again.

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
generic readiness message and lacks the new preflight/retention policy. Refresh the
installed utility from the verified target release first (see
[updating](#updating)) when these guarantees are required for that same update.

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

`--recover` follows the operation and phase recorded in `transaction.json`. It can
resume an interrupted explicit rollback, finish an installed-state commit or
clean up a committed restore marker. A new `--rollback` requires the prior
transaction to be resolved first. A rollback verifies the release, matching
snapshot, database compatibility and restore topology before stopping the service.

Recovery before migration keeps the live data and prior committed rollback
references. An explicit rollback records the older release as installed and
preserves any still-valid rollback reference associated with that release in the
snapshot. Recovery that finds the previous release healthy can verify it in
place. The updater keeps all retained data directories for manual reconciliation.

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

### Journal write failures

An update publishes the shutdown and migration intent before proceeding. If a
journal write fails before shutdown, the previous service stays running. If
shutdown already happened and the journal still proves that migration and
activation have not started, the updater can restart the previous service
without replacing live data. The startup guard verifies the recorded release
and data directory identities and the prior installed state. It authorizes the
restart only while the updater holds a lock on that exact journal. A healthy
previous service and still-pending recovery metadata are reported separately.

Preflight budgets for transaction metadata, a 1 MiB reserve and the reserve's
inode on the configuration filesystem. Updates and explicit rollbacks
allocate the single root-owned mode-0600 reserve before stopping the service.
If publishing recovery or commit metadata fails with ENOSPC or EDQUOT, the
updater can release the reserve and retry the write once using the same atomic
replacement and fsync sequence. A later update or explicit rollback replenishes
the reserve after space is available.

Once migration may have changed live data, a failed target service is stopped
when possible. Recovery requires durable intent before any data-directory swap.
If publication still fails, keep the journal, installed state, releases,
snapshots and retained data in place. Free blocks and at least one inode on the
filesystem reported in the error, resolve any quota, or repair filesystem I/O
before running `hnefatafl-update --recover`. Respect the startup guard while
authority is uncertain. Release and data capacity checks still apply when
recovery resumes.

A terminal phase can be visible after the journal was replaced and its parent
directory failed to sync. Before an update or executing cleanup relies on
that record, the updater confirms durability by synchronizing the directory.
Recovery makes the same check before backup relocation or staging cleanup.
If the check fails, the updater keeps the recovery artifacts in place and
reports the affected filesystem. Restore reliable access and run
`hnefatafl-update --recover`. Read-only commands continue to inspect the
visible metadata without synchronizing the directory.

### Cross-schema automatic recovery is fail-closed

Creating an automatic pre-deployment snapshot is **not** a guarantee that an
automatic rollback can complete. After a successful migration the live database
uses a newer schema than the pre-deployment snapshot. Existing account/online
schema-authority checks refuse to replace a schema-26 live database with an older
snapshot, including schema 15, 20, 21, 22, 23, 24 or 25. This preserves authoritative online,
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
[online recovery](../administration/online-operations.md#recovery) before resuming.

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
