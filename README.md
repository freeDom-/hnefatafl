# Hnefatafl — The King's Table

Play Brandubh, Tablut and Copenhagen in a responsive browser application. Challenge
search opponents, compare them in tournaments, or train a policy/value network
from scratch with real self-play learning.

![Brandubh desktop gameplay in an earlier interface](docs/screenshots/desktop.png)

[Documentation](docs/README.md) · [Quick start](#quick-start) · [Training](docs/TRAINING.md) · [Deployment](docs/DEPLOYMENT.md)

<a id="features"></a>

## What you can do

- Play locally with two humans, Human vs AI or AI vs AI; use mouse, touch or
  keyboard, undo/redo, saved replay, captures and configurable computer playback.
- Choose tactical, alpha-beta or MCTS opponents. Use compatible trained models
  with **MCTS, alpha-beta or policy-only** wrappers and inspect saved position
  estimates or a background move advisor.
- Run saved two-to-four-player round robins with balanced sides, per-battlefield
  win matrices and explicit unfinished outcomes. Browse sortable player/AI records.
- Train on CPU or supported CUDA/MPS devices; monitor retained learning charts,
  evaluate against baseline/previous best and run persistent Optuna studies.
- Resume a full checkpoint with reviewed changes to training budgets, optimizer
  settings and execution mode while retaining learning state. Use sequential or
  concurrent self-play with batched neural inference and inspect actual throughput.
- Enroll outbound remote workers with simultaneous independent training slots.
  Rename/import/export models and delete training data separately from playable models.
- Use English/German, persistent identity runes and independent color, board and
  token themes. See the [mobile interface](docs/screenshots/mobile.png).

<a id="install-and-start"></a>

## Quick start

Use **Python 3.12 recommended** (3.11+ supported), **Node 22+** (CI uses 24) and
[uv](https://docs.astral.sh/uv/getting-started/installation/) (CI pins 0.12.23).
From the repository root:

```sh
make install
make build
bash scripts/start.sh
```

Open port 8000 on the local machine. The server binds `127.0.0.1` and serves both
the built browser client and API. Check readiness with
`curl --fail http://127.0.0.1:8000/api/health`, then create a game and make a move.
Set `HNEFATAFL_DATA_DIR` to choose persistent storage (default `./data`).
`.env.example` documents variables; it is not automatically loaded.

For live development use `bash scripts/dev.sh` after installation. See
[Deployment](docs/DEPLOYMENT.md) for trusted-LAN access, Linux/LXC, systemd,
authenticated reverse proxies, updates and backups. No Docker is required.
The locked environment installs CPU PyTorch; [GPU setup](docs/PERFORMANCE.md#installation-and-hardware)
requires a matching official build and suitable hardware.

<a id="play-and-replay"></a>
<a id="run-an-ai-tournament"></a>
<a id="train-and-use-a-model"></a>

## Play or train

In **New game**, choose a battlefield, mode, sides and opponents. Select a piece,
then a highlighted legal destination. Arrow keys navigate, Enter/Space selects,
and Escape clears selection. **Hall of records** reopens saved games; **Analytics**
below move history configures an advisor. See [Gameplay](docs/GAMEPLAY.md).

In **AI workshop**, use **Quick check** for a tiny functional training run, or
choose a board-specific recommended plan for a larger experiment. Equivalent CLI:

```sh
.venv/bin/python -m hnefatafl.cli devices
.venv/bin/python -m hnefatafl.cli train --config configs/train-smoke.json
.venv/bin/python -m hnefatafl.cli benchmark --output data/benchmark.json
```

A successful smoke run proves updates/checkpoints/legal play, not stronger play.
See [Training](docs/TRAINING.md), [Performance](docs/PERFORMANCE.md) and
[checkpoint/data management](docs/TRAINING_DATA.md). Importing a model supplies
an opponent; it does not register or resume a training job.

<a id="tests"></a>
<a id="project-map-and-deployment"></a>

## Develop and verify

```sh
make check
```

This runs formatting/lint, Python/TypeScript checks, backend/frontend unit tests
and a production build. Browser checks additionally require Playwright Chromium.
See [Development](docs/DEVELOPMENT.md), [Architecture](ARCHITECTURE.md) and
[validation evidence](docs/VALIDATION.md) for commands, counts and limitations.

## Current limitations

A server connection is required. Local profiles are not authenticated accounts;
keep administrative gameplay/training routes on a trusted network or behind an
authenticated TLS proxy. Offline play, internet accounts/lobbies, distributed
self-play and PostgreSQL migration tools are not implemented.

CPU workflows are verified. CUDA/MPS execution, GPU throughput and target-host
systemd/proxy operation require separate validation. Tiny v0.17.0 CPU benchmarks
reduced forward passes but showed **no batching speedup**. No strong pretrained
model or measured improvement in playing strength is claimed. Displayed winning
chances are uncalibrated position assessments. Copenhagen's inaccessible primary
source and selected interpretation are documented in its [rules](docs/rules/copenhagen.md).

Current guides describe the implementation; [release notes](docs/README.md#rulesets-release-history-and-historical-requirements)
and [original requirements](docs/REQUIREMENTS.md) preserve historical scope.
