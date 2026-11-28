# Quick start

This is the shortest supported local installation. It uses CPU PyTorch from the
locked environment. For a persistent service, reverse proxy or Proxmox container,
use [installation](../installation/README.md) instead.

## Prerequisites

- Python 3.12 recommended (3.11+ supported).
- Node.js 22.12+ or 24+.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (CI pins 0.12.23).
- Git checkout of this repository.

## Install and run

From the repository root:

```sh
make install
make build
.venv/bin/python -m hnefatafl.accounts init-admin
bash scripts/start.sh
```

The administrator prompt asks for a username (default **Odin**), email and
password. There is no default password and no public registration by default.

The server binds `127.0.0.1:8000` and serves the built client and API. Verify it:

```sh
curl --fail http://127.0.0.1:8000/api/health
```

Then open <http://127.0.0.1:8000>, sign in and create a game on **The Great Hall**.
Ordinary accounts can play; training, AI testing and model management require an
administrator permission.

## Storage and configuration

`HNEFATAFL_DATA_DIR` selects persistent storage (default `./data`). The
application does not load `.env` automatically; export variables in the shell or
service unit. Every supported variable is listed in
[configuration](../installation/configuration.md). Do not commit `data/`,
databases, checkpoints or credentials.

## Live development

```sh
bash scripts/dev.sh
```

This starts a reloadable API on port 8000 and Vite on port 5173, proxying `/api`
to the backend. Vite binds all interfaces, so use it only on a trusted network.
See [development setup](../development/setup.md) for focused test commands and
the branch/pull-request workflow.

## First checks

```sh
make docs       # local Markdown links, anchors and images
make check      # docs, lint, types, backend/frontend tests and production build
```

Browser end-to-end tests are separate and require Playwright Chromium; see
[testing](../development/testing.md).
