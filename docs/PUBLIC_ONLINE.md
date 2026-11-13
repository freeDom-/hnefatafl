# Public online discovery (read-only)

[Documentation index](README.md) · [Online gameplay](ONLINE.md) ·
[Playing online](ONLINE_PLAY.md) · [Security](SECURITY.md)

Signed-out visitors may browse online games and completed replays through the
additive `/api/public/online/*` namespace. This is a read-only spectator surface:
it exposes no single-player or local records, performs no mutation, offers no
live assistance and never generates analysis. The namespace is gated by a
deployment switch and bounded per connecting IP.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /api/public/online/games` | Bounded, filterable index of public online games |
| `GET /api/public/online/games/{game_id}` | Read-only detail of a live or completed game |
| `GET /api/public/online/games/{game_id}/replay` | Completed-game replay with validated states |

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
(the canonical public label), `username`, a reserved `portrait_id` (`null` until
portrait storage ships with schema 17) and the game's `start_rating` and the
current pool `current_rating`. The result exposes `winner`,
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

| Environment setting | Default | Meaning |
| --- | --- | --- |
| `HNEFATAFL_PUBLIC_ONLINE_ENABLED` | `true` | Enables the whole `/api/public/online/*` namespace |
| `HNEFATAFL_PUBLIC_RATE_LIMIT` | `60` | Public requests allowed per window per IP, integer 1–100000 |
| `HNEFATAFL_PUBLIC_RATE_WINDOW_SECONDS` | `60` | Public rate window in seconds, integer 1–86400 |

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
- No join, cancel, move, concede, void, analysis generation or personal scope is
  reachable through this namespace.
- Existing authenticated lobby, personal and Hall routes keep their current
  authorization and behavior; the flag gates only the additive public namespace.
