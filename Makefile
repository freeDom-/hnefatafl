PYTHON := .venv/bin/python
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export npm_config_cache ?= $(CURDIR)/.cache/npm
export OMP_NUM_THREADS ?= 2
export MKL_NUM_THREADS ?= 2

.PHONY: install foundation test lint format typecheck build check serve docs
install:
	uv sync --frozen --extra train --extra dev
	npm --prefix frontend ci

foundation:
	$(PYTHON) -c "from fastapi import FastAPI; import torch, optuna; x = torch.tensor([2.], requires_grad=True); (x*x).sum().backward(); assert x.grad.item() == 4.; print('FastAPI, PyTorch autograd and Optuna ready')"

test:
	$(PYTHON) -m pytest
	npm --prefix frontend test

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

serve:
	$(PYTHON) -m uvicorn hnefatafl.server:app --host 0.0.0.0 --port 8000
