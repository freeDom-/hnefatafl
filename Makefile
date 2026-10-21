PYTHON := .venv/bin/python

.PHONY: install foundation test lint typecheck build check serve
install:
	uv sync --frozen --all-extras
	npm --prefix frontend ci

foundation:
	$(PYTHON) -c "from fastapi import FastAPI; import torch, optuna; x = torch.tensor([2.], requires_grad=True); (x*x).sum().backward(); assert x.grad.item() == 4.; print('FastAPI, PyTorch autograd and Optuna ready')"

test:
	$(PYTHON) -m pytest
	npm --prefix frontend test

lint:
	.venv/bin/ruff check hnefatafl tests

typecheck:
	.venv/bin/mypy hnefatafl
	npm --prefix frontend run typecheck

build:
	npm --prefix frontend run build

check: lint typecheck test build

serve:
	$(PYTHON) -m uvicorn hnefatafl.server:app --host 0.0.0.0 --port 8000
