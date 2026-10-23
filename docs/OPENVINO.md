# Optional Intel GPU inference

[Training](TRAINING.md) · [Remote workers](REMOTE_TRAINING.md) · [Performance](PERFORMANCE.md)

OpenVINO is an explicit, optional self-play inference backend. Game simulation,
legal moves and MCTS traversal stay on the CPU. PyTorch on the CPU owns the
model, gradients, optimizer and checkpoints. Evaluation still uses the current
PyTorch candidate and frozen benchmark/champion models; its budgets, seeds,
paired sides and promotion rules are unchanged.

## Installation and detection

From the same checkout/environment used to start a worker:

```sh
uv sync --frozen --extra train --extra openvino
.venv/bin/hnefatafl devices
```

This release locks OpenVINO 2026.4.1. The optional extra supports OpenVINO
2025.4 through 2026.x; use the lockfile for reproducibility. Do not combine Python
environments through `PYTHONPATH`. Ordinary server/CPU/CUDA installations do not
need this extra. Normal `make install` and production deployment retain the training/dev
extras without installing OpenVINO; CI requests all extras for portable parity tests.
Installing it never selects it for a job.

Install Intel's supported GPU driver and OpenCL/Level Zero compute runtime for
your hardware and OS. For WSL2, update WSL and the Windows Intel graphics driver,
then follow Intel's current WSL GPU runtime instructions. A GPU visible to Windows
or PyTorch is insufficient: OpenVINO must enumerate and successfully compile on it.
No runtime or driver is installed automatically by Hnefatafl.

The devices command and worker cards report the OpenVINO version and operational
device IDs/names. Capability probing compiles and executes a small instance of
our actual network in a subprocess, once per process. A native crash or timeout
is reported without crashing the HTTP server or worker supervisor. Restart the
worker after repairing drivers/dependencies to refresh its cached report.

`GPU`, `GPU.0` and other enumerated numbered GPU identifiers are accepted.
`GPU` also matches an operational numbered GPU. `auto` means GPU for OpenVINO;
it never falls back to CPU. `CPU` is available explicitly for diagnostics.
Composite fallback devices such as `AUTO:GPU,CPU` are deliberately rejected.
An unavailable explicitly selected device fails clearly. Missing metadata in
older worker reports means OpenVINO unavailable. Legacy protocol-1 workers receive
ordinary PyTorch jobs without the new default keys; resume transport copies omit
only backend preferences, preserving tensors, optimizer/replay/RNG and source files.

## Intel remote worker example

Enroll a worker in the AI Workshop as an administrator, then on the Intel machine:

```sh
uv sync --frozen --extra train --extra openvino
.venv/bin/hnefatafl devices
read -r -s HNEFATAFL_WORKER_TOKEN
export HNEFATAFL_WORKER_TOKEN
.venv/bin/hnefatafl worker --server https://tafl.example.org \
  --work-dir ./data/remote-worker --concurrency 1
```

Keep existing authentication, private networking/HTTPS and worker capacity limits.
One iGPU shares memory bandwidth with CPU optimization; start with one job.

In the Workshop select that worker, **Training device: CPU** and **Neural inference
backend: OpenVINO — Intel GPU**. The detected GPU name is shown. Any remote worker
can be selected instead; the job waits for a compatible worker with capacity.
Named workers are validated when queuing and all claims are validated server-side.
Unknown capability data never enables a named target's GPU option. Changing targets
or presets preserves an explicit backend choice and shows incompatibilities.

Equivalent training JSON, saved as `work/intel-training.json`:

```json
{
  "preset": "recommended",
  "ruleset": "tablut",
  "device": "cpu",
  "inference_backend": "openvino",
  "inference_device": "GPU",
  "self_play_mode": "batched",
  "self_play_concurrency": 32,
  "inference_batch_size": 32,
  "cpu_threads": 4,
  "output_dir": "data/intel-training"
}
```

```sh
.venv/bin/hnefatafl train --config work/intel-training.json
```

This example explicitly opts into concurrency 32; the recommended Tablut budget
still generates 24 games per iteration, limiting actual batches to at most 24.
Select CPU explicitly on machines whose automatic PyTorch device is CUDA or MPS.
OpenVINO with CPU optimization never silently moves explicit CUDA/MPS training.
Use an explicit `games: 32` experiment when measuring 32 concurrent games.
The optimization `batch_size` remains an independent setting.

## Defaults, synchronization and portability

