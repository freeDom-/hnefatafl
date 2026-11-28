# Training performance and hardware

[Training index](README.md) · [Parameters](parameters.md) · [Benchmarks](../evaluation/benchmarks/README.md) · [Troubleshooting](troubleshooting.md)

This guide explains how to configure and optimize training. Detailed measurements
and reproduction evidence live in [Evaluation](../evaluation/benchmarks/README.md).

## Choose hardware

```sh
.venv/bin/python -m hnefatafl.cli devices
```

`device: auto` prefers NVIDIA CUDA, then Apple MPS, then CPU. Explicit `cpu`,
`cuda`, `cuda:N` or `mps` fails clearly when unavailable. The locked development
environment uses CPU PyTorch; CUDA requires the official wheel matching the driver
and a separately verified host. MPS/CUDA execution is not validated on every
platform.

For an isolated CUDA environment:

```sh
python3 -m venv .venv-cuda
.venv-cuda/bin/python -m pip install --upgrade pip
.venv-cuda/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv-cuda/bin/python -m pip install -e '.[train]'
.venv-cuda/bin/python -m hnefatafl.cli devices
```

`cu128` is an example, not a claim about the host. Record the actual PyTorch,
driver and GPU with any measurement. Do not run the CPU lock into the CUDA
environment. For web-managed GPU jobs, start the server and worker with the CUDA
environment; workers inherit the server's Python executable. Proxmox NVIDIA
driver/device passthrough is host administration.

## Efficient execution settings

| Setting | Default | Effect |
| --- | --- | --- |
| `self_play_mode` | `auto` | Batched on CUDA/OpenVINO, sequential on CPU/MPS |
| `self_play_concurrency` | 16 | Maximum independent games and batch fill |
| `inference_batch_size` | 32 | Maximum positions per forward pass; never waits to fill |
| `search_workers` | 0 | Persistent CPU search processes for PyTorch CPU models |
| `evaluation_mode` | `auto` | Batched evaluation on CUDA/OpenVINO, sequential otherwise |
| `evaluation_concurrency` | 16 | Concurrent evaluation games/worker cap |
| `evaluation_batch_size` | 32 | Positions per evaluation forward pass |
| `mixed_precision` | true | CUDA AMP; no effect on CPU/MPS |
| `cpu_threads` | 2 | PyTorch/OpenVINO CPU threads |

`batch_size` is the optimizer batch and is independent of inference batching.
Concurrency is cooperative scheduling in one process with one shared model, not one
model copy per game. A larger inference limit cannot fill batches without pending
work. CPU traversal and move generation can dominate a small network, so fewer
forward passes do not guarantee faster generation.

`search_workers` is a cap, not a target: processes start on demand, only as far as a
phase has game slots, and are reused. It applies only to CPU models with PyTorch
inference. It does not distributed-train or share a GPU model.

## Compare fairly

- Keep games, model, ruleset, search budget, precision and inference settings
  identical. Use fixed work or fixed time, not whichever finishes first.
- Use the completed phase's simulations/second. Live snapshots count finished games
  while elapsed time includes ongoing games.
- Inspect actual batch sizes; concurrency cannot exceed remaining games.
- Keep unfinished/truncated games separate from rule draws. Higher throughput caused
  by hitting the ply cap is not progress.
- CPU and GPU results are not interchangeable. A CPU smoke model is not a competitive
  model. See [benchmark methodology](../evaluation/methodology.md).

Canonical measurement reports:

- [Self-play optimization](../evaluation/benchmarks/training-selfplay-optimizations.md)
- [Training monitoring](../evaluation/benchmarks/training-monitoring.md)
- [Evaluation cost](../evaluation/benchmarks/training/evaluation-cost.md)
- [CUDA GPU (RTX 3060)](../evaluation/benchmarks/inference/gpu.md)
- [Exact cache and symmetry](../evaluation/benchmarks/inference/symmetry-and-caching.md)
- [MCTS](../evaluation/benchmarks/search/mcts-phase2.md)
- [Alpha-beta search](../evaluation/benchmarks/search-alpha-beta.md)
- [OpenVINO](../evaluation/benchmarks/inference/openvino.md)
- [Parallel search](../evaluation/benchmarks/search-parallel.md)

## Caching, batching and symmetry

`inference_cache_mode: exact` reuses an evaluation for an identical ruleset/board/
side position. Each phase builds its own cache and drops it when model, backend or
precision changes. It helps repeated positions; measure the hit rate before assuming
a gain.

`training_symmetry_mode: random_d4` augments samples and policy targets with board
symmetries and stores a resumable RNG stream in the checkpoint. It changes training
data, not rules or search budget. Search symmetry/transposition use is separate and
documented in the alpha-beta report.

Batched and sequential execution preserve legal masking, PUCT/backup, root noise,
policy targets and unfinished-game bootstrapping. Per-game seeds remain independent.
Reproducibility within one mode does not require bitwise-identical results across
modes.

## Evaluation cost

Training runs one selected evaluator. Use `evaluation_frequency` independently of
checkpoint cadence and inspect `evaluation_seconds` and each evaluation duration.
Jarl uses real alpha-beta search with its own depth/deadline and can cost more than
neural play. Champion uses equal neural budgets for both models. Evaluation stays
FP32 unless `evaluation_inference_backend: training` opts into the job's OpenVINO
backend. Queue waiting counts against wall deadlines.

## Monitoring overhead

Live monitoring updates one aggregated counter per position and optimizer batch and
republishes a bounded heartbeat about once per second. Full history is written on
real progress events. Overhead and remote forwarding measurements are in the
[monitoring report](../evaluation/benchmarks/training-monitoring.md); operator
states are in [the interface](interface.md#live-monitoring).

## Troubleshooting

- `auto` stays sequential on CPU/MPS by design. Inspect the effective worker's
  PyTorch build before expecting CUDA.
- Small batches mean insufficient pending work or a low concurrency/game limit.
- CUDA out of memory: reduce model size, inference/optimization batches or worker
  slots. Slots do not partition GPU memory automatically.
- CPU overload: return OMP/MKL threads to 2 and reduce `cpu_threads`,
  `self_play_concurrency` or `search_workers`.
- Throughput rose while completion fell: inspect ply caps and unfinished games before
  claiming a gain.
- OpenVINO selected but unavailable: check the optional extra, Intel driver/OpenCL/
  Level Zero runtime and `devices`; see [OpenVINO](openvino.md).

Do not infer strength or GPU speed from a successful smoke run. See
[benchmark methodology](../evaluation/methodology.md) and
[troubleshooting](troubleshooting.md).
