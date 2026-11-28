# OpenVINO 0.23.0 validation

[Setup and lifecycle](../../../training/openvino.md) · [Release notes](../../../releases/0.23.0.md)

This records newly executed checks for optional OpenVINO inference. Prior user
Iris Xe forward-pass measurements are motivation, not newly executed evidence.

## Environment

Linux, Intel Xeon Platinum 8370C CPU, Node 24.19.0, PyTorch 2.14.1+cpu and
OpenVINO 2026.4.1. Portable backend checks ran on both Python 3.12.14 and the
requested Python 3.14.4. OpenVINO enumerated operational **CPU only**. Intel GPU,
CUDA, MPS and WSL2 execution were unavailable on this host.

## Checks

- `make check`: documentation, Ruff formatting/lint, mypy, TypeScript, full
  Python tests, frontend unit tests and production Vite build passed. The full
  Python collection at that point had **697 passes and five hardware skips**;
  the final legacy-worker transport regression was added and checked separately.
  Frontend: **175 passes in 25 files**. The existing large-bundle warning remains.
- `make foundation docs lint typecheck` passed again after implementation review.
  Foundation exercises real PyTorch autograd, FastAPI and Optuna.
- Final legacy resume/scheduling checks passed, covering ordinary old-worker
  leases, unchanged modern transport bytes, safe legacy resume projection, tensor
  preservation, source-file preservation and explicit backend intent on retries.
  The final default inference/batching regression run passed **29 tests**, with
  one explicit CUDA skip. Final missing-runtime/sequential/cancellation checks
  passed two tests; production deployment checks passed all **21 tests**.
- Python 3.14.4 OpenVINO/complete-benchmark suite: **38 passes, four Intel GPU
  skips**. This exercises the actual TorchScript route, without `torch.export`
  or environment mixing. Python 3.12 portable backend checks passed as well.
- Actual browser suite plus focused rerun: **56 distinct desktop/mobile passes**,
  four existing mobile duplicate checks skipped. The two new tests initially used
  an exact label selector that did not resolve in Playwright; both passed after
  correcting it to a combobox role selector. Other gameplay, tournament, security,
  worker management, training/resume and historical dashboard checks passed.
- A separate locked environment installed only training/dev extras: OpenVINO was
  absent, device reporting advertised PyTorch only, CPU training made an actual
  optimizer update, checkpoint loading worked, and explicit OpenVINO selection
  returned the actionable installation error.
- That environment also loaded an OpenVINO-produced full checkpoint and resumed
  through iteration five using PyTorch, with nonempty optimizer state and replay.
  No OpenVINO runtime was installed for that cross-backend resume.

The release workflow additionally requires successful **Verify** CI on `main` for
the exact annotated release commit, then verifies the stable release asset digest.
The published release links that CI run. Local checks above do not assert target
host production deployment or physical mobile/Safari validation.

## Numerical and training coverage

FP32 comparisons use absolute tolerance `3e-5`, relative tolerance `1e-4`, comparing
all raw logits and defender-relative values, then shared legal-policy results.
Brandubh 32 channels / 2 blocks, Tablut 48 / 3 and Copenhagen 64 / 4 cover batches
1, 8, 16, 32 and changing batches 3/19/1, both turns, legal midgames, terminal
results and immediate escapes. Tests check shapes, finite outputs, source/target
ordering, legal masks, normalization, repeatability and saved-checkpoint weights.

A real optimizer update changes the authoritative PyTorch model while a frozen
OpenVINO snapshot remains unchanged; refreshing brings inference back into parity.
Every compiled snapshot also performs a runtime parity check before activation.
Training tests exercise batched auto, explicit sequential, real updates, portable
registration/checkpoints, same-backend resume, changed-backend resume, cancellation
and safe preservation after a conversion failure. Evaluation identities and
promotion remain under the existing regression suite.

TorchScript deprecation and GroupNorm's fixed-spatial-size guard warning are
expected and documented. Their presence is not a claim of universal conversion
support for future architectures or dependency versions.

## Performance scope

