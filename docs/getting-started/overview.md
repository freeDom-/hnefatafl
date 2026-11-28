# Overview

Hnefatafl is a self-hosted Viking board-game application. It combines a
deterministic Python rules engine, a responsive React client and an optional
self-play training system for neural opponents. Accounts, online play, ratings,
achievements, player tournaments and leagues are server-owned; there is no
required external service or Docker dependency.

## What you can do

- Play three rulesets: [Brandubh](../rules/brandubh.md) (7×7),
  [Tablut market rules](../rules/tablut.md) (9×9) and
  [Copenhagen](../rules/copenhagen.md) (11×11). Each page documents the source
  interpretation and digital conventions.
- Play local 1v1 pass-and-play, against fixed or custom AI opponents, or against
  other verified accounts. Online games support single games and two-game matches,
  per-move or total-time clocks, Elo ratings, leagues and player tournaments.
- Review completed games read-only, inspect saved agent opinions and request a
  background move advisor. Analysis can run on the server; an opt-in browser-local
  runtime is documented separately.
- Earn tiered achievements and portraits, browse the Hall of Renown and compare
  standings and records.
- Train policy/value networks from self-play, evaluate against fixed opponents,
  resume checkpoints, tune with Optuna and distribute work to remote workers.
- Run AI round-robin tournaments and compare agent configurations.

## What is not provided

- No offline play: a server connection is required.
- No anonymous gameplay. Signed-out visitors can read the public Watch Hall.
- No automatic internet matchmaking beyond the account lobby, leagues and player
  tournaments.
- No distributed self-play or PostgreSQL migration tooling.
- No strong pretrained model ships with the repository. A successful smoke run
  proves the training workflow, not playing strength.
- CUDA/MPS and Intel OpenVINO code paths require suitable hardware and are not
  verified on every platform.

See [current limitations](../development/releasing/readiness.md) and
[release notes](../releases/README.md) before treating a recorded measurement as
current evidence.

## Roles

| Role | Access |
| --- | --- |
| Signed-out visitor | Public Watch Hall: online discovery, live viewing and completed replay |
| Ordinary account | Single-player, local 1v1, online play, statistics, records, portraits |
| Administrator | User management plus training, models, workers, AI testing and server operations |

Permissions are enforced by the backend; hiding a control is not authorization.
See [security](../administration/security.md).

Next: [quick start](quick-start.md).
