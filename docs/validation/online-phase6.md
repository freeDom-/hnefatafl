# Phase 6 rated games and leaderboards: validation

[Documentation index](../README.md) · [Rated games](../RATINGS.md) ·
[Online core](../ONLINE.md) · [Phase 5 evidence](online-phase5.md)

Baseline: `0702ea9` (merge of PR #21 `feat/post-phase5-refinements` into
`feat/online-multiplayer`). Phase 6 was developed on `caf866b` (the PR #21 head)
and rebased onto `0702ea9` after #21 merged; the branch is
`feat/phase6-rated-games-leaderboards` and targets `feat/online-multiplayer`.

## Delivered behavior

- Versioned Elo pool (initial 1500, K = 32, provisional at 10 games) in the pure
  `hnefatafl/rating.py`; durable events/applications and leaderboard queries in
  `hnefatafl/online_ratings.py`.
- Schema 11 adds `rating_events`, `rating_applications`, `player_ratings` and the
  immutable `online_matches.rated` column in place. No historical backfill.
- Rated results apply in the same transaction as the terminal result; voids
  withdraw the event and rebuild the pool deterministically in sequence order.
- Explicit Rated/Casual creation (default Casual), immutable after creation, with
  authoritative per-player rating changes on completed Rated games.
- Authenticated `GET /api/leaderboard` with qualified/provisional separation,
  deterministic ties, pagination, own standing and policy; administrator-only
  `POST /api/online/ratings/rebuild`.
- Dashboard placeholder replaced by a compact leaderboard; full `/leaderboard`
  view added.

## Automated checks actually run

Complete aggregate:

```
make check
# docs, ruff check + format, prettier, mypy, TypeScript, backend tests,
# frontend tests and production build all passed
```

Backend tests (real SQLite, controlled UTC, no sleeps):

```
.venv/bin/python -m pytest -q
# 1510 passed, 36 skipped, 1 warning in 982.41s
```

The 36 skips are the existing optional/hardware skips (OpenVINO/GPU); none are
new. `tests/test_ratings.py` contributes 27 focused cases covering pure Elo
values/precision, pre-game simultaneous updates, provisional threshold, all
outcomes, pools, eligibility exclusions, duplicate/idempotent application,
concurrent completion, rollback, deterministic void rebuild with later games,
two-game legs, the leaderboard (ties, pagination, provisional separation, own
standing, unranked) and the authenticated/admin API surface.

Frontend unit tests:

```
npm --prefix frontend test
# 45 files, 372 passed
npm --prefix frontend run typecheck        # passed
npm --prefix frontend run format:check     # passed
```

Browser tests (bundled Playwright Chromium, desktop + mobile emulation):

```
npm --prefix frontend run test:online
# 14 passed (5.8m), desktop + mobile
npm --prefix frontend run test:personal
# 18 passed (3.7m), desktop + mobile
```

The personal suite includes the merged Hall-review case plus a new leaderboard
case that logs in with a real session, checks the compact Dashboard leaderboard
(qualified players and own standing), opens `/leaderboard`, switches to an empty
ruleset pool and asserts no page-level horizontal overflow. Screenshots were captured for the compact and
full leaderboard at desktop and mobile widths. This environment has no image
viewer available to the agent, so the screenshots were not visually inspected;
the no-overflow assertion and DOM assertions are the only layout evidence.

Static checks:

```
.venv/bin/ruff check hnefatafl tests           # passed
.venv/bin/ruff format --check hnefatafl tests  # passed
.venv/bin/mypy hnefatafl                        # Success: no issues found
make docs                                       # Documentation OK
```

## Not claimed

- No production admission, timeout-finalization, deployment, release, tag or
  version change.
- No CUDA/OpenVINO hardware, Safari/Firefox or physical-device validation.
- The captured browser screenshots were not visually inspected (no image viewer
  in this environment); only automated DOM/no-overflow checks were performed.
- No rating backfill, anti-farming weighting, rewards or public leaderboard.
- Repeated-opponent farming remains possible and is documented, not mitigated.
