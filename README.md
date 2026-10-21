# Hnefatafl

A browser-based Viking board game with Tablut, Brandubh and Copenhagen rules,
search opponents and a separate self-play learning workflow.

## Foundation release

This first milestone establishes the architecture and locked Python environment.
It is a development foundation; playable rules and the web application follow in
subsequent verified milestone releases. See [PROGRESS.md](PROGRESS.md).

Python 3.11+, Node 22+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)
are required for development. The lockfile selects official CPU PyTorch wheels so
development does not require a GPU or large CUDA runtime downloads.

```sh
uv sync --frozen --all-extras
make foundation
```

The foundation check exercises a real PyTorch forward/backward operation and imports
FastAPI and Optuna. See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries,
the serializable state format and the plan for future server-hosted multiplayer.

## Releases

Milestone versions use `v0.x.0` until the initial feature set is verified. Each GitHub
release identifies its exact scope, checks, usage and remaining limitations. Full
training quality and hardware-dependent GPU validation are reported separately from
functional correctness.
