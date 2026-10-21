# Architecture

## Decisions

Python 3.11+ owns the deterministic engine, search, persistence and training. FastAPI
exposes server-authoritative JSON sessions; React, TypeScript and Vite provide one
responsive browser client for desktop and phones. SQLite keeps ordinary Linux and
Proxmox LXC installations simple. PyTorch provides real CPU/CUDA policy/value learning;
Optuna manages persistent search studies. Docker is not required.

The browser never decides legality or game outcomes. Every position and move has a
serializable representation, shared by interactive play, replay, AI and training.
Pieces are `.`, `A`, `D`, `K`; moves use zero-based flattened `source` and `target`
indices. Turns and results use `attackers`, `defenders`, and `draw` for draws.

## Components

- `hnefatafl/engine.py`: immutable states, ruleset configuration, legal moves,
  captures and terminal rules. No web, database or tensor dependency.
- `hnefatafl/ai.py`: pluggable heuristic, alpha-beta and MCTS opponents and seeded
  tournaments. Neural evaluation is supplied through the model interface.
- `hnefatafl/models.py` and `training.py`: policy/value networks, compatible model
  registry, self-play, checkpoint/resume, evaluation and optimization.
- `hnefatafl/storage.py` and `server.py`: SQLite sessions, stable local identities,
  optimistic version checks, history/statistics and API endpoints.
- `frontend/`: accessible board interaction, game setup, playback, statistics,
  rules and training management.

Long-running training runs in separate worker processes, never in the API request
loop. Retained files (SQLite, checkpoints, model metadata and worker logs) are
separate from live processes, which must restart after machine restoration.

Browser tournaments also run in a separate process. SQLite schema 3 adds a
tournament collection containing opponent/configuration snapshots, lifecycle status,
progress and completed match records. The worker saves after each finished match;
stopping a run preserves that subset. Replay reconstructs positions through the
pure engine and does not require the original model weights. Tournament matches
remain separate from ordinary game statistics. Catalog locking protects the model
references of active tournaments until the worker exits.

Tournament schedules support two to four opponents. Every unordered pair plays
the configured even game count on each battlefield, alternating sides. Progress
uses the number of pairings, and standings update only the two participating
opponents. New matches retain explicit attacker and defender indexes; the browser
can still infer the defender in earlier two-player records without migrating them.

## Rules

One engine applies configurable board sizes, initial arrangements, restricted
squares, first player, king capture and escape rules. Variant-specific mechanisms
are explicit hooks, including Copenhagen shield walls, encirclement and edge forts.
The exact linked market Tablut rules take precedence over other historical Tablut
reconstructions. Dedicated rule documents record interpretation and source access.

## Future multiplayer

Game IDs, stable player identities, persisted ordered moves and versioned move
requests permit future authenticated accounts and WebSocket broadcasts. Authentication,
authorization and lobby matchmaking must be added before exposing competitive online
play. The engine does not need to change. SQLite transactions currently serialize
updates; a PostgreSQL adapter can replace storage without changing rules or clients.

## Model scope and trust

Models are ruleset-specific, allowing small networks and straightforward compatibility
checks. Safe tensor-only loading and validated architecture metadata avoid arbitrary
Python object deserialization. Functional training tests establish that the pipeline
works; model strength requires separate, sufficiently sized evaluation tournaments.
CUDA support does not establish that CUDA was tested on this development host.

## History and presentation configuration

AI record identity is a deterministic digest of normalized effective parameters,
including algorithm, relevant budgets, randomness and model identity. New sessions
store resolved parameters and display-name snapshots. Legacy explicit-algorithm
sessions retain the defaults their original engine used; profile defaults are not
retroactively applied. Variant statistics expose configuration metadata for inspection.

SQLite schema 2 adds player tombstones and archived model metadata. Deleting a player
hides it from selection while preserving its ID and saved history; reusing the same
display name creates a new identity. Deleting an available model removes its registry
copy and entry, retaining historical labels and original training outputs. Catalog
reference creation and deletion are serialized in the supported single API process.

Appearance is browser-local state, separate from game configuration. A typed catalog
defines color modes, board palettes and token sets; shared token rendering is used
for actual pieces and previews. The provider resolves device color preferences and
persists choices. New artwork and palettes can be added without changing game rules
or persistence. See `docs/PERSONALIZATION.md`.

Ordinary new games store a server-generated base seed; actual decision seeds combine
the base with the ply. Explicit game seeds support reproducible requests. Restart
always creates a fresh seed, and legacy records retain their original semantics.
Game deletion uses an expected version and removes the source record; statistics
are derived from retained records, so all counts/rates update without cached totals.

Model deletion previews every unfinished reference on the server rather than only
the browser's current game. Discarding those games requires an explicit opt-in and
a matching usage fingerprint. SQL and registry failure handling preserve references
if deletion fails; completed histories and training checkpoints remain. App-level
deletion callbacks clear matching current-game pointers and reject late AI responses.

Hall tables sort and filter presentation data locally without rewriting stored
results or changing aggregate statistics. Numeric comparisons cover counts, rates
and averages; player/history text searches remain independent. AI parameter dialogs
share the same full-value renderer as recorded-game details while keeping table rows
compact. New browser preferences default to carved wood and walnut; valid saved
preferences remain authoritative.
