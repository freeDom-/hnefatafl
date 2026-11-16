# Public online discovery (read-only)

[Documentation index](README.md) · [Online gameplay](ONLINE.md) ·
[Playing online](ONLINE_PLAY.md) · [Security](SECURITY.md)

Signed-out visitors may browse online games and completed replays through the
additive `/api/public/online/*` namespace. This is a read-only spectator surface:
it exposes no single-player or local records, performs no mutation, offers no
live assistance and never generates analysis. The namespace is gated by a
deployment switch and bounded per connecting IP.

## Endpoints

| Method and path                                 | Purpose                                          |
| ----------------------------------------------- | ------------------------------------------------ |
| `GET /api/public/online/games`                  | Bounded, filterable index of public online games |
| `GET /api/public/online/games/{game_id}`        | Read-only detail of a live or completed game     |
| `GET /api/public/online/games/{game_id}/replay` | Completed-game replay with validated states      |

No `POST`, `PATCH` or `DELETE` route exists under this namespace, and there is no
public analysis endpoint. Unknown and live-replay identifiers return a consistent
`404`. Malformed or over-long identifiers fail validation with `422` before any
lookup.

## Index filters, search and paging

- `ruleset`: `brandubh`, `tablut` or `copenhagen`.
- `status`: `waiting`, `active`, `completed`, `cancelled` or `voided`.
- `outcome`: the effective winner `attackers`, `defenders` or `draw`.
- `player`: case-insensitive literal substring over the public display names and
  handles only; `%` and `_` are ordinary characters, never wildcards. Matching
  applies full Unicode case folding, so `müller` matches `MÜLLER` and `STRASSE`
  matches `Straße`. It is not accent-insensitive or transliterating: `muller`
  does not match `Müller` and `Mueller` is a distinct spelling. Bounded to
  148 code points (a full attributed label), matching the authenticated Hall.
  A withheld display name is not searchable; only the public handle remains so.
- `limit`: `1..100` (default `25`).
- `cursor`: opaque keyset cursor minted by the previous page. It is bound to the
  filter set and sort key, so reusing it under different filters returns `422`.

The index sorts by most recent activity (`updated_at` descending, then `id`
ascending) and never exposes offset scans to anonymous callers. The page shape is
`{"items": [...], "next_cursor": string | null, "limit": number}`; an empty page
returns `items: []` and `next_cursor: null`, never omitted list fields.

Each item carries only public attribution: `id`, `ruleset`, `ruleset_version`,
`status`, `mode`, `leg`, `rated`, `created_at`, `recorded_at`, the two public
participants and the effective `result`. A participant exposes `display_name`
(the canonical public label), `username`, the effective published `portrait_id`
(the server-resolved catalog ID from the schema-17 selection, falling back to the
always-available default, or `null` when the seat is empty) and the game's
`start_rating` and the current pool `current_rating`. The result exposes `winner`,
`termination_reason`, `rated` and the effective `winner_delta`.

## Live versus completed reads

- Live and waiting games expose the current `position`, `captured` counts,
  participants and clock/status fields only. They never expose the move list,
  evaluations, hints, best moves, win chances or any analysis.
- Completed games additionally expose the recorded move list and effective
  result. A voided result is published as `winner: null`, `rated: false` and
  `winner_delta: null`, never as a win.
- The replay reconstructs and validates every stored move against the terminal
  position before returning `states`. A stored-history mismatch returns `409`.
- Replay may surface analysis that is **already stored** for the terminal
  position; a public request never runs inference, writes analysis, or reserves
  identity runes. Only completed games expose it, and only the terminal
  position's own report is read.
- A stored report is projected onto a fixed public allowlist before it is
  returned; the stored report itself is never modified. Every approved field is
  also type-checked: a value of the wrong type (a nested object or array where a
  scalar is expected, a boolean where a number is expected, an out-of-range
  coordinate or win chance, a non-ISO timestamp) is dropped rather than
  published. A public evaluation exposes `agent_id`, `role`, `name`,
  `profile_name`, `model_name`, `source`, `value`, `win_probability`,
  `best_move` (`source`/`target`), `estimated`, `calibrated` and
  `unavailable_reason`. The report exposes the position's `cursor`, `ply`,
  `side_to_move` and `created_at`, the projected `evaluations` and the projected
  `hint` (`agent_id` and its move). The effective agent `config` (including model
  filesystem paths and seeds), reserved identity runes, search diagnostics,
  private notes and any other stored property are dropped recursively, so a
  future stored field cannot leak by default.

## Caching and rate limiting

Completed detail and replay responses carry `Cache-Control: public, max-age=60`;
live and index responses keep the default `no-store`. The security boundary owns
the header: it strips any handler directive and emits exactly one, so every other
`/api/` response (including 4xx/5xx errors) is forced to `no-store` and no private
route can opt itself into shared caching. Every public request is
bounded per connecting IP by an in-process sliding window (default 60 requests
per 60 seconds), which is stricter than authenticated lobby polling. The
deployment runs one API worker, so the limiter needs no durable state and adds no
schema.

