# Development and verification

[Documentation index](../../README.md) · [Architecture](./architecture.md) · [Working conventions](../../AGENTS.md)

## Environment and startup

Use the existing checkout; cloud tasks are already isolated. Do not create a Git
worktree unless explicitly requested. Python 3.12 is recommended (3.11+ supported),
Node.js 22.12+ (or Node.js 24+) is required (CI runs the frontend unit/build job
on Node 22.12 and the browser suite on Node 24, and fails on moderate npm
advisories), and CI pins uv 0.12.23. From the root:

```sh
make install
make foundation
.venv/bin/python -m hnefatafl.accounts init-admin
bash scripts/dev.sh
```

`make install` uses `uv sync --frozen --extra train --extra dev` and `npm --prefix frontend ci`.
For OpenVINO parity tests, explicitly add `--extra openvino` to that sync command.
CI installs all extras to exercise the portable OpenVINO CPU plugin.
The lock selects CPU PyTorch. The Makefile places uv/npm caches under `.cache`
and defaults OMP/MKL math threads to two. `foundation` checks real PyTorch autograd
and FastAPI/Optuna imports. Development starts a reloadable loopback API on 8000
and Vite on its default port 5173 (Vite binds all interfaces). Vite proxies `/api`
to 8000; `VITE_API_TARGET` can override that destination. Keep dev access trusted.

For a production-shaped local check, run `make build` then
`HNEFATAFL_DATA_DIR=/absolute/disposable/path bash scripts/start.sh`. The script
requires the built frontend and starts one API worker. It honors `HNEFATAFL_HOST`
and `HNEFATAFL_PORT`; `dev.sh` uses its fixed API host/port. `.env` is not loaded
automatically. See [Deployment](../installation/installation.md) for persistent/service configuration.

## Browser-analysis runtime integration

Phase 4 browser-analysis work is opt-in and disabled by default. Focused backend
checks (bounded, no sustained measurements):

```sh
.venv/bin/python -m pytest tests/test_browser_runtime_distribution.py   tests/test_browser_model_publication.py tests/test_browser_analysis_api.py   tests/test_browser_analysis_lifecycle_sweep.py tests/test_security.py -q
npm --prefix frontend run test -- src/browserAnalysis
npm --prefix frontend run build
```

The canonical export validator is exercised by
`tests/test_browser_neural_export.py` when the optional ONNX dependencies are
installed (`uv pip install --python .venv/bin/python -r
spikes/browser_offline/requirements-onnx.txt`). Actual-browser verification needs
a production build, Chromium and a staged runtime:

```sh
HNEFATAFL_BROWSER_ANALYSIS_ENABLED=1 HNEFATAFL_DATA_DIR=/absolute/disposable   .venv/bin/python -m hnefatafl.browser_runtime --data-dir "$HNEFATAFL_DATA_DIR"   build --fetch --activate
HNEFATAFL_BROWSER_ANALYSIS_BROWSER=1 HNEFATAFL_BROWSER_ANALYSIS_DATA=/absolute/disposable   .venv/bin/python -m pytest tests/test_browser_analysis_browser.py -q
```

`scripts/verify_browser_analysis.mjs --neural` additionally exercises published
model download, verified-byte inference (one cold artifact transfer), provider
evidence and effective settings; it requires an explicit publication. The
actual-browser suite also stages a second verified runtime and asserts immutable
identity handling during activation. Source-checkout runtime staging requires
`--development`. Never publish
private checkpoints from a development tree. The E1 lifecycle sweep is covered by
stub-command tests (`tests/test_browser_analysis_lifecycle_sweep.py`) and does not
require a browser or a sustained run.

## CLI reference

Use `.venv/bin/python -m hnefatafl.cli --help` (or the installed `hnefatafl` entry
point). All commands print JSON; train/tune stream JSON progress before the final
result. Do not parse their entire stdout as a single JSON document. Failures return
nonzero status and a JSON error on stderr. Paths are relative to the current
working directory unless absolute; the server data variable does not override CLI
configuration paths.

| Command | Interface and guide |
| --- | --- |
| `devices` | Report actual PyTorch and optional OpenVINO availability; [hardware](../training/performance.md#installation-and-hardware) |
| `train --config FILE [--resume FILE]` | JSON training config; safe full checkpoint resume; [training](../training/getting-started.md) |
| `tune --config FILE` | Local Optuna study/trials; [optimization](../training/getting-started.md#hyperparameter-optimization) |
| `tournament --config FILE` | JSON round-robin or fixed-role comparison, final report to stdout; [tournaments](../evaluation/ai-tournaments.md) |
| `benchmark-heuristics [--config FILE] [--output FILE]` | CPU heuristic strength, latency and complete-evaluation comparisons; [evidence](../evaluation/shield-maiden.md) |
| `benchmark-alpha-beta [--config FILE] [--output FILE] [--summary-output FILE]` | Fixed-depth correctness, independent minimax oracle, fixed-time depth and alpha-beta optimization ablations; `--summary-output` writes the compact evidence with the run's source hashes; [evidence](../training/performance.md#alpha-beta-profiling-and-root-window-pruning) |
| `benchmark-training --config FILE [--output FILE]` | Complete CPU/OpenVINO training iterations with repeated seeded runs; [OpenVINO](../training/openvino.md#full-training-benchmark) |
| `benchmark [--config FILE] [--output FILE]` | Fixed-model self-play, optional checkpoint/device/mode selection, no optimizer/model registration; [benchmark protocol](../training/performance.md#benchmark-protocol-and-evidence) |
| `models [--registry DIR]` | List; optional `--import FILE --name NAME` or `--export ID --output FILE`; [models](../training/models.md#checkpoints-registry-import-and-export) |
| `worker --server URL [--work-dir DIR] [--concurrency N] [--once] [--max-jobs N]` | Token from `HNEFATAFL_WORKER_TOKEN`, concurrency 1–16; [workers](../training/remote-workers.md) |

Runnable budgets live in [configs](../../configs): smoke training, beginner training,
smoke tuning and tournaments. Inspect a config before running: a full tournament
or recommended plan can be much more expensive than a syntax/interface check.

`scripts/alpha_beta_strength.py` is a standalone equal-time strength series for
two alpha-beta configurations (wins/draws/losses, 95% interval and mean completed
depth); see the [control-refinement evidence](../evaluation/benchmarks/search-alpha-beta.md#equal-time-strength-comparison-legacy-vs-full-symmetry).
