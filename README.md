# Hnefatafl — The King's Table

Play Tablut, Brandubh and Copenhagen in a responsive browser application. Challenge
search opponents, watch computers compete, or train and import a neural strategist
using real self-play learning.

![Brandubh in the desktop application](docs/screenshots/desktop.png)

## Features

- Three source-specific engines with legal moves, captures, special rules and replay.
- Pass-and-play, Human vs AI and AI vs AI; configurable opponents, side selection,
  playback speed, pause, undo/redo, restart, captured pieces and in-game rules.
- Tactical baseline, iterative alpha-beta, Monte Carlo tree search and neural search.
- Saved agent position estimates from actual move searches, background analysis
  and configurable live/replay advisors with progressive legal-move hints.
- Round-robin tournaments for two to four players, live progress, standings,
  battlefield opponent win matrices and match replay.
- SQLite histories, stable local profiles and filtered player/AI statistics and ratios.
- Sortable records, seed-independent AI grouping and inspectable effective settings.
- English/German localization, saved game-mode preference and mythological tournament names.
- Complete saved game setups and persistent player/model runes across games and tournaments.
- Light/dark/wood/device appearance, independent board themes, Viking figures and carved-wood tokens.
- Training workshop and CLI: actual PyTorch self-play updates, evaluation, safe model
  import/export, cancellation, exact CPU checkpoint resumption and Optuna studies.
- Reviewed UI resumption with editable total iterations, learning rate, weight decay and SGD momentum.
- Board-specific training plans, live learning graphs and optional retention of every new best model.
- Model renaming, reusable recorded training settings and independent training-data cleanup.
- Remote API workers with simultaneous training slots, retained progress and automatic model transfer.
- CPU operation and configurable CUDA/MPS support, with automatic device detection.

Mouse, touch and keyboard are supported. Arrow keys navigate; Enter/Space select;
Escape clears selection. See the [mobile screenshot](docs/screenshots/mobile.png).
A server connection is required; internet multiplayer and offline play are later work.

## Install and start