## Deployment switch

| Environment setting                    | Default | Meaning                                                     |
| -------------------------------------- | ------- | ----------------------------------------------------------- |
| `HNEFATAFL_PUBLIC_ONLINE_ENABLED`      | `true`  | Enables the whole `/api/public/online/*` namespace          |
| `HNEFATAFL_PUBLIC_RATE_LIMIT`          | `60`    | Public requests allowed per window per IP, integer 1–100000 |
| `HNEFATAFL_PUBLIC_RATE_WINDOW_SECONDS` | `60`    | Public rate window in seconds, integer 1–86400              |

Setting `HNEFATAFL_PUBLIC_ONLINE_ENABLED=false` withdraws the namespace on the next
service start (no code change or migration needed): every public handler then
returns `404`, while all authenticated `/api/online/*`, `/api/history/*`,
`/api/personal/*` and `/api/leaderboard` routes keep their existing behavior. The
switch does not require a database migration; no schema version changes for this
feature.

## Security guarantees

- Online games only. Single-player and local 1v1 records never appear in the
  list, counts, detail or replay paths, and requesting one returns `404`.
- No email, account or session identifier, private profile field, date of birth,
  private administrative note, CSRF detail or unpublished name is exposed.
- A participant's `portrait_id` is a catalog ID resolved server-side from the
  stored selection; the request never supplies it and no achievement progress,
  entitlement or account-private field is returned.
- No join, cancel, move, concede, void, analysis generation or personal scope is
  reachable through this namespace.
- Existing authenticated lobby, personal and Hall routes keep their current
  authorization and behavior; the flag gates only the additive public namespace.

## Public Watch Hall frontend

`/watch` is a signed-out discovery page with ruleset, status, result and published
player filters and bounded cursor navigation. `/watch/games/{id}` watches an online
position; `/watch/games/{id}/replay` navigates validated completed states with
first/previous/next/last controls and Arrow/Home/End shortcuts. Read-only board
cells expose no legal moves, hints or analysis. The frontend deliberately ignores
cached analysis rather than mounting an advisor.

The public entry loads before the protected Auth/App graph. Public requests are
GET-only with credentials omitted and cannot trigger session/CSRF side effects.
Session service failure does not block watching. Login/registration are ordinary
full-page links; account forms also link to Watch Hall. Public-off, missing/private
IDs, empty communities, rate limits and read failures have explicit states/retry.
A failed dynamic route import offers an explicit full reload; it never loops.

Discovery cards name both sides explicitly, mark the winning side and reuse the
shared status badge to separate live, waiting, completed and terminal games;
completed cards expose the replay action as the primary action. Card width and
column count follow the available space while each card stays capped, from 320 px
phones to 4K desktops. Spectator boards stay square and fully visible, and primary
spectator actions keep at least a 44 px mobile touch target without enlarging
desktop controls. The compact public header keeps the theme and language
selectors and adds a session-neutral `Play` link to `/play` alongside login and
registration, so signed-out visitors can reach either without protected state.

Discovery polls at 15 seconds while visible; active viewers at 5 seconds and
waiting viewers at 15 seconds. Completed replay stops recurring requests. Hidden
views abort and suspend polling, then refresh on return. Countdown seconds derive
from the server deadline without polling each second. Stale responses and previous
filter/game generations are rejected. All four themes, language and independent
board/token/scale preferences use the shared providers.

Every anonymous read is bounded by a 10 s timeout and cancels cleanly on
navigation, unmount or a hidden tab. Failures are classified: `404` (including
withheld private IDs) shows the privacy-preserving unavailable state and stops
automatic polling; a `409` replay validation failure stops retries with an
explicit message; `429` honours `Retry-After` (delta-seconds or HTTP-date) for
both automatic and manual retries, keeps the cooldown across tab visibility
changes, and explains why Retry is temporarily unavailable; `5xx` and network
failures retry with bounded backoff. Recoverable states keep a manual retry. A
live game that completes keeps its final position with replay controls
available, while an explicit `/watch/games/{id}/replay` link starts at move zero.
Live games show the active side to move from the server position and format
time controls with the shared duration formatter. Only `/watch`,
`/watch/games/{id}` and `/watch/games/{id}/replay` are valid public routes; other
`/watch` paths render a translated not-found state with a link back to the Watch
Hall and issue no API requests. Discovery and live reads stay uncached; completed
detail and replay reads defer to the server's public `Cache-Control` policy.
Portrait assets come from one shared allowlist used by both the public spectator
and the authenticated dashboard, so an unknown server id never becomes a URL.

The public shell is intentionally independent from authenticated Arena navigation.
Its build entry is split from the protected application; this reduces public
startup code, not the full authenticated Dashboard bundle. Further route-level
Workshop splitting and shared protected polling remain separate workstreams.
