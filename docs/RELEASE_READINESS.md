# Initial public release readiness

Status: **not ready for initial public deployment.** This checklist is a
release-preparation report, not a release, tag, version-bump or deployment
authorization.

Reviewed source: merged `main`
`1b4c4ae5c213fb5ece096db7d2d5b216847b703a` (2026-11-20, PR #105 merge). The
checklist originates from the historical post-PR #84 audit baseline
`2579a86b82ed1eca91f39e7d8064196597e873ed` (2026-11-15).
[Current completeness and dependency graph](designs/arena-current-completeness.md) ·
[Historical audit](designs/arena-completion.md) · [Deployment](DEPLOYMENT.md) ·
[Security](SECURITY.md) · [Release procedure](RELEASING.md)

Merged features have source evidence and green PR checks for their named
revisions. They are not one accepted release candidate: the final combined
acceptance below has not been performed. Historical audit counts and earlier
release evidence are not relabelled as fresh combined validation.

## Delivery status at this review

| State | Scope |
| --- | --- |
| Implemented and merged | [The Great Hall](DASHBOARD.md) totals, polling and hidden-tab suspension (#91); Hall of Renown (`/portraits`) with portrait selection (#86), Deeds of Valor progress and [automatic server-owned tiered achievements](validation/automatic-achievements.md) (#94, #101); signed-out public discovery, live spectating and completed replay (#82, #87); human tournament UI, durable automatic settlement and scheduled starts (#90); Workshop consolidation and bounded job rendering (#92); deferred feature loading and enforced initial bundle budgets (#93); disposable backend read benchmark (#97, benchmark hardening #105); Hall of records navigation (#103); dependency-audit corrections (#100, #102) |
| Implemented on open branches | Browser analysis Phase 1A full-strength Jarl and Phase 1B trained smoke-model ONNX/WASM parity in PR #104 revision 5; integration work is not merged |
| In development | League seasons, queue and authoritative result accounting in draft PR #99 (proposed schema 22, partial backend, no UI, failing checks at review) |
| Missing | Merged league functionality and UI; production classical/neural analysis execution modes; neural MCTS; combined capacity, recovery and deployment evidence; distribution license and third-party notices |
| Deferred | Browser-neural production integration is optional for the initial public release; durable UTC login/activity-day streaks remain excluded |
| Final combined acceptance | **Not performed.** No release candidate has been evaluated as one system across performance, recovery, security, localization, accessibility and deployment |

Delivered functionality still has to be re-verified as part of the combined
release candidate. Do not read a merged PR's green matrix as release acceptance.

## Blocking product and evidence gaps

- [ ] League policy, result accounting, queue, season lifecycle, corrections and
  recovery are not merged. Draft PR #99's schema 22 and backend are not production
  functionality; its security and Python checks failed at review.
- [ ] Production browser analysis has no execution-mode integration. Decide
  explicitly whether a full-strength Jarl executor ships in the initial release or
  is deferred; any shipped executor needs integration and regression acceptance.
  Neural MCTS is a primary analysis target but is not implemented.
- [ ] Combined performance on the actual release candidate: route/page latency,
  request traces, 1/10/50-client p50/p95, mixed writes, bounded concurrent
  analysis, league workloads, realistic data volumes and production capacity.
- [ ] Final combined visual, localization and accessibility QA across all merged
  surfaces: four themes, English/German, phone/tablet/desktop/4K, genuine 200%
  zoom, keyboard/focus and contrast. Physical mobile-browser limits are recorded
  honestly rather than assumed.
- [ ] Combined regression QA across merged features, including account switches,
  permissions, replay, tournaments, Workshop, public watch and achievements.

## Licensing and dependencies

- [ ] Owner selects the public distribution license. No top-level LICENSE/NOTICE
  exists. Do not infer a license from GitHub visibility or select one silently.
- [ ] Inventory third-party code, fonts, art and runtime redistribution
  obligations and ship notices; preserve original generated-art provenance. If
  Pyodide/ONNX runtimes ship, review the exact pinned package and distribution
  licenses, including model-publication rights.
- [ ] Rerun the full and production-only dependency audits before release. On
  2026-11-18 both reported zero findings and GitHub had withdrawn
  GHSA-fc5q-39xc-vjpf. Withdrawal is not an upstream technical patch; the unused
  development-only acceptance was removed by merged PR #100 and approval metadata
  validation was hardened by merged PR #102. PR #96 was closed unmerged and is not
  a prerequisite. The active exception list is empty. The historical owner
  approval (PR #95, 2026-11-18) and exposure analysis remain preserved in the
  [dependency record](validation/test-toolchain-security.md#withdrawal-and-empty-exception-state-2026-11-18).
  The fail-closed `security` job must remain: returning or new findings require
  remediation or separate owner review. Do not treat the 2026-11-18 clean audits
  as permanent, and rerun audits after any lockfile change.
- [ ] Fresh Python dependency and license inventory with security review.

## Security and abuse resistance

- [ ] Fresh authorization inventory and anonymous/unverified/verified/admin/revoked
  permission matrix, including account switches and late requests.
- [ ] CSRF, origin, body-size, session-expiry and account-freeze controls retained
  for every mutation; optimistic version guards preserved.
- [ ] Live online gameplay exposes no analysis, move suggestions, win
  probabilities, legal-move assistance or other unauthorized assistance.
  Authenticated participants retain authorized gameplay mutations, including
  submitting a move and conceding, through the existing permission, CSRF and
  optimistic version checks. Only the seated player whose turn it is may act.
- [ ] Public spectator surfaces remain strictly read-only: no private game
  categories, mutation controls, POST/PATCH/DELETE routes or analysis. Account
  isolation, authorization boundaries and the shared authoritative result services
  remain mandatory for online play and spectators.
- [ ] Public profile redaction and safe portrait IDs verified; no
  account-controlled asset paths, email, full birth dates, CSRF tokens or private
  moderation notes leak.
- [ ] Registration/login/reset/email and public-IP limits tested under exhaustion;
  protected analysis CPU quotas, concurrency and cancellation reviewed under load.
- [ ] Queue, tournament and league duplicate events, concurrent writes,
  corrections, cancellations, freeze and restart recovery covered by database tests
  once league tables exist.
- [ ] Production HTTPS/host/proxy/cookie settings and CSP verified against the
  actual reverse proxy; do not add broad `unsafe-eval` or un-reviewed Wasm
  permissions merely to make an experiment run.
- [ ] Owner action, not applied: add required status checks `security`, `python`,
  `node22` and `browser` to the active main-branch ruleset. Verified on 2026-11-20,
  ruleset `25820905` has no required status checks and zero required approvals, so
  green CI is voluntary rather than GitHub-enforced. See the
  [required-checks recommendation](validation/test-toolchain-security.md#required-main-branch-checks-owner-action-not-applied).
  Do not modify the ruleset from this documentation change.

## Data, installation and recovery

- [ ] Fresh install using locked dependencies and the canonical schema on a
  disposable directory, with explicit secure admin initialization.
- [ ] Inventory valuable development database/WAL, accounts, games, jobs, Optuna
  studies, full checkpoints and playable model copies before any destructive work.
- [ ] Exercise stopped-writer backup/export and restore into a disposable
  directory; verify game/replay, identities, progression, tournaments, model hashes
  and actual training resume. Include league/settlement tables in retain/restore
  inventories once PR #99 or another league change merges.
- [ ] Test disk-full, interrupted-export and corrupt-backup failures; originals stay
  intact.
- [ ] Schema cleanup documents incompatible old formats and a recovery path. "No
  public compatibility requirement" never means silently deleting development data.
  Canonical migration head on this merged `main` is schema 21; do not reserve or
  reuse a future number from older documentation.

## Operations and capacity

- [ ] Deploy a staging instance with a non-root process, explicit storage
  ownership, health checks, structured scrubbed logs, bounded error responses and
  monitoring.
- [ ] Keep registration and online admission defaults off until operator setup.
  Public discovery is implemented and enabled by default but can be disabled;
  document its rate limits and privacy boundaries.
- [ ] Measure SQLite indexes/query plans, WAL/checkpoint/backup procedure and disk
  maintenance with realistic records. Keep one API process until shared scheduler
  and rate-limit ownership is established; do not promise multiworker safety.
- [ ] Repeatable 1/10/50-client workloads recording p50/p95, errors, request/SQL
  rates, CPU/RSS and store size for The Great Hall, lobby, live games, bracket,
  queues, statistics/leaderboard, replay and bounded analysis. No unmeasured
  capacity claim.
- [ ] Test SMTP, timeout sweeper, durable tournament/league workers, retry/failure
  alerts and maintenance recovery without an open browser.
- [ ] Complete GitHub CI (`security`, `python`, `node22`, `browser`) green on the
  actual candidate; disclose exact skips and warnings.

The existing evidence does not cover these gates. PR #97 measures 936 warm loopback
GETs from eight accounts at concurrency 1/4/8 (p95 9.74/31.69/59.36 ms) on a small
seeded fixture; #105 later merged benchmark-only mountinfo hardening without a new
measurement. It does not cover mixed writes, league workloads, bounded
concurrent analysis, realistic data volumes, WAN/TLS or deployment capacity.
PR #93 measures the initial production bundle (JS 569,155 raw / 181,828 gzip bytes
at `ab3ac68`; CSS 114,187 / 23,803) and enforces a 200,000 gzip-byte JavaScript
budget. That is a build budget and transfer-size proxy, not measured page-load
latency or server capacity. The original 180 KB gzip target is retained as a
distinct target rather than silently replaced by the 200,000-byte ceiling.

## Browser-side analysis

The established direction has two primary targets: full-strength Jarl classical
alpha-beta analysis and a genuinely trained neural network with MCTS. Neural MCTS
is a primary target; browser-neural production integration remains optional for the
initial public release.

- Existing evidence: an isolated current-engine Pyodide worker (#89), plus open
  PR #104 revision 5's partially validated Jarl comparison and smoke-model
  ONNX/WASM inference. Neural MCTS is not implemented but Phase 2 is ready to begin
  from the validated WASM inference boundary.
- Not established: competitive trained weights for all rulesets, real
  numerical parity for those weights, browser neural MCTS, completed-search
  strength, CPU/memory/startup/physical-device benchmarks, production CSP/runtime
  packaging/licensing, frontend execution modes and final rollout acceptance.
- The dedicated roadmap and revision-5 evidence live on open PR #104
  ([roadmap](https://github.com/freeDom-/hnefatafl/blob/1f03202e66878048ed76cdad30409f8989a20c73/docs/designs/browser-analysis-roadmap.md),
  [PR](https://github.com/freeDom-/hnefatafl/pull/104)); local records are the
  [classical prototype](validation/browser-analysis-prototype.md) and
  [neural feasibility](validation/browser-neural-feasibility.md).
- Server authority, game permissions, private-model access and the restriction on
  assistance in live online games are unchanged. Self-hosting, CSP and runtime/model
  notices are release gates for any shipped executor.

## Decision

Do not announce public readiness. Merged features are substantial, but leagues are
not merged, production analysis has no integration or explicit deferral decision,
neural MCTS is not implemented, and combined performance, recovery, security,
accessibility and deployment acceptance has not been performed. The 2026-11-18
audits are dated observations, not permanent clearance. The owner still must choose
a distribution license and authorize public release.

Re-evaluate this checklist on the actual release candidate after the remaining
focused PRs. The owner makes the merge and release decisions. No tag, version bump,
release asset or automatic deployment is part of this record.
