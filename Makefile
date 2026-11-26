PYTHON := .venv/bin/python
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export npm_config_cache ?= $(CURDIR)/.cache/npm
export OMP_NUM_THREADS ?= 2
export MKL_NUM_THREADS ?= 2

.PHONY: install foundation test lint format typecheck build check serve docs deployment-check historical-refs deployment-cross-version browser-runtime browser-runtime-status browser-model-verify
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
	$(PYTHON) -m pytest tests/test_player_tournaments.py -q -k 'startup_survives_exclusive_deployment_gate or recovery_defers_only or recovery_propagates_unexpected or shutdown_while_recovery_is_deferred or due_scheduled_start_stays_blocked or full_background_lifecycle or startup_failure_cleans or shutdown_while_reconciliation_is_deferred_stops_all'

# Pinned historical sources are fetched into local refs, never committed.
historical-refs:
	$(PYTHON) scripts/fetch_historical_refs.py

# Genuine old-updater/target-app subprocess test. Requires a built frontend and
# the pinned historical refs; CI runs this in a dedicated job.
deployment-cross-version:
	HNEFATAFL_CROSS_VERSION_REQUIRED=1 $(PYTHON) -m pytest tests/test_deployment_cross_version.py -q

serve:
	$(PYTHON) -m uvicorn hnefatafl.server:app --host 0.0.0.0 --port 8000

# Optional browser-analysis runtime distribution. Assets are staged outside the
# release directory, verified against pinned digests and activated atomically.
# Browser analysis stays disabled until HNEFATAFL_BROWSER_ANALYSIS_ENABLED=1.
browser-runtime:
	$(PYTHON) -m hnefatafl.browser_runtime --data-dir "$${HNEFATAFL_DATA_DIR:?set HNEFATAFL_DATA_DIR}" build --fetch --activate

browser-runtime-status:
	$(PYTHON) -m hnefatafl.browser_runtime --data-dir "$${HNEFATAFL_DATA_DIR:?set HNEFATAFL_DATA_DIR}" status

# Validate an export before publication (no state change).
browser-model-verify:
	@test -n "$${HNEFATAFL_DATA_DIR}" -a -n "$${EXPORT_DIR}" || (echo "set HNEFATAFL_DATA_DIR and EXPORT_DIR" >&2; exit 2)
	$(PYTHON) -m hnefatafl.browser_models --data-dir "$$HNEFATAFL_DATA_DIR" verify "$$EXPORT_DIR"
