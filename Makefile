PYTHON := .venv/bin/python
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export npm_config_cache ?= $(CURDIR)/.cache/npm
export OMP_NUM_THREADS ?= 2
export MKL_NUM_THREADS ?= 2

.PHONY: install foundation test lint format typecheck build check serve docs deployment-check
install:
	uv sync --frozen --extra train --extra dev
	npm --prefix frontend ci

foundation:
	$(PYTHON) -c "from fastapi import FastAPI; import torch, optuna; x = torch.tensor([2.], requires_grad=True); (x*x).sum().backward(); assert x.grad.item() == 4.; print('FastAPI, PyTorch autograd and Optuna ready')"

test:
	$(PYTHON) -m pytest
	npm --prefix frontend test
	npm --prefix frontend run test:benchmarks

lint:
	.venv/bin/ruff check hnefatafl tests
	.venv/bin/ruff format --check hnefatafl tests
	npm --prefix frontend run format:check

format:
	.venv/bin/ruff format hnefatafl tests
	npm --prefix frontend run format

typecheck:
	.venv/bin/mypy hnefatafl
	npm --prefix frontend run typecheck

build:
	npm --prefix frontend run build

docs:
	$(PYTHON) scripts/check_docs.py

check: docs lint typecheck test build

# Release-blocking production deployment regression gate. The full suite still
# runs these tests; this target makes the real lifespan + exclusive flock path
# explicit in CI.
deployment-check:
	$(PYTHON) -m pytest tests/test_deployment.py tests/test_deployment_lifecycle.py -q
	$(PYTHON) -m pytest tests/test_player_tournaments.py -q -k 'startup_survives_exclusive_deployment_gate or recovery_defers_only or recovery_propagates_unexpected or shutdown_while_recovery_is_deferred or due_scheduled_start_stays_blocked'

serve:
	$(PYTHON) -m uvicorn hnefatafl.server:app --host 0.0.0.0 --port 8000