Use Python **3.12 recommended** (package supports 3.11+), Node **22+**, and
[uv](https://docs.astral.sh/uv/getting-started/installation/). From the repository root:

```sh
make install
make build
bash scripts/start.sh
```

The server serves the built frontend and API on port 8000, bound to loopback by
default. For a phone on the same trusted LAN, run
`HNEFATAFL_HOST=0.0.0.0 bash scripts/start.sh` and open
`http://<server-LAN-address>:8000` on the phone. The startup script limits default
CPU math threads to two, respecting explicit environment overrides.

Equivalent installation commands are `uv sync --frozen --all-extras`,
`npm --prefix frontend ci`, and `npm --prefix frontend run build`. The lockfile
selects official CPU PyTorch wheels; see [TRAINING.md](docs/TRAINING.md) for CUDA.
Set `HNEFATAFL_DATA_DIR` for persistent storage (default `./data`). `.env.example`
is documentation; `.env` is not automatically loaded. For live development use
`bash scripts/dev.sh`.

## Play and replay

Choose a battlefield and mode in **New game**. Configure your side and opponent,
or both opponents for AI vs AI. Select a piece and a highlighted destination.
The server validates every move and rejects stale concurrent updates.

**Hall of records** puts statistics above a separate, collapsible saved-game history.
Delete a game after confirmation to remove its moves/result and recalculate statistics.
The page also contains profiles and replay. Viewing
an earlier move preserves the recorded result and future moves; making a different
move branches the local history. Restart creates a new game from an unfinished
record, preserving the old one. Completed games cannot restart, even during replay.

Both statistics tables have sortable column headers. Search **Filter players** in
the performance table or **Filter recorded games** in history independently.
AI rows show a compact summary; the button beside their name opens the full parameters.

New-game human selectors list existing players or let you enter a new name. Delete
a player in **Hall of records** to remove it from future selections while retaining
its historical identity, results and replay. AI rows and recorded games have
expandable parameter details; depth, search budgets, seed and other effective settings
distinguish AI records. Battlefields are ordered 7×7, 9×9, 11×11 throughout the UI.

Open **Appearance** in the header to choose light/dark/device mode, Classic stones,
Viking figures or Carved wood tokens, and Golden oak, Old walnut or Fjord slate boards.
Choices are independent and saved in this browser. See
[PERSONALIZATION.md](docs/PERSONALIZATION.md) for settings and theme extension points.
Fresh browsers default to Carved wood tokens on an Old walnut board; saved choices
are preserved.

New games and restarts receive fresh server-generated random seeds. The saved game
shows its seed; a fixed seed remains an explicit option for reproducible comparisons.
Compatible AI models are preselected by comparable baseline evaluation results,
with a labeled newest-model fallback when no such result is available.

Rules: [Tablut market rules](docs/rules/tablut.md), [Brandubh](docs/rules/brandubh.md),
[Copenhagen](docs/rules/copenhagen.md). The linked Tablut leaflet starts defenders,
uses sandwich king capture and any-edge escape. Other Tablut reconstructions differ.

## Run an AI tournament

Open **Tournaments** to compare two to four configured opponents across one or more
battlefields. Every pair plays the chosen even number of games and swaps sides.
Participants use their model or AI profile name, with network/training summaries
retained in saved model comparisons.
Choose the number of games per pairing on each battlefield,
a move limit and shared seed, then follow progress and inspect standings, results
by battlefield and individual game replays. Saved tournaments remain available
after reloading or restarting the server. Stop a running tournament to retain its
finished matches, or delete an inactive tournament after confirmation.

Wins score one point and rules draws half a point. Games stopped at the move
limit are marked unfinished and receive no points. See [TOURNAMENTS.md](docs/TOURNAMENTS.md)
for configuration, scoring and data lifecycle details.

## Train and use a model

In **AI workshop**, choose a ruleset and **Quick check** for a tiny real training
job. Inspect progress and evaluation, resume a checkpoint, or export/import a model.
Select **Neural network** and a compatible model when configuring a new opponent.
Use the name-suggestion button to generate a Viking name. Model cards offer deletion
with confirmation: saved game history and original training checkpoints are retained.
Model cards show the recorded network and key training settings, including seed.
Rename a model or load its recorded parameters into the form from its card.
Delete an inactive job's training data separately from the progress panel; this
removes its resumption checkpoints and logs while keeping registered models and
saved games. The model-delete dialog lists unfinished
games that still use a model, including records from other browser sessions. You
can explicitly discard those unfinished games without results and delete the model;
completed history is kept. Running tournaments must be stopped first.
The CLI requires no Python programming:

```sh
.venv/bin/python -m hnefatafl.cli devices
.venv/bin/python -m hnefatafl.cli train --config configs/train-smoke.json
.venv/bin/python -m hnefatafl.cli tune --config configs/tune-smoke.json
.venv/bin/python -m hnefatafl.cli models --registry data/models
.venv/bin/python -m hnefatafl.cli tournament --config configs/tournament.json
```

The workshop defaults to board-specific recommended plans; see
[TRAINING_PLAN.md](docs/TRAINING_PLAN.md) for their architecture and budgets.
Use `configs/train-beginner.json` for a smaller explicit experiment. `last.pt` retains optimizer,
replay, RNG and the portable best model for exact resumption; `best.pt` and registry
exports are compact inference snapshots. Resume uses a higher target **total**
iteration count. See [TRAINING.md](docs/TRAINING.md) and [AI.md](docs/AI.md).

Small runs prove the software workflow, not stronger play. Truncated games are
reported separately from rules draws. Measure strength with larger balanced seeded
tournaments. Training workers run separately so gameplay remains responsive.

For a cloud CPU/GPU, enroll a worker in **AI workshop**, start the provided CLI
on that machine, and choose a remote execution target for new or resumed jobs.
Multiple worker slots train independent models concurrently; the server retains
their charts, checkpoints and imported models. See
[REMOTE_TRAINING.md](docs/REMOTE_TRAINING.md) for setup and access requirements.

## Tests

```sh
make check
cd frontend
npx playwright install chromium
npm run test:e2e
```

`make check` runs Ruff/Prettier, Python/TypeScript type checks, pytest, Vitest and
the production build. Playwright uses the actual backend on isolated ports
8001/4173 for desktop/mobile gameplay and real training followed by neural play.
Four duplicate mobile worker checks are deliberately skipped; mobile gameplay,
appearance, records/statistics and tournament lifecycle checks run. Actual training,
model cleanup and tournament cancellation run on desktop.
Linux Chromium libraries may need installation on a new host.

See [VALIDATION.md](docs/VALIDATION.md) for evidence and hardware/source limitations.
GitHub Actions repeats these checks. `make format` formats source files.

## Project map and deployment

React/TypeScript/Vite provide one browser client; FastAPI owns authoritative sessions.
SQLite supports simple Linux/LXC installation. The pure Python engine is independent
of HTTP, persistence and tensors; PyTorch/Optuna run in independent workers.

| Path | Responsibility |
| --- | --- |
| `hnefatafl/` | Engine, AI, models, training, CLI, storage, API |
| `frontend/` | Board, archive, workshop, unit/browser tests |
| `tests/` | Engine, AI, model, training and API suites |
| `configs/` | Runnable training, tuning and tournament configurations |
| `scripts/`, `deployment/` | Startup, systemd and reverse-proxy examples |
| `docs/` | Rules, training, deployment, evidence and release notes |

Read [ARCHITECTURE.md](ARCHITECTURE.md), [DEPLOYMENT.md](docs/DEPLOYMENT.md),
[PROGRESS.md](PROGRESS.md) and the [requirements](docs/REQUIREMENTS.md).
The server also exposes API documentation at `/docs`.

Use a trusted network or authenticated TLS reverse proxy: local profiles do not
provide account authentication, and training endpoints are administrative capabilities.
Back up the entire data directory consistently before updates. Docker is unnecessary.

Current limitations: CUDA/MPS and systemd/proxy operation need validation on the target
host. Copenhagen's primary source returned HTTP 455; its rules document identifies
accessible transcriptions, the shieldwall interpretation and repetition convention.
Model strength needs longer training and fair evaluation. Internet accounts/lobbies,
WebSockets, distributed self-play and PostgreSQL migration tooling are future work.

Milestone versions use `v0.x.0` with explicit scope and validation. See
[GitHub Releases](https://github.com/freeDom-/hnefatafl/releases) and
[RELEASING.md](docs/RELEASING.md).
