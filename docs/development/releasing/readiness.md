# Release readiness

[Releasing](README.md) · [Arena roadmap](../roadmap/arena.md) · [Browser roadmap](../roadmap/browser-analysis.md) · [Installation](../../installation/README.md) · [Security](../../administration/security.md)

Status: **no production release candidate or initial public deployment is
authorized by this checklist.** Publishing a milestone release, accepting a
production release candidate and authorizing public deployment are separate
decisions. A merged PR or green CI run is not combined production acceptance.

Current implementation snapshot: League Arena is merged; browser-analysis Phase 5
transparent integration is implemented behind the deployment policy; neural MCTS is
implemented as an opt-in, non-production-accepted path. Outstanding acceptance gates
are listed below.

## Product scope and deferrals

- [ ] Confirm the authorized release scope and user-visible limitations.
- [ ] Explicitly decide whether browser-local analysis is shipped disabled,
      server-only, browser-first or deferred. It must not be presented as
      production-accepted while competitive/device/offline gates remain open.
- [ ] Keep offline play, distributed self-play, PostgreSQL migration and
      activity-day streaks deferred unless separately authorized.

## Security, authorization and privacy

- [ ] Fresh anonymous/unverified/verified/administrator/revoked permission matrix,
      including account switching and late requests.
- [ ] CSRF, Origin, body-size, session-expiry and freeze controls retained on every
      mutation; optimistic version guards preserved.
- [ ] Live online games expose no analysis or assistance. Public spectators are
      GET-only and cannot generate/mutate analysis. Completed public replays may
      expose already-stored terminal analysis through the typed allowlist; private
      configuration/models/seeds/diagnostics remain excluded.
- [ ] Public profile redaction, portrait allowlist and no email/full birth-date/
      session/moderation leakage verified.
- [ ] Registration/login/reset/mail and public-IP limits tested under exhaustion;
      analysis quotas/concurrency/cancellation reviewed under load.
- [ ] Production HTTPS/host/proxy/cookie/CSP settings verified against the real
      reverse proxy.

## Licensing and owner decisions

- [x] The selected distribution license is the PolyForm Noncommercial License 1.0.0
      (see [LICENSING.md](../../LICENSING.md)); this selection does not itself
      authorize deployment.
- [x] Third-party runtime inventory and [THIRD_PARTY_NOTICES.md](../../../THIRD_PARTY_NOTICES.md)
      are shipped; the frontend build emits package license texts.
- [ ] Copyright ownership and generated-artwork provenance still require owner
      confirmation. Recorded author aliases and image-tool terms require owner
      confirmation.
- [ ] No CLA/DCO is active; contributors retain copyright. Any future CLA needs
      explicit maintainer approval and legal review.
- [ ] Rerun full and production-only dependency audits on the candidate. The current
      exception list is empty; the security job must remain fail-closed.
- [ ] Confirm repository merge gates: required `security`, `python`, `node22` and
      `browser` checks (owner action; do not change rulesets in a docs PR).

## Data integrity and compatibility

- [ ] Fresh install with locked dependencies and the canonical schema on disposable
      storage, including secure admin initialization.
- [ ] Inventory database/WAL, accounts, games, jobs, studies, checkpoints, models and
      backups before destructive work.
- [ ] Stopped-writer backup/export and restore into a disposable directory; verify
      games/replays, identities, progression, tournaments/leagues, model hashes and
      training resume. Test disk-full, interrupted export and corrupt backup while
      preserving originals.
- [ ] Document incompatible old formats and a recovery path; never silently delete
      development data.

## Installation, update and recovery

- [ ] Staging deploy with non-root service, explicit storage ownership, readiness
      checks, bounded logs/errors and monitoring.
- [ ] Registration and online admission default off until configured; public
      discovery default enabled is documented with a disable path.
- [ ] Verify install, update, rollback, recovery journal, backup retention and
      cross-schema refusal on the candidate. `deployment-cross-version` must pass.
- [ ] Verify SMTP, timeout sweeper, league/tournament workers, retries and recovery
      without an open browser.

## Performance, capacity and UI

- [ ] Repeatable 1/10/50-client mixed workload (Hall, lobby, live game, bracket,
      queues, statistics/leaderboard, replay, bounded analysis) with p50/p95, errors,
      CPU/RSS and store size. No unmeasured capacity claim.
- [ ] Four themes, English/German, phone/tablet/desktop/4K, 200% zoom, keyboard/focus,
      contrast and no horizontal overflow on merged surfaces.
- [ ] Cross-feature regression: account switches, permissions, replay, tournaments,
      Workshop, public watch and achievements.
- [ ] Physical desktop/mobile/Safari/Firefox and CUDA/MPS/OpenVINO/GPU execution
      validated where claimed; browser emulation and skipped hardware tests are not
      physical/GPU evidence.

## Browser-local analysis gates

- [ ] Competitive 32×2+ checkpoints for all rulesets, numerical parity and
      playing-strength evidence.
- [ ] Physical devices/browsers, verified WebGPU execution, sustained memory,
      offline-readiness and distribution licensing.
- [ ] Controlled rollout and production acceptance; server-analysis decommissioning
      remains deferred until then.

## Release and deployment authorization

- [ ] Candidate Verify CI green, including `deployment-cross-version`, with exact
      skips/warnings disclosed.
- [ ] Release notes state delivered behavior, exact validation and limitations.
- [ ] Separate owner authorization obtained for the tag/release, production
      acceptance and initial public deployment. Initial public deployment remains
      unauthorized by this checklist.
