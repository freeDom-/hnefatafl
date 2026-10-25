# Phase 0 online lifecycle and result design

[Documentation index](../README.md) · [Source assessment](online-phase0-assessment.md) ·
[Registration brief](registration-phase1.md) · [Offline spike](../../spikes/browser_offline/README.md) ·
[Validation and handoff](../validation/online-phase0.md)

This is a future integration contract. Only [lifecycle.py](../../hnefatafl/lifecycle.py)
and its [deterministic tests](../../tests/test_lifecycle.py) are implemented here;
production handlers, schema, training, history and deployment remain unchanged.
The contract trusts authenticated adapter identities and engine-validated states.

## Confirmed requirements and pending owner decisions

Confirmed: distinct players, server-validated play, time per move default 3 days,
minimum 1 minute, maximum 14 days; fresh full duration after a non-terminal move;
timeout loses, only the timed-out player gains an abandoned-games count; no explicit
abandon action; no analysis/chances for running online games; separate online and
account-single-player accounting; anonymous single-player computes locally and
retains review of only its active/last game.

Recommendations **requiring owner approval**:

| Decision | Initial recommendation |
| --- | --- |
| Sides | Creator explicitly selects attackers or defenders while creating a waiting game; joiner takes the other side. Display first mover from the ruleset, never assume creator starts. No reroll after joining. |
| Waiting configuration | Freeze ruleset/version, creator side, per-move seconds, rated intent and visibility at creation. To change, cancel and create another lobby. No initial position customization or handicap for rated play. |
| Waiting visibility/lifetime | Public readable lobby and board, with bounded pagination; no automatic lobby expiry initially. Creator cancellation and admin voiding remain available. |
| Rated eligibility | Verified active distinct accounts, standard initial board, supported pinned ruleset, rating intent fixed before joining, no assistance, no admin void; require both participants to see rated status before joining. |
| Outage handling | See outage proposal below; approve before enabling automatic production forfeits. |
| Offline retention | Single local slot, retained on reload, replaced only by explicit new-game confirmation; see offline policy below. |
| Ratings | Per-ruleset online Elo, with parameters/provisional policy approved before implementation. |

Whole-second time-control precision is the Phase 0 contract choice: accept only
actual integer seconds, inclusive range 60–1209600, default 259200. Reject booleans,
strings, floating-point numbers (even `60.0`) and fractional/ambiguous durations.
Use the same strict validation at future API and persistence boundaries. Timestamps
retain microseconds, so integer durations do not round deadline boundaries.

## State and transitions

Engine winner/reason stays separate from lifecycle status. Current engine outcomes
map to **Completed**; existing records with no winner do not automatically map to
an online Waiting/Active game. Existing local game modes remain unchanged.
Concession/timeout do not fabricate an engine board outcome: online responses and
controls must use the lifecycle/effective result, not only `State.winner`. Online
replay navigation is per-viewer read-only state; never apply today's shared-session
seek/undo/branch mutations to an active competitive game.

| From → to | Actor | Preconditions and effect |
| --- | --- | --- |
| creation → Waiting | Eligible authenticated creator | Valid immutable configuration, creator assigned a side; no opponent, start or deadline. |
| Waiting → Active | Eligible authenticated other account | Empty opponent seat, distinct creator/joiner, atomic join; fix identities/sides, start time and first deadline together. |
| Waiting → Cancelled | Creator | No start/competitive result; reject subsequent joins. |
| Active → Active | Player to move | Before deadline, server accepts a legal non-terminal engine move; append exactly one ply and renew next deadline. |
| Active → Completed | Player to move | Timely concession or engine-terminal accepted move; publish one effective result. |
| Active → Completed | Request expiry check or sweeper | At/after deadline: timeout, opponent wins, timed-out account alone marked abandoned. |
| Waiting/Active/Completed/Cancelled → Voided | Administrator | Explicit reason/audit actor; withdraw eligibility and replace a recorded result by an ineligible revision. Pre-start void has no competitive result. |
| Any → same | Connection close/disconnect/logout | No lifecycle transition and no deadline adjustment. |