The reproducible `benchmark-training` command measures full iterations, with
separate refresh, self-play, optimization, evaluation and artifact timings;
startup and all warm-up iterations additionally appear in process wall time.
Its report includes actual batch distributions, completed/requested simulations,
deadline cutoffs, throughput and process peak RSS. See the
[benchmark instructions](#complete-iteration-benchmark).

Only a diagnostic CPU-plugin comparison can run here. It establishes neither Intel
GPU performance nor a universal CPU advantage. No new GPU end-to-end speedup is
claimed. Target Intel hardware must run the documented Tablut 24/32-concurrent and
Copenhagen 32-concurrent comparisons with sufficiently sized search/game budgets.

The [isolated diagnostic report](../raw/inference/openvino-cpu-diagnostic-2026-10-23.json)
uses Brandubh 32/2, eight concurrent two-ply games, three repetitions and one
warm-up plus three measured iterations per repetition on Python 3.14.4. Actual
batches averaged 8; each backend completed 288/288 measured simulations with no
deadline cutoffs. Mean full-iteration wall: PyTorch CPU 0.1630 s, OpenVINO CPU
2.0463 s, a 0.0797× ratio. This is an overhead-dominated CPU diagnostic, not a GPU
benchmark or full-length-game performance claim. No test suite ran concurrently.

## Complete-iteration benchmark

The existing `benchmark` command measures self-play only. Use the new command
for complete iterations, including optimization, evaluation and model refresh:

```sh
.venv/bin/hnefatafl benchmark-training --config work/intel-benchmark.json \
  --output work/intel-benchmark-results.json
```

Example `work/intel-benchmark.json`:

```json
{
  "training": {
    "preset": "recommended",
    "ruleset": "tablut",
    "channels": 48,
    "blocks": 3,
    "games": 32,
    "self_play_concurrency": 32,
    "inference_batch_size": 32,
    "cpu_threads": 4,
    "seed": 17,
    "mcts_time_limit": 300
  },
  "repetitions": 3,
  "warmup_iterations": 1,
  "measured_iterations": 3,
  "inference_device": "GPU"
}
```

Run Tablut with both 24 and 32 games/concurrency. Also test Brandubh 32 channels /
2 blocks as a regression baseline, and Copenhagen 64 / 4 with 32 games/concurrency.
Retain identical budgets within each comparison. These recommended-budget runs can
be long. Use the smoke preset and small ply limits first to validate the setup.

Each backend/repetition starts in a fresh subprocess with identical seeded initial
weights and config. Backend order alternates. Warm-up iterations are recorded but
excluded from averages. Both use FP32, CPU optimization and batched self-play.
Reports include software/hardware, all per-iteration phase timings, conversion,
compilation/warm-up, refresh, actual batch distribution, completed/requested
simulations, cutoffs, throughput, games/hour and process peak RSS where supported.
Iteration wall includes checkpoint persistence; compute time excludes persistence.
Process wall additionally includes startup, warm-up iterations and final registration.
`games_per_hour` retains self-play phase semantics; `iteration_games_per_hour`
uses complete iteration wall time, including persistence. Both count generated
self-play games; the separate completed/drawn/truncated counters distinguish rule
results from ply-budget truncations. Memory is process high-water RSS, not measured GPU memory. Missing GPU scenarios
are explicitly skipped. Retained run directories contain inputs, logs, checkpoints
and the full JSON report; no model is published to the server.

Compare complete work as well as wall time: numerical differences can change
trajectories, and deadlines can change completed simulations. Never treat faster
forward passes as measured full-training acceleration.

## Diagnostic CPU result on the release host

The [newly executed CPU-only report](../raw/inference/openvino-cpu-diagnostic-2026-10-23.json)
compares Brandubh 32/2 on Python 3.14.4, three repetitions, one warm-up and three
measured iterations per repetition. Eight concurrent games were deliberately
limited to two plies; actual average inference batch size was 8. Both backends
completed 288/288 requested MCTS simulations across measured iterations, with
zero deadline cutoffs. This is a functional smoke workload, not full-length games.

Mean complete iteration wall time was **0.1630 s for PyTorch CPU** and
**2.0463 s for OpenVINO CPU** (0.0797× speedup, therefore slower). Conversion and
compilation dominate such tiny iterations. This diagnostic is not an Intel GPU
comparison and predicts no GPU training speedup or general CPU ranking.
