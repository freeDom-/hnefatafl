# Hnefatafl

A browser-based Viking board game with Tablut, Brandubh and Copenhagen rules,
search opponents and a separate self-play learning workflow.

## Engine milestone

The current engine milestone provides immutable, serializable states, legal move
generation, captures, outcomes and replay for all three rulesets. Browser gameplay
and AI follow in subsequent verified milestone releases. See [PROGRESS.md](PROGRESS.md).

Python 3.11+, Node 22+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)
are required for development. The lockfile selects official CPU PyTorch wheels so
development does not require a GPU or large CUDA runtime downloads.

```sh
uv sync --frozen --all-extras
make foundation
.venv/bin/python -m pytest tests/test_engine.py
```

The foundation check exercises a real PyTorch forward/backward operation and imports
FastAPI and Optuna. See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries,
the serializable state format and the plan for future server-hosted multiplayer.

Rules: [Tablut](docs/rules/tablut.md), [Brandubh](docs/rules/brandubh.md),
[Copenhagen](docs/rules/copenhagen.md). These document the source-specific differences
and implementation conventions. Copenhagen's primary page was unavailable during
research; the cited mirror interpretations and verification limitation are explicit.

## Releases

Milestone versions use `v0.x.0` until the initial feature set is verified. Each GitHub
release identifies its exact scope, checks, usage and remaining limitations. Full
training quality and hardware-dependent GPU validation are reported separately from
functional correctness.
