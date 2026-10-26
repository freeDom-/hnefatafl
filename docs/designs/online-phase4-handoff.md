# Online Phase 4 handoff

[Documentation index](../README.md) · [User guide](../ONLINE_PLAY.md) ·
[Operations and policies](../ONLINE.md) · [Phase 3 validation](../validation/online-phase3.md)

This is the incoming Phase 3 contract. Phase 4 subsequently implements the
owner-approved guest boundary described in [guest play and storage](../GUEST_PLAY.md);
see [Phase 4 evidence](../validation/online-phase4.md) and the
[Phase 5 handoff](online-phase5-handoff.md) for current follow-up decisions.

Phase 3 provides authenticated lobby/game routes on top of the durable Phase 2
core. Its only backend addition is `GET /api/online/availability`, an exact `play`
grant returning `{enabled, eligible}` from current settings and
`security_context.online_eligible`. This is a presentation hint, never an admission
capability. Every write reauthorizes normally. No migration, lifecycle, deadline,
quota or production configuration change accompanies it.

`Online.tsx` owns view state, frozen creation intents, selection and local replay.
`onlineTypes.ts` describes safe projections without local-game analysis fields.
`useOnlinePoll.ts` coordinates reads with cancellation, one queued refresh,
revision guards, five-second normal polling, ten-second request deadlines and
bounded error backoff. Mutations pause reads, submit once and reload. No online ID
is loaded into App's local-game adapter or persisted as its selected local game.
The existing Board accepts a presentation-only shape; online supplies no legal
moves or hints. Auth generations plus account-keyed remounts protect account
changes and logout, including delayed unauthorized responses.

The isolated `test:online` harness uses ordinary account login and the real API.
Its separate loopback test controller changes only disposable configuration,
verification and deterministic time. The application has no fixture routes or mail
capture mode. New acceptance checks run in the existing browser CI job. Keep these
checks when replacing polling or routing later; mocks alone cannot establish
multiplayer acceptance.

## Decisions and rollout prerequisites

The next phase's exact product scope still requires owner approval. In particular:

- Anonymous/public lobby or spectator access remains unapproved and disabled.
- Waiting-game retention/lifetime requires an explicit policy. Current durable
  records plus transactional creation quotas are not an expiry/retention policy.
- Rated eligibility, Elo, ladder/rewards and aggregate match scoring are absent.
- Approve an outage/maintenance resolution procedure before production timeout
  forfeits. Both the configuration prerequisite and persistent timeout gate must
  permit finalization; the UI never changes them or extends a deadline.
- Production admission enablement, deployment, migration and release remain separate
  owner actions. Follow schema-10 backup/restore and single-API-process guidance.
- Offline/guest gameplay is not implemented by this phase. Preserve the Phase 0
  feasibility evidence if offline work is selected next; do not route online IDs
  into browser/local analysis to implement it.

Keep admission/timeout defaults disabled. No deployment, production mail, real
accounts, GPU/hardware or strength validation is established by Phase 3 browser
fixtures. Completed-game analysis would need a separately approved lifecycle/cache/
export boundary; active assistance must stay blocked for all roles.
