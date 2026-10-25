# Phase 3 online UI validation

[Documentation index](../README.md) · [Online play](../ONLINE_PLAY.md) ·
[Phase 4 handoff](../designs/online-phase4-handoff.md)

Implementation branch: `feat/phase3-online-ui`, based on integration commit
`de0b64b36fe3445b01062fed51e9e3008c1c8e8c` after Phase 2 PR #12 merged.
PR base is `feat/online-multiplayer`. No release/version bump, deployment, admission
activation or production-data operation belongs to this work.

## Delivered behavior and boundaries

English/German authenticated lobby and `/online/games/{id}` views provide bounded
pagination, My games recovery, all three side choices/match modes, creation,
waiting join/cancel, real moves, turn-bound confirmed concession, effective results,
independent sibling boards/deadlines/versions and viewer-local replay. Selection is
presentation only. Active analysis remains absent and legacy adapters retain their
backend online-ID guards. No aggregate scoring, public visibility or production
outage policy is introduced.

Creation retains one account-scoped request UUID/configuration in memory and tab
session storage until accepted or explicitly discarded. A retry is deliberate;
409 is not interpreted as idempotent success. Quota errors retain the request and
offer own-games/cancellation/waiting recovery. Client code does not duplicate
configurable quota limits.

Reads coalesce with abort/revision guards and bounded backoff. Mutations submit
once, then reload on success, conflict or uncertainty. A successful timeout response
with `action_accepted=false` is distinguished from an accepted requested action.
Stale state remains visible with actions disabled. Existing seats do not depend on
admission eligibility. Account-keyed state, request cancellation and Auth generation
guards prevent delayed old-account responses restoring protected views.

The only backend contract extension is authenticated
`GET /api/online/availability` -> `{enabled, eligible}`. Existing contracts exposed
neither the admission switch nor `security_context.online_eligible` to frontend
code. The endpoint uses that predicate directly and reveals no account/private
configuration details. Its route is explicitly granted `play` and inventoried.
No schema/lifecycle/timeout/admission policy changes were needed.

## Validation results

Final commands and counts are recorded below after the full validation run.
The first online browser run passed **10/10** checks (five desktop and five mobile)
on local Chromium 154. It used real FastAPI/SQLite and isolated ordinary accounts:
single game with move/concession/replay, simultaneous swapped seats, sequential
return discovery, reconnect, competing stale concession, spectator mutation denial,
legacy analysis denial, disabled admission and paused adjudication with controlled
time. No long real-time timeout wait or production account was used.

Unit fixtures are snapshots from actual `OnlineCore` creation/join/move/concession/
replay/sibling/timeout projections, not invented fields. Regenerate explicitly with
`PYTHONPATH=. .venv/bin/python frontend/tests/generate_online_fixtures.py` using
the [isolated generator](../../frontend/tests/generate_online_fixtures.py). IDs
change when regenerated; no session/password/private audit data is written. Component/hook coverage includes
all side/mode combinations, retained creation conflicts/denials, existing-seat
eligibility, duplicate prevention, no optimistic board, timeout rejection, stale
versions, 401/403/422/429/503, replay, sibling reads, clock uncertainty, German labels,
account switching, logout and late-response/backoff cancellation. A batched
double-submit check verifies that the retained UUID still matches the one submitted
even before pending state renders.

## Limits

Browser results use Chromium desktop and mobile emulation, not physical devices or
other browser engines. Device-clock countdowns are approximate; no undocumented
server-clock field is assumed. Retained requests survive reload only where tab
session storage is available; own-games recovery remains available otherwise.
No production SMTP/deployment, anonymous visibility, retention expiry, rated play,
offline guest play, GPU or strength validation is claimed. See the
[Phase 4 handoff](../designs/online-phase4-handoff.md) for remaining owner decisions.