Absent metadata means `inference_backend: pytorch`, `inference_device: auto`.
CPU/CUDA/MPS inference follows the PyTorch optimization device as before.
Existing presets and budgets are unchanged. Auto self-play still selects
sequential for PyTorch CPU/MPS and batched for CUDA. Only explicit OpenVINO
selects batched auto on a CPU training worker. Explicit sequential/batched modes
are respected; concurrency, games and inference batch limits are never raised.

Before each self-play iteration, the backend clones the authoritative CPU model,
traces that frozen copy with TorchScript and converts/compiles one bounded dynamic
batch graph. Its batch dimension ranges from 1 to the minimum of games,
concurrency and inference batch limit. No padding, additional queue delays or
compiled batch cache is used. Conversion/compilation occur outside per-move MCTS
deadlines and are charged to iteration time. A request buffer is reused; results
are copied before reuse. Every compiled snapshot is checked against PyTorch on an encoded starting position
with the documented FP32 tolerance before replacing the previous representation.
Model refresh happens once per iteration, after any
previous optimization, never per batch. Resources are released on completion,
cancellation and Python exceptions. Native failures remain isolated in the
existing training subprocess; previous valid checkpoint files are retained.

TorchScript avoids the experimental export route implicated in the prior native
crash report. PyTorch 2.14 deprecates tracing, so future dependency upgrades need
parity tests before changing conversion routes. GroupNorm's trace warning comes
from its fixed spatial-size validation guard; supported boards always have more
than one spatial element. Variable-batch tests verify that the traced operations
do not freeze batch-dependent computation. This is not a promise that arbitrary
future architectures are safe to trace.

Shared encoding and legal-policy postprocessing preserve source/target indexing,
defender-relative values, terminal handling and legal softmax probabilities.
FP32 parity tests use absolute tolerance `3e-5`, relative tolerance `1e-4`.

Checkpoints retain the existing validated PyTorch tensor/primitives format;
OpenVINO compiled objects are never saved. Resume creates a new inference model
from restored PyTorch weights. The resume dialog allows backend/device changes
while preserving network identity, optimizer buffers, replay, RNG and history.
When switching to PyTorch select inference device `auto`. The CLI also supports
an explicit new backend in resume JSON. Old checkpoints default to PyTorch.

## Full-training benchmark

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

## Monitoring and limitations

The dashboard reports optimization device, self-play backend/device, PyTorch
evaluation device, actual self-play mode, average/min/max inference batches and
existing throughput charts. Conversion, compilation including one warm-up request,
and total refresh are separate counters; total refresh includes those components
and must not be added to them again. The enclosing iteration includes refresh.
Host inference activity includes encoding and postprocessing; no Intel GPU
utilization or device kernel time is inferred. Historical jobs without fields
remain readable.

The user's earlier Iris Xe inference-only results favored larger Tablut/Copenhagen
networks and batches around 32–64. Those are prior user measurements, not tests
executed here. This release's executing host has OpenVINO CPU only, with no Intel
GPU or CUDA. Portable FP32 and training tests establish CPU-plugin correctness;
Intel GPU correctness/performance and WSL2 deployment still require target hardware.
The tiny diagnostic CPU benchmark is not evidence for GPU speedup. Small networks,
small batches, CPU traversal and conversion overhead can make OpenVINO slower.

If installation is broken, inspect `openvino_error` or the worker log, correct the
runtime and restart the worker. A failed job is not automatically retried. Resume
its valid checkpoint with a working backend. Cancellation is checked around refresh
and between searches/optimizer updates; a native conversion/compile call cannot be
interrupted cooperatively, but the existing supervisor can terminate its process.

### Diagnostic CPU result on the release host

The [newly executed CPU-only report](benchmarks/openvino-cpu-diagnostic-2026-10-23.json)
compares Brandubh 32/2 on Python 3.14.4, three repetitions, one warm-up and three
measured iterations per repetition. Eight concurrent games were deliberately
limited to two plies; actual average inference batch size was 8. Both backends
completed 288/288 requested MCTS simulations across measured iterations, with
zero deadline cutoffs. This is a functional smoke workload, not full-length games.

Mean complete iteration wall time was **0.1630 s for PyTorch CPU** and
**2.0463 s for OpenVINO CPU** (0.0797× speedup, therefore slower). Conversion and
compilation dominate such tiny iterations. This diagnostic is not an Intel GPU
comparison and predicts no GPU training speedup or general CPU ranking.
