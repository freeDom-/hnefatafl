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