Only the player to move can move **or concede**; admin privilege does not impersonate
a seat. Spectators cannot mutate. Terminal states reject gameplay and joining;
cancel rejects repeated cancellation. `expire` returns the identical state and no
effects when waiting, not due or terminal. Authorized repeated `void` is also a
stable no-op. A late eligible move/concession returns the timeout transition; its
requested action is not accepted. Wrong actors are rejected before any mutation;
independent authorized expiry processing can still finalize the game.

The pure contract explicitly models actor checks, whole-second durations,
activation, renewal, expiry, cancellation, concessions, engine outcomes and void
revisions. `accept_move` consumes a trusted next `State`, with basic ruleset/ply/turn
consistency checks; it does not prove move legality or origin. Adapters must call
the engine and authorize before passing this value. It must never be deserialized
from a browser's asserted winner. Only contract constructors/transitions are
supported; arbitrary `dataclasses.replace` of contract state is not a public API.

## Deadlines, abandonment and outage policy

Deadline = activation time + duration for the first mover. Each accepted
non-terminal move sets deadline = authoritative acceptance time + full duration
for the other player. There is no accumulated time or increment. At `now >=
deadline`, a move or concession is too late. Check expiry before engine work and
recheck under the transaction lock. A late concession must never replace timeout
with concession. Terminal moves/concessions clear the active deadline.

Use aware UTC datetimes in the pure contract (normalize incoming aware offsets;
reject naive time and backwards transition time). Proposed durable representation:
integer Unix microseconds, stored/compared consistently, rendered as UTC ISO 8601
with `Z` in JSON. Never mix local datetime strings, milliseconds and seconds.
Sample server wall time **after acquiring the write lock** to arbitrate an action;
client send time, browser countdown and packet arrival are not authoritative.
Monitor clock synchronization; fail closed on detected backwards-clock anomalies
pending operational resolution. Do not infer an early client action from its clock.

Persist the deadline and start before acknowledging activation or a move. Restart
does not reset either. A bounded sweeper queries overdue Active games on startup
and periodically; request and sweeper call the same expiry function inside the
same transaction pattern. Late sweeps record completion at the persisted deadline,
with a separate processing/revision timestamp. There is no expiry for Waiting.

Use **abandoned games** for the player-facing count. Technical reason remains
`timeout`. Timely concession is a loss with no abandonment. Disconnect, closing a
browser and logout have no immediate effect. Cancellation and voided games have
no competitive contribution. Timeout is an outcome, not evidence of malicious intent.

**Outage proposal, approval required:** retain actual persisted deadlines; do not
automatically shift them in Phase 0. Before production, add an explicit incident
gate that suspends timeout finalization (including request-time timeout paths)
while an operator reviews a known service outage. Overdue gameplay still cannot
be accepted against an expired deadline. Publish the incident interval; an admin
can void affected games or apply a separately approved, audited uniform deadline
extension while frozen. If a result was already recorded, issue a result revision
and rebuild affected accounting. Do not silently count downtime as misconduct or
silently adjust deadlines. This operational mechanism is **not implemented** by
the pure contract; absent such integration its ordinary clock semantics apply.

## Persistence, transactions and integration tests

Keep SQLite and a single API process initially, but require database protection,
not Python locks alone. Extend the existing participant scaffold only in an
approved later migration. Enforce one seat per side and a distinct-account
constraint, check status/version under `BEGIN IMMEDIATE`, and atomically write
opponent seats, start, status and deadline. Two joins cannot both succeed; joining
the same creator or a closed lobby fails even if a client previously read it open.

