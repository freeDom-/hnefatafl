# Deployment, updater and recovery internals

[Development index](README.md) · [Updating runbook](../installation/updating.md) · [Installation](../installation/installation.md) · [Testing](testing.md)

This page records the mechanisms behind the operator update/recovery runbook. For
commands, warnings and incident procedures use [Updating](../installation/updating.md).

## Utility generations

- `hnefatafl-update` is a stable launcher selecting the generation in `active.json`.
- Each generation contains `deployment.py` and schema-aware restore modules.
- Publication compiles/imports the candidate, checks wrapper syntax, fsyncs it, then
  switches one atomic pointer; the previous generation remains available.
- Generations declare `UPDATER_ABI` and a minimum launcher protocol. Launcher
  replacement is atomic while the old generation runs; ABI/protocol advances one
  compatible step at a time and require reading predecessor transaction/state formats.
- The two command wrappers have a fixed contract; verified releases carry canonical
  bytes. A wrapper-contract change needs a reviewed migration preserving both forms.

## Locks, journals and activation

- The installer/updater use an exclusive deployment operation lock. The API lifespan
  defers writes while the lock is held so no worker mutates data during replacement.
- Releases activate through an atomic release pointer/symlink; migrations run before
  activation. The service restarts only after a verified release is active.
- `transaction.json` records shutdown/migration/activation intent, release/data
  identities, snapshot/transaction ID, checksums, versions and recovery state.
- Journal publication uses atomic replacement + fsync; a failed pre-shutdown write
  leaves the old service running. Post-shutdown recovery is allowed only while the
  journal/lock/state prove migration/activation did not start.
- A root-owned reserve (1 MiB + inode) allows one retry when journal writes hit
  ENOSPC/EDQUOT; it is replenished after space is available.
- A visible terminal journal whose parent directory failed to sync is re-synced before
  cleanup/recovery relies on it.

## Retention, snapshots and schema authority

- Release/backup retention is configurable (`releases`, `backups`; off-host backup
  directory optional). Cleanup is locked and preserves the active/previous release,
  unfinished transaction references and verified snapshots.
- Pre-deployment snapshots include the database, configuration and protected state.
  A pre-deployment snapshot is not automatically a downgrade target: a migrated live
  database with a newer schema refuses replacement by an older snapshot.
- Read-only compatibility preflight runs before explicit rollback or automatic
  recovery; incompatible live/snapshot schemas fail closed. Manual recovery preserves
  the journal, snapshot, retained data and target release.
- Recovery keeps live authoritative online/league state when overall schemas match,
  pauses timeout finalization with an audit entry and retains post-snapshot data in a
  sibling `retained-<id>` directory. It never silently merges arbitrary game/model
  data or downgrades the database.

## Invariants

Preserve data over code convenience. Never force a schema downgrade, bypass the
deployment lock, delete an unfinished journal, or start arbitrary old code against an
unknown schema. Operational commands and exact failure handling remain canonical in
[Updating](../installation/updating.md); release publication is in
[Releasing](releasing/README.md).
