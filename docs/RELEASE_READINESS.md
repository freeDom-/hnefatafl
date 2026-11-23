# Initial public release readiness

Status: **not ready for initial public deployment.** This checklist is a
release-preparation report, not a release, tag, version-bump or deployment
authorization.

Reviewed source: merged `main`
`ca1a640fe6b3971cf7f7764083eee19d1598a19a` (2026-11-20, PR #107 merge
following release 0.41.0). The checklist originates from the historical post-PR #84 audit
baseline `2579a86b82ed1eca91f39e7d8064196597e873ed` (2026-11-15). The merged
0.41.0 milestone is a release artifact, not evidence of initial public deployment
readiness, and this checklist does not authorize deployment.
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
| Implemented and merged | [The Great Hall](DASHBOARD.md) totals, polling and hidden-tab suspension (#91); Hall of Renown (`/portraits`) with portrait selection (#86), Deeds of Valor progress and [automatic server-owned tiered achievements](validation/automatic-achievements.md) (#94, #101); signed-out public discovery, live spectating and completed replay (#82, #87); human tournament UI, durable automatic settlement and scheduled starts (#90); Workshop consolidation and bounded job rendering (#92); deferred feature loading and enforced initial bundle budgets (#93); disposable backend read benchmark (#97, benchmark hardening #105); browser-analysis Phase 1 research ([#104](validation/browser-analysis-phase1.md), partially validated); Hall of records navigation (#103); dependency-audit corrections (#100, #102) |
| Implemented on open branches | None in the Arena program at this snapshot. Browser-analysis Phase 1A/1B (#104) and benchmark hardening (#105) merged during this review |
| In development | League seasons, queue and authoritative result accounting in draft PR #99 (proposed schema 22, partial backend, no UI, failing checks at review) |
| Missing | Merged league functionality and UI; production classical/neural analysis execution modes; neural MCTS; combined capacity, recovery and deployment evidence; owner/legal confirmation of contributor aliases and generated-artwork terms |
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

- [x] Owner selected the [PolyForm Noncommercial License 1.0.0](LICENSING.md)
  with SPDX identifier `PolyForm-Noncommercial-1.0.0`. The root
  [LICENSE](../LICENSE), package metadata, README, documentation, frontend notice
  and release packaging include the license. This is not an OSI-approved
  open-source license.
- [x] Inventoried the current third-party runtime dependencies and project
  assets and shipped [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). The
  frontend build generates full runtime package license texts. Original portrait
  provenance and the image-generation tool's terms remain owner confirmation
  items, not settled facts.
- [ ] Owner must confirm that the recorded author aliases belong to the same
  rights holder and resolve the generated-artwork provenance questions in the
  [licensing audit](LICENSING.md#open-questions-for-the-project-owner).
- [x] No CLA, copyright assignment or DCO enforcement is active. Contributors
  retain copyright, and a deferred Harmony CLA proposal is documented only as a
  contingency. Any future activation needs explicit maintainer approval and legal
  review.
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
- [ ] Fresh Python dependency and license inventory with security review for the
  exact release candidate; the current notice covers the locked direct and
  frontend runtime inventory, not every future optional package.

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
  categories, mutation controls or POST/PATCH/DELETE routes. Active and waiting
  games expose no analysis, evaluations, hints, best moves or win probabilities,
  and a public request never generates, recomputes or mutates analysis. Completed
  replays may expose already-stored analysis of the terminal position through the
  existing typed public allowlist; private analysis configuration, model paths,
  seeds, identity runes, diagnostics and private notes remain excluded. Account
  isolation, authorization boundaries and the shared authoritative result services
  remain mandatory for online play and spectators. Existing authenticated gameplay
  permissions and authorized post-game analysis behavior are unchanged.
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

- Existing evidence: an isolated current-engine Pyodide worker (#89), plus merged
  PR #104 revision 5's partially validated Jarl comparison and smoke-model
  ONNX/WASM inference. Neural MCTS is not implemented but Phase 2 is ready to begin
  from the validated WASM inference boundary.
- Not established: competitive trained weights for all rulesets, real
  numerical parity for those weights, browser neural MCTS, completed-search
  strength, CPU/memory/startup/physical-device benchmarks, production CSP/runtime
  packaging/licensing, frontend execution modes and final rollout acceptance.
- The dedicated [browser analysis roadmap](designs/browser-analysis-roadmap.md) and
  revision-5 evidence are merged in PR #104. Current records are
  [Phase 1 validation](validation/browser-analysis-phase1.md), the
  [classical prototype](validation/browser-analysis-prototype.md) and
  [neural feasibility](validation/browser-neural-feasibility.md).
- Server authority, game permissions, private-model access and the restriction on
  assistance in live online games are unchanged. Self-hosting, CSP and runtime/model
  notices are release gates for any shipped executor.

## Decision

Do not announce initial public deployment readiness. The merged 0.41.0 milestone
delivers substantial player functionality, but leagues are not merged, production
analysis has no integration or explicit deferral decision, and combined
performance, recovery, security, accessibility and deployment acceptance has not
been performed. Neural MCTS is a primary but unimplemented roadmap target; it need
not block a server-only or classical-only release if browser analysis is explicitly
deferred. The 2026-11-18 audits are dated observations, not permanent clearance.
The owner must still choose a distribution license and authorize deployment.

Re-evaluate this checklist on the actual release candidate after the remaining
focused PRs. The owner makes deployment decisions for the merged milestone or any
later candidate. This record performs and authorizes no tag, version bump, release
asset or automatic deployment.