Every gameplay/finalization transaction loads current lifecycle/participants and
version, samples time, checks actor and expiry, accepts an engine result only if
still timely, writes the ordered ply/state/version/deadline and any result revision,
and commits accounting with it. For expensive future computation use a snapshot,
then repeat validation/CAS under the lock. If the transaction rolls back, neither
game nor result nor aggregates change. Respond only after commit; stale versions
conflict and clients reload. A retry after an unknown commit outcome loads the
already terminal state and emits no new result.

Pure `Transition.effects` are proposed **replacement Result snapshots**, not
side-effectful counter updates. Re-evaluating the same stale Active object can
produce the same proposed result again. Unit tests show no extra effects only
when processing the returned updated state. They do **not** prove database
exactly-once execution, transaction safety or concurrency.

Required later integration tests, with independent connections/processes:

1. Two concurrent joins, creator self-join, cancel versus join: one committed
   activation at most, two fixed distinct identities, one first deadline.
2. Same-version competing moves and move versus expiry just before/at/after
   deadline; concession versus expiry; no lost move or overwritten timeout.
3. Two sweepers and request versus sweeper finalization: one effective revision,
   one accounting contribution and one abandonment for the loser.
4. Inject crashes before writes, between state/result/projection writes, after
   commit/before response; restart/retry preserves deadlines and deduplicates work.
5. Void versus finalization and duplicate correction requests: revision guards,
   audit retention, no residual rating/reward/abandonment contribution.
6. Ownership/participant/role revocation during requests; spectator denials,
   public-field redaction, every analysis path and private-history pagination.

## Authoritative results, ratings and progression

One stable `game_id` has one authoritative **effective** result revision, plus
retained audit revisions. Do not overwrite audit history or derive an online
result from the current replay cursor. Conceptual result fields:

| Field | Meaning |
| --- | --- |
| Identity/revision | game ID, revision, superseded revision, actor/reason/revised timestamp for corrections |
| Participants | Immutable account IDs and attacker/defender sides; display snapshots are presentation only |
| Game context | Ruleset ID and rule version; category `online` or `account_single_player`; rated intent/eligibility snapshot |
| Times | UTC start and completion; processing time independent of timeout completion |
| Outcome | Winning side or draw; no effective winner for a void; technical termination reason separate from lifecycle |
| Move count | Accepted plies (half-moves), including a terminal engine move; concession/timeout add zero |
| Eligibility | Competitive accounting eligibility and separate rated eligibility/reason |
| Abandonment | Timed-out account only on an eligible timeout; absent on concession, cancellation or void |

The pure module models online result snapshots and eligibility only; it does not
implement rated eligibility, correction authorization storage or single-player
adapters. Account-single-player results use the same conceptual identity/revision
discipline in a separate category and never affect online Elo/leaderboards.

Simplest first implementation: retain versioned results in SQLite and calculate
statistics from effective eligible results, as current statistics derive from
retained records. If materialized totals/ratings/rewards are introduced, update them
in the result transaction with a unique `(consumer, game_id, revision)` marker.
Consumers reload the effective result under that transaction and track the last
applied revision/contribution per game: skip superseded or out-of-order snapshots,
remove the previously applied contribution, then apply the current replacement.
A delayed revision 1 must not restore a loss after revision 2 voided it. Do not
commit a marker independently of its projection. Restart/retry must either
commit the whole transaction or replay/rebuild from authoritative rows; no broker
or generic event-sourcing framework is needed. External delivery, if later required,
can use a small SQLite pending-work record committed with the result.

Voiding replaces the effective result with an ineligible revision; it does not
emit another loss. Remove both players' contributions and the abandoned-games
contribution. A correction of a recorded winner requires an administrator,
expected effective revision, justification and immutable audit record, producing
a new revision. Do not reopen an already played game or change participant IDs.
The pure module implements void revision, not arbitrary winner correction.

