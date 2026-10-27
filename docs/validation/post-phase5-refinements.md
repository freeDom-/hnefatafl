# Post-Phase 5 refinement validation

[Documentation index](../README.md) · [Dashboard guide](../DASHBOARD.md) ·
[Phase 5 contract](../designs/personal-dashboard-phase5.md)

This refinement continues on `feat/post-phase5-refinements`, branched from the
merged `feat/online-multiplayer` head. The pull request targets
`feat/online-multiplayer` and stays unmerged. There is no schema migration,
application version bump, tag/release, deployment, rating or production
admission/timeout activation.

## Delivered scope

- Login shows one accessible page heading; the redundant `Log in` heading is
  removed and initial focus moves to the username/email field. The single-player
  setup no longer renders the account card, while the server still binds the
  human seat to the authenticated account and rejects caller-supplied human
  identities.
- Dashboard statistics are personal. `GET /api/personal/single-player` and
  `GET /api/personal/online` add a `statistics` object grouped by ruleset and
  matchup/game mode. Account outcomes require an account-bound human seat;
  `ai-ai` and unattributed legacy records never produce personal wins/losses.
  Online outcomes use the account's seat and the current effective eligible
  result revision, counting own-clock timeouts separately from opponent
  timeouts. Rates and averages render as an em dash with no eligible games.
- Normal users see their participating online games and their own account
  single-player records in the Hall; the administrator name filter and
  all-account scope stay restricted to trusted current `all_data`.
- Hall review uses a read-only historical analysis request that analyzes the
  selected recorded cursor without seeking the authoritative game. Live gameplay
  keeps the exact-cursor contract; online analysis stays denied to every role.
- The embedded Online lobby takes the old Recorded games grid slot, the
  Leaderboard placeholder follows, and Recorded games spans the final row. All
  three cards share padding and the section gap.
- Shared recorded games render as a table with visible Status, Ruleset,
  Attacker, Defender, Winner and Date headers. The Winner cell names the
  participant and side ("Odin (Defenders)") or Draw; unfinished, cancelled and
  voided rows show an em dash and never gain an invented winner. The Date cell
  holds the single accessible review link and its overlay makes the whole row
  clickable; the date is shown in the viewer's local timezone with a locale-aware
  format and timezone label, and the trailing Review label is removed. The
  selected/focused row is highlighted as a whole and the date link has no separate
  focus ring or hover underline. The table scrolls inside its card on narrow
  screens.
- Selecting a row restores the full read-only review: "The saga so far" move
  list, board replay navigation, general game information and the
  Analytics/Winning chances panels. The board keeps a captured king visible at
  its final square, matching the live game. Account single-player review may use
  the existing authorized analysis; recorded online review keeps those panels but
  shows them as unavailable because all-role online analysis stays denied. The
  Winning chances checkbox and label are removed: selecting a move advisor is the
  single trigger for calculating and displaying winning chances and the best-move
  hint.
- The recorded-game replay controls add a viewer-local Play/Stop button. Play
  advances one recorded move per second from the current position, stops at the
  final position, stops on manual navigation and is disabled with no next move.
  Playback never requests AI moves or analysis and never writes authoritative
  games or results.

## Statistics definitions

For each `ruleset` × `mode` group:

- `played`: account games owned by the account (all statuses) for account modes;
  effective eligible completed games for online modes.
- `wins`, `losses`, `draws`: from the persisted account result or the current
  effective online result, relative to the account's seat.
- `win_rate = wins / (wins + losses + draws)`.
- `win_rate_attackers` / `win_rate_defenders`: the same formula restricted to
  attributed completed games played on that side.
- `average_game_length`: mean accepted-ply count (`moves`) over the same
  completed population, with `average_game_length_unit` naming the unit.
- `timed_out` (online): games where `abandoned_account_id` equals the account.

