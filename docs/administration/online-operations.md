# Online operations

[Administration index](README.md) · [Online play](../game/online-play.md) · [Online authority](../development/online-authority.md) · [Configuration](../installation/configuration.md)

## Admission and configuration

All online/public variables and defaults are canonical in
[configuration](../installation/configuration.md#online-play-and-public-discovery). This page assumes the configured admission, quota, clock and public-discovery values.

Enable admission only after accounts, clocks, result finalization, backups and
reverse proxy are verified. Registration and online admission are independent.
A two-game match counts as one creation; cancelling does not refund the budget.

## Monitoring

Operational signals:

- `GET /api/online/monitoring` (administrator) for admission, deadlines and backlog.
- `GET /api/online/timeout-gate` and `PATCH` to inspect/pause finalization.
- `GET /api/health` for HTTP readiness.
- Service journal for deadline sweeps, settlement and worker errors.
- League administration health for queue/settlement/rebuild state.

Pausing timeout finalization is an incident/recovery control. It does not cancel
games or stop ordinary moves; restore it promptly. A worker restart resumes durable
queue/outbox work; do not delete rows to force progress.

## Results, corrections and voids

The server owns result revisions. Administrators can void an online result under
the audit path; the effective revision drives rating and league settlement. A void
does not delete the recorded game or replay. Corrections/voids are not a way to
rewrite history silently; preserve the audit trail.

League settlement uses the authoritative result ledger. A rebuilding season keeps
permanent achievements visible while temporary seasonal values are hidden. If a
rebuild/retry backlog keeps growing, capture logs/backlog age, verify storage and
take a supported backup before repairing on a disposable copy.

## Recovery

- Deadline finalization and league/tournament workers are lifespan-owned; restart
  resumes due work from durable state.
- A deployment lock defers pending writes during update/rollback.
- On an interrupted update, use the documented `--recover`/rollback procedure; do
  not copy a live SQLite file without its WAL.
- See [league recovery](../installation/updating.md#league-worker-recovery) and
  [updating](../installation/updating.md).

## Public discovery

Public Watch Hall discovery is enabled by default. Set
`HNEFATAFL_PUBLIC_ONLINE_ENABLED=false` to return not found for the public namespace
and hide the Watch Hall. Authenticated play is unaffected. Public reads are GET-only
and never include private history, analysis or account data. See
[public API](../development/api/README.md#progression-and-public-spectator).

## Implementation boundary

Lifecycle, deadline transactionality, result revisions, authorization and API
semantics are in [online authority](../development/online-authority.md). This page
is the operator runbook.
