# League policy and implementation contract

[Documentation index](README.md) · [Online authority](ONLINE.md) ·
[Arena completion plan](designs/arena-completion.md)

Status: implementation contract, policy version 1. League play is not yet exposed
on main. The existing five tier decorations are artwork, not earned standings.
This policy does not change Elo or reinterpret historical casual games.

## Season and progression

Seasons are calendar months in UTC, with half-open boundaries `[start, next month)`.
A game belongs to the season in which the server pairs it. A season has separate
Brandubh, Tablut and Copenhagen standings; no cross-ruleset matchmaking. IDs and
boundaries are deterministic, but a read must not invent stored history or ranks.
A season is persisted only on a mutation that needs it.

League points: win +3, draw +1, loss -1, clamped at zero after each authoritative
result in stable result-ledger sequence. These measure seasonal progress, not
estimated playing strength. Thresholds:

| Tier | Minimum points |
| --- | ---: |
| Midgard | 0 |
| Nidavellir | 6 |
| Alfheim | 15 |
| Asgard | 30 |
| Valhalla | 50 |

A loss or result correction can demote a player; inactivity never subtracts points.
Season rollover resets league points, not Elo or permanent achievements. Show
recorded progress immediately, but qualify ranking only after three eligible
results. Do not fabricate an opponent, rank or percentile in an empty population.
Standings sort by points, wins and fewer losses. Equal sporting records share a
rank; account ID only stabilizes pagination. Selected portraits are published
through the existing safe portrait projection, not client-created asset paths.

Games have 24 hours after season end to finish for seasonal scoring. Existing
online clocks and results continue after that grace; late games retain their Elo
result but contribute no league points. Season summaries remain provisional until
grace ends. Later official corrections or voids can revise archived standings,
with a visible revision. There is no permanent freeze that hides an official error.

## Eligibility, queue and small populations

Only active, verified human accounts with normal play permission may queue.
Administrator accounts are human accounts too; administrative permission does not
bypass eligibility. AI participation is disabled. Existing tournament legs remain
casual and never enter league standings. League games use the normal rated online
engine and unchanged Elo pool, initial rating, K factor and provisional policy.

The fixed initial league clock is 600 seconds total per player with a 5-second
increment. Queue entry is explicit, ruleset-aware and globally unique per account.
An account may have at most one active league game, across all rulesets. Joining,
cancelling and matching are idempotent and serialized with authoritative online
writes. Queue entries expire after ten minutes; cancellation does not cancel an
already committed game. Cancellation targets its original queue request, so a
delayed retry cannot cancel a later entry. Return the official game link when
matching wins the race. New request IDs are limited to ten per minute and one
hundred per UTC day per account; exact retries do not consume this budget.

Pair the oldest mutually compatible eligible entries. Both players must accept
the rating window implied by their wait: initially 200 Elo, widening by 100 each
full minute to a maximum 600. Re-read eligibility, ratings, queue age, active-game
state and season under the write lock before seating. No self-pair, duplicate
seat or incompatible ruleset may commit. Waiting time must not silently relax the
maximum rating bound or substitute an AI. An empty queue explains the lack of
players, expiry and cancellation rather than promising an estimated wait.

Cap league pairings at three per unordered opponent pair per UTC day. Count
committed pairings even if a result is later voided, so corrections cannot reopen
an unlimited farming loophole. This conservatively implements the earlier
three-scoring-game proposal. It limits repeated pairing, not collusion across
accounts. Queue discovery may show a cooldown reason but never private account
metadata. A global administrator matching pause blocks new pairings, without
changing existing authoritative online clocks or results.

## Results, revisions and recovery

Use one server-owned provenance record per official online game, carrying season,
ruleset, players, pairing time and effective result revision. The normal online
result transaction durably enqueues affected league accounting. No browser is
needed to settle results. An eligible applied rated result can contribute once;
corrections replace that contribution and voids withdraw it. Never add a second
rating application or compute the winner in the client.

Because the points floor is nonlinear, a correction must replay the affected
player's season contributions in authoritative sequence, not subtract a guessed
delta. Persist stable ledger order and idempotency keys. Account/season lookup and
queue/result discovery must use indexes. Worker batches, retries and outstanding
work must be bounded; avoid recurring whole-history scans. Old-season matching is
forbidden at rollover even while old games finish during grace.

Administrators may inspect queue/settlement health, pause matching, cancel stale
entries and request an audited version-checked season rebuild with a private
reason. Official game corrections and voids use the existing online authority;
league controls cannot invent winners. Recovery must survive restart and browser
absence, preserve corrections, and be testable without live accounts. Restore
inventory must include every new league table in dependency order. The next schema
number must be chosen from current main, not an earlier branch's proposed number.
No developer database, games, training job, checkpoint or model is deleted to
introduce leagues.

## Acceptance gates

Database tests must prove simultaneous queue joins/cancels, duplicate request IDs,
self-match exclusion, cross-ruleset isolation, distinct player seats, inactive or
unverified accounts, window growth/caps, expiry, pair caps, season/grace boundaries,
result retries/corrections/voids, nonlinear-floor rebuild, restart and restoration.
Read endpoints remain bounded and account-safe; unknown routes remain denied.

The frontend must show season/tier progress, qualified standings, official match
history, explicit queue/cancellation/expiry, match links, promotion/demotion and
empty-population states. Administrator monitoring must be permission-gated.
Reuse Arena themes, tier artwork, shared controls, focus and reduced-motion
behavior; support English/German and small screens. No live online advisor is
introduced. Domain and browser checks must pass before this contract becomes a
claim that league gameplay is delivered.
