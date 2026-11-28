# Optional Intel GPU inference

[Training](./getting-started.md) · [Remote workers](./remote-workers.md) · [Performance](./performance.md)

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

## Backend and resume behavior

Defaults are PyTorch/auto. Explicit OpenVINO is opt-in and uses a CPU training
worker with supported Intel inference; existing PyTorch CPU/CUDA/MPS behavior is
unchanged. OpenVINO compiled objects are never saved: each iteration refreshes from
the authoritative PyTorch weights, and resume rebuilds inference state from the
tensor-only checkpoint while preserving optimizer/replay/RNG/history. Switching back
to PyTorch uses inference device `auto`. Conversion/compilation/synchronization and
parity internals are in [OpenVINO implementation](../development/openvino.md).

## Complete-iteration benchmark

See the [evaluation report](../evaluation/benchmarks/inference/openvino.md#complete-iteration-benchmark) for the full-training benchmark procedure, workload comparisons and CPU diagnostic.

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

See the [OpenVINO evaluation report](../evaluation/benchmarks/inference/openvino.md#diagnostic-cpu-result-on-the-release-host) for the diagnostic CPU measurement and interpretation.