Recommend per-ruleset online Elo and leaderboard filters matching ruleset and
category. Approve initial rating, K/provisional policy, draw scoring, repeated-opponent
limits and rated eligibility before coding. Elo is order-dependent: correction
requires replay from the earliest changed result in deterministic completion-time/
game-ID order, not simply subtracting the old delta. Keep seasonal ladder scores,
season membership and reset policy separate from lifetime Elo. Future achievements
can consume eligible result revisions with the same duplicate/revocation discipline;
do not implement a reward or achievement engine now.

## Permission and information-access design

This matrix is proposed future access, not a change to today's mandatory login.
Participant privileges apply to their online game; owner privileges to their
account-single-player game. Apply account verification gates when approved.

| Capability | Anonymous | Authenticated nonparticipant / spectator | Online participant | Single-player owner | Administrator |
| --- | --- | --- | --- | --- | --- |
| Public online lobby/board/replay | Read proposed public projection | Read | Read own/public | Read public | Read |
| Create/join online | No | With eligibility gate | May create other game; cannot self-join | With eligibility gate | Same eligibility; no seat bypass |
| Move/concede online | No | No | Own side only when to move, timely and Active | No special access | Only if a participant, same rules |
| Cancel waiting online | No | No | Creator only | No special access | Use audited void instead |
| Running online hints/chances | No | No | No | No special access | No serving assistance; separate audited collection need only |
| Completed / started-voided online analysis | Proposed read | Read | Read | Read | Read |
| Account-single-player board/history/analysis | No | No | No unrelated access | Read/compute/manage own | Collection/training access, separate from account secrets |
| Void/correct online result | No | No | No | No | Audited permission and expected revision |
| Account/security metadata | No | Own profile only | Own profile only | Own profile only | Explicit account-management permission, never bundled with game collection |
| Offline active/last game | Local browser slot | Same local feature | Same local feature | Same local feature | No remote access privilege |

Validate online legality, seats, deadlines and lifecycle on the server. Redact all
evaluation, probability, hints, advisor settings/results and search diagnostics
from Active online responses; reject analysis computation at the backend, including
replay positions of an Active online game. Disable corresponding controls in the UI
for clarity, not enforcement. Permit analysis after Completed and for played,
started-Voided public games. Cancelled and pre-start Voided lobbies have no played
line and expose no analysis computation. Private single-player analysis retains
owner/admin access regardless of public online projections.

Review every analysis entry point: game JSON, analytics polling/settings, explicit
analysis POST, AI responses, replay exports, tournament/collection routes and any
future arbitrary-position endpoint. Do not add a public arbitrary-position analysis
route alongside multiplayer. Existing account-single-player analysis can analyze
a reconstructed public online position; rate limits or same-service assistance
policy may reduce abuse, but public boards cannot prevent external analysis.
Do not promise technological cheating prevention.

Public player/game projections allow account ID, safe display name/rune, side,
ruleset, board, move list, lifecycle, deadline and public result only. Never reuse
`public_account`: omit email, hashes, session/CSRF/token fields, login timestamps,
role/status/security settings and unrelated private account data. Exclude internal
free-text correction notes and administrative/security audit context from the
public result projection; a public void uses the technical reason only. Anonymous offline
games create no account or server result. Do not upload them automatically.

## Offline retention recommendation

Proposed production policy: after installation/caching, engine and bounded AI
execute locally in a worker without a runtime server. Keep exactly one current/last
game slot (full ruleset/version, move list and replay cursor), updated atomically in
IndexedDB after each accepted move; reload restores that slot and validates it by
engine replay. Starting a replacement asks for confirmation and replaces the slot;
there is no browseable local history, list or archives. Keep appearance preferences
separate. Provide explicit clear-device action; browser eviction can still erase
the slot and must be explained. Offline results never enter competitive accounting.

The spike intentionally keeps one game in memory and demonstrates cache/runtime
feasibility, **not** production persistence, install/update UX or full offline play.
Approve the retention/replacement policy before implementing it.
