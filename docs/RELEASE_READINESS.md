# Initial public release readiness

Status: **not ready for initial public deployment** at audited baseline
`2579a86b82ed1eca91f39e7d8064196597e873ed` (2026-11-15). This checklist is a
separate release-preparation report, not a release/tag/version authorization.
[Completion matrix and dependencies](designs/arena-completion.md) ·
[Deployment](DEPLOYMENT.md) · [Security](SECURITY.md) · [Release procedure](RELEASING.md)

Post-audit delivery: **Portrait Hall, achievement claiming and selection are
delivered by PR #86** and its checks are not re-run here. The human tournament
backend (schema 18) already existed at the baseline; automatic settlement, leagues,
the signed-out spectator frontend, Workshop consolidation and local-analysis
research remain outstanding. Checkbox state below records the audited baseline.

## Blocking product and evidence gaps

- [ ] Portrait Hall/claim/select/profile integration complete and accessible
  (delivered by PR #86; verify that PR's evidence before release).
- [ ] Human tournaments have automatic durable server settlement and complete UI.
- [ ] Signed-out public discovery/live viewing/replay works independently of Auth.
- [ ] League policy, result accounting, queue, season lifecycle and recovery tested.
- [ ] Workshop consolidation preserves every important parameter and operation.
- [ ] Bundle/request/load budgets measured before and after changes.
- [ ] Local-analysis research has a documented go/no-go. Neural browser execution
  remains optional; absence must not block a justified classical-only/server MVP.
- [ ] All major surfaces reviewed in four themes, EN/DE, phone/tablet/desktop/4K
  and 200% zoom; physical mobile browser limitations recorded honestly.

## Licensing and dependencies

- [ ] Owner selects public distribution license. No top-level LICENSE/NOTICE exists
  at the baseline. Do not infer a license from GitHub visibility or silently choose.
- [ ] Inventory third-party code/fonts/art/runtime redistribution obligations and
  include notices; preserve original generated-art provenance. If Pyodide/ONNX
  runtimes ship, review the exact pinned package/distribution licenses too.
- [ ] Resolve material development-toolchain advisories with tested upgrades.
  Fresh baseline npm audits report **zero runtime findings** and three affected
  development packages: direct Vitest (critical), tinypool (critical),
  @vitest/mocker (moderate). These are affected-package counts, not unique advisory
  counts. No blind force upgrade; rerun tests and audit after lockfile changes.
- [ ] Python dependency/license inventory and fresh security review recorded.

## Data, installation and recovery

- [ ] Fresh install using locked dependencies and canonical schema tested on a
  disposable directory, with explicit secure admin initialization.
- [ ] Inventory valuable development database, WAL, accounts, games, jobs, Optuna
  studies, full checkpoints and playable model copies before any destructive work.
- [ ] Exercise stopped-writer backup/export and restore into a disposable directory;
  verify game/replay, identities, progression, tournaments, model hashes and actual
  training resume. Include league/settlement tables in retain/restore inventories.
- [ ] Disk-full/interrupted-export/corrupt-backup failures tested; originals intact.
- [ ] Schema cleanup explicitly documents incompatible old formats and recovery
  path. “No public compatibility” never means silently deleting development data.

## Security and abuse resistance

- [ ] Fresh authorization inventory and anonymous/unverified/verified/admin/revoked
  permission matrix, including account switches and late requests.
- [ ] CSRF/origin/body-size/session expiry/freeze controls retained for every mutation.
- [ ] Online and public pages provide no active-game analysis or private categories.
- [ ] Public profile redaction and safe portrait IDs verified; no account-controlled
  asset paths, email, full birth dates, CSRF or private moderation notes leak.
- [ ] Registration/login/reset/email and public-IP limits tested under exhaustion;
  protected analysis CPU quotas/concurrency and cancellation reviewed under load.
- [ ] Queue/tournament/league duplicate events, concurrent writes, corrections,
  cancellations, freeze and restart recovery covered by database tests.
- [ ] Production HTTPS/host/proxy/cookie settings and CSP verified against actual
  reverse proxy; never add unsafe-eval merely to make an experiment run.

## Operations and capacity

- [ ] Deploy a staging instance with non-root process, explicit storage ownership,
  health checks, structured scrubbed logs, bounded error responses and monitoring.
- [ ] Registration and online admission defaults remain off until operator setup;
  public discovery is enabled by default but can be disabled. Document that public
  APIs already exist even before a signed-out frontend does.
- [ ] SQLite indexes/query plans, WAL/checkpoint/backup procedure and disk maintenance
  measured with realistic records; keep one API process until shared scheduler/
  rate-limit ownership is established. Do not promise multiworker safety.
- [ ] Repeatable 1/10/50-client workloads record p50/p95, errors, request/SQL rates,
  CPU/RSS and store size for Dashboard, lobby, live games, bracket, queues,
  statistics/leaderboard, replay and bounded analysis. No unmeasured capacity claim.
- [ ] SMTP, timeout sweeper, durable tournament/league workers, retry/failure alerts
  and maintenance recovery tested without an open browser.
- [ ] Complete CI is green on the actual candidate; exact skips/warnings disclosed.

## Decision

Do not announce public readiness from PR #84's green UI CI. Missing feature UI,
automatic tournament progress, leagues, the owner's license decision and capacity/
recovery/deployment evidence are outstanding. Re-evaluate this checklist after the
focused completion PRs; the owner makes the merge and release decisions. No tag,
version bump, release asset or automatic deployment is part of this audit.

The automatic-award revision in PR #94 supersedes manual collection. Its separate
[validation record](validation/automatic-achievements.md) does not change the
historical audit evidence above.