Account attribution is only the stored `human_side` player when its `kind` is
`human` and its `account_id` matches the current account. `ai-ai` groups keep
`played` and `average_game_length` but leave personal outcome fields null.
Records without an authoritative account seat are excluded and reported through
`statistics.coverage.unattributed_games`; no stored data is rewritten and no
ownership or seat is guessed.

## Recorded-games table

Columns are `Status | Ruleset | Attacker | Defender | Winner | Date`, with
`<th scope="col">` headers. The Winner cell is `Name (Side)` for a completed
recorded result, `Draw` for a draw, and an em dash for unfinished, cancelled and
voided records. A stale winner on a non-completed record is not shown. The Date
cell is the review link, named `Open review: {attacker} vs {defender}`, so the
link remains keyboard-accessible and clearly named without a visible Review
label. The table is wrapped in a horizontally scrollable container so narrow
screens do not overflow the page.

## Automatic replay

`useReplayPlayback` owns a viewer-local cursor and a bounded timer. `ReplayControls`
renders Play/Stop when playback is enabled. Manual move selection and
previous/next/start stop playback before changing position; reaching the final
position stops automatically and disables Play until the viewer returns to an
earlier move. The timer is cleared on every dependency change and on unmount, so
there is at most one timer and no stale updates. The hook never calls the API.

## Review and security boundaries

The review reuses the single-player presentation and is viewer-local: move
selection and replay navigation never write the authoritative cursor, version or
result. `/archive?review=…&selected=…`, `/dashboard/single-player/{id}`,
refresh, direct load and browser back/forward keep their existing behavior.
Backend role/participant authorization and canonical replay validation are
unchanged. Online history/replay routes expose no analysis or live assistance;
the review shows the analysis panels as unavailable. Account single-player
analysis uses the existing separate analysis tables, so authoritative game and
result rows are not mutated.

## Checks

| Check | Actual result |
| --- | --- |
| `make check` | **passed**, 14:56 backend + frontend + build; docs, Ruff/format, Prettier, mypy and TypeScript all passed |
| Complete backend `pytest` | **1483 passed, 36 hardware/optional skips, 1 warning**, 896.25 s |
| Complete frontend `vitest` | **44 files, 361 passed** |
| Ruff / Ruff format / Prettier | passed (104 backend files; frontend formatting clean) |
| mypy | passed, 48 source files |
| TypeScript | passed |
| Production build | passed (existing large-chunk advisory only) |
| `make docs` | passed, 86 Markdown files, 637 links |
| Personal production browsers | **14 passed**, desktop + mobile Chromium, 3.7 min |
| Affected legacy production browsers | **16 passed, 2 intentional duplicate skips**, desktop + mobile Chromium, 6.8 min |
| Advisor/replay production browsers | **6 passed**, desktop + mobile Chromium, 5.2 min |
| Pull-request CI | **python passed** (6m11s), **browser passed** (12m12s); legacy **66 passed / 4 skips**, registration **8**, online **14**, personal **14**, guest **16** |

Focused new tests cover the recorded-games table headers/order, winner
formatting, non-winning states, accessible review links, the selected-row
highlight, the removal of the Review label, locale/timezone date rendering,
one-move-per-second playback, Stop preserving position, end-of-game and
empty-game Play disabling, replay from an earlier position, manual navigation
stopping playback, unmount cleanup, the absence of analysis requests during
playback, login identity focus, the advisor-triggered winning-chances/best-move
display without a checkbox, and captured-king retention in the review.

## Limits

Browser evidence uses Chromium desktop and mobile emulation with the project's
existing local fixture servers. The personal suite's geometry assertions checked
equal card padding, heading inset, the shared section gap, horizontal containment
and no page-level overflow for the Dashboard and lobby at desktop and mobile
widths; its screenshots were captured but not visually inspected by this agent.
No physical device, Safari/Firefox, CUDA or OpenVINO hardware execution, AI
strength ranking, production SMTP, deployment or full offline-startup validation
is claimed. The local run uses isolated
`HNEFATAFL_DATA_DIR` storage; no database, model weights or credentials are
committed. Ratings and production rollout remain separate owner decisions.
