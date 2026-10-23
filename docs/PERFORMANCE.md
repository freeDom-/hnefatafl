# Hardware, self-play performance and benchmarking

[Documentation index](README.md) · [Training workflow](TRAINING.md)

## Installation and hardware

Install the project's Python dependencies using the root README commands.
`torch` and `optuna` are required for training; ordinary engine play does not
need to import them. Inspect actual hardware with:

```sh
.venv/bin/python -m hnefatafl.cli devices
```

`device: "auto"` selects CUDA when available, then Apple MPS, then CPU. Explicit
`cpu`, `cuda`, `cuda:N` or `mps` requests fail clearly if unavailable. For NVIDIA
training, install the CUDA-enabled PyTorch wheel matching your driver using the
current command from [PyTorch's official installer](https://pytorch.org/get-started/locally/).
The development lock uses the official CPU wheel to remain runnable on machines
without CUDA; a CPU wheel alone does not provide NVIDIA acceleration.

CUDA training uses mixed precision when `mixed_precision` is true. The model
stays on the chosen device during self-play and optimization; each optimization
minibatch transfers inputs and targets together. There is no forked CUDA context:
the API launches an independent Python process. Self-play can cooperatively batch
independent games through that worker's shared network; sequential execution
remains the default on CPU/MPS and an explicit option on CUDA. CUDA memory usage
is included in progress when available. This environment verified CPU training; CUDA/MPS
execution requires separate verification on matching hardware.

### Separate CUDA environment

Keep the reproducible CPU `.venv` intact and create a separate GPU environment.
Use the wheel index selected by the official PyTorch installer for your driver;
`cu128` below is an example for a compatible CUDA 12.8 installation, not a claim
that this host has that runtime or hardware:

```sh
python3 -m venv .venv-cuda
.venv-cuda/bin/python -m pip install --upgrade pip
.venv-cuda/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv-cuda/bin/python -m pip install -e '.[train]'
.venv-cuda/bin/python -m hnefatafl.cli devices
.venv-cuda/bin/python -m hnefatafl.cli train --config configs/train-beginner.json
```

As an alternative to selecting a wheel index manually, uv supports backend
selection on the GPU host. After creating/installing the separate environment:

```sh
uv pip install --python .venv-cuda/bin/python --reinstall --torch-backend=auto 'torch>=2.6,<3'
.venv-cuda/bin/python -m hnefatafl.cli devices
```

Verify the resulting backend instead of assuming auto installed CUDA.
The `auto` device chooses CUDA only when PyTorch can actually initialize it. Do not
run the CPU `uv sync` command into this separate environment: the project lock
deliberately selects CPU wheels. Record the chosen PyTorch/driver versions with
your experiment results. This CUDA recipe requires verification on the GPU host.

For web-managed GPU jobs, launch the server with that environment too:
`.venv-cuda/bin/python -m uvicorn hnefatafl.server:app --host 127.0.0.1 --port 8000`.
Workers inherit the server's Python executable. Update the systemd `ExecStart`
similarly. In Proxmox LXC, expose the host NVIDIA devices and matching driver
libraries to the container first; check actual device visibility with `nvidia-smi`.
Driver/device passthrough is host administration, not supplied by installing a wheel.

## Concurrent self-play and performance comparisons

Batched execution preserves legal masking, PUCT/backup semantics, root noise,
policy targets, defender-relative values and truncated-game bootstrapping. Per-game
RNGs/seeds stay independent. Reproducibility within one execution mode does not
require bitwise-identical results across modes.

Start Training and Resume Training expose all three execution fields. Existing
checkpoints/configurations missing them use `auto`, concurrency 16 and inference
batch size 32. Resume can change these settings while retaining the five immutable
checkpoint settings and saved learning state. Update remote-worker code with the
server so workers recognize the new parameters; no new worker protocol, model
format or storage migration is needed.

## Two different batch sizes

| Setting | Default and accepted range | What changes |
| --- | --- | --- |
| `self_play_mode` | `auto`; also `sequential` or `batched` | Execution schedule: auto batches on CUDA, stays sequential on CPU/MPS |
| `self_play_concurrency` | 16; 1–1024 | Maximum independent active games, bounded by games remaining |
| `inference_batch_size` | 32; 1–4096 | Maximum pending positions evaluated in one network forward pass |
| `batch_size` | Preset-dependent; 1–2048 | Replay examples per optimizer update, independent of inference batching |

Concurrency is cooperative scheduling in one training process with one shared model,
not one OS thread or model copy per game. Each MCTS generator yields a pending leaf;
the scheduler batches leaves across independent games and returns each policy/value
result to its own search before that simulation can advance. Finished games are
replaced, while replay callbacks commit results in game-index order with bounded
out-of-order buffering. There is no distributed self-play or multi-GPU optimizer.
Remote-worker slots instead run separate jobs with separate models.

Increase concurrency only when enough games and memory are available. Raising the
inference limit cannot fill batches without pending work. A larger optimization
batch changes learning updates and memory use; it does not create more concurrent
searches. CPU traversal, legal-move generation and policy decoding can dominate a
small network, so fewer forward passes need not mean faster generation.

## Reading performance metrics

Metrics live under `metrics.performance` in progress/result JSON and under
`history[].performance` for completed history points. The workshop shows live and
completed throughput, batch sizes, outcomes, phase times and throughput history
alongside separate loss/evaluation graphs. Rates describe self-play generation,
not full training throughput or model quality. Unfinished games still contribute
to games/hour and positions/s.

| JSON metric | Unit and meaning |
| --- | --- |
| `self_play_wall_seconds` | Elapsed seconds for generation, including search and inference |
| `games`, `completed_games`, `drawn_games`, `truncated_games` | Generated games; completed includes rules draws, so drawn is a subset |
| `self_play_positions`, `mcts_simulations` | Generated positions and actually completed search simulations |
| `requested_mcts_simulations` | Requested self-play visits across actually played moves, including shortcut moves |
| `attacker_wins`, `defender_wins` | Completed self-play wins by side |
| `immediate_win_shortcuts`, `search_deadline_cutoffs` | Searches that used a direct winning move or stopped before their visit budget |
| `games_per_hour`, `positions_per_second`, `simulations_per_second` | Work divided by self-play wall time; not requested budgets |
| `neural_evaluations`, `neural_evaluations_per_second` | Positions evaluated by the network, including search leaves/bootstrap evaluations |
| `inference_batches` | Forward-pass batches, distinct from position evaluations |
| `inference_batch_size_average`, `inference_batch_size_min`, `inference_batch_size_max` | Observed positions per batch, not the configured maximum |
| `inference_time_seconds`, `inference_batch_latency_seconds` | Accumulated host inference seconds and mean seconds per batch |
| `cpu_search_seconds`, `inference_cpu_seconds` | Accumulated process CPU activity, including math threads; can exceed wall time |
| `search_traversal_wall_seconds` | Host traversal time excluding measured inference |
| `optimization_seconds`, `evaluation_seconds` | Wall seconds for those iteration phases |
| `iteration_compute_seconds` | Iteration boundary before artifact persistence |
| `artifact_persistence_seconds`, `iteration_wall_seconds` | Persistence time and final elapsed iteration time including it |
| `gpu_peak_allocated_bytes`, `gpu_peak_reserved_bytes` | Optional CUDA allocator peaks, bytes; not total device memory use |
| `inference_cuda_seconds` | Optional CUDA-event activity in benchmarks, not ordinary training |

Ordinary training avoids extra CUDA events or explicit per-simulation timing
synchronization; only benchmarks collect CUDA activity at phase boundaries.
Host, process CPU and CUDA activity timings measure different things and must not
be added to estimate total duration. Checkpoint history is captured before its own
serialization; final progress/result includes that persistence cost. Missing GPU
fields mean unavailable measurements, not zero GPU usage.

## Benchmark protocol and evidence

Compare modes without starting a training job or registering a model. `--output`
also saves the JSON report; `--config` selects an explicit training-style budget:

```sh
.venv/bin/python -m hnefatafl.cli benchmark --output data/benchmark.json
.venv/bin/python -m hnefatafl.cli benchmark --config configs/train-smoke.json
```

With no config it uses Brandubh, a 4-channel/0-block network, four eight-ply games,
four simulations per move, seed 17 and two CPU threads. It always compares CPU and,
if available, CUDA; the supplied training `device` is not a device filter. MPS is
supported by training but is not a benchmark scenario. The benchmark forces float32
and a 300-second search ceiling, warms the model, excludes initialization/warm-up
from timing and synchronizes CUDA at scenario boundaries. It neither trains an
optimizer nor registers a model, and rejects a `resume` input.

Before comparing speedup, inspect actual positions, simulations, neural evaluations,
`requested_simulations` and `work_matches_sequential`. A terminal winning root,
deadline or different game trajectory can change work. Compare modes on the same
device and repeat runs with recorded hardware, driver, PyTorch version, seeds and
budgets. Reproducibility within a mode does not imply bitwise equality across modes
or hardware.

The [v0.17.0 measurements](releases/v0.17.0.md#performance-evidence) completed all
128 simulations/32 positions in each tiny CPU scenario. Sequential/batched speedups
were **0.98×** (4 channels/0 blocks) and **0.65×** (32 channels/2 blocks). Batching
reduced forward-pass counts but did **not** demonstrate a CPU speedup. CUDA was
skipped; GPU speedup and full-game throughput remain unverified. These historical
numbers are evidence for those exact runs, not hardware-independent expectations.

## Troubleshooting performance

- If auto remains sequential, inspect `devices` and the effective worker's PyTorch
  installation. CPU and MPS intentionally select sequential execution.
- If batches stay small, compare games/concurrency with the inference limit and
  inspect actual batch sizes. Dependent simulations cannot be arbitrarily packed.
- If throughput rises while completion falls, inspect `truncated_games` and the
  move cap before interpreting the result. Short unfinished games are not wins.
- If CUDA runs out of memory, reduce network size, inference/optimization batch
  sizes or independent worker slots. A slot count is not automatic GPU allocation.
- Keep default math threads at two initially. More CPU threads or worker slots can
  oversubscribe the host. Measure a small representative budget before scaling.


## Training evaluation cost

Training runs one selected evaluator, rather than baseline plus previous-best.
Use `evaluation_frequency` independently of checkpoint cadence and inspect
`evaluation_seconds` and each evaluation record's `duration`. Jarl uses the real
alpha-beta search and its own depth/deadline; it may cost more than neural play.
Champion uses equal neural budgets on both models, but remains sequential.
The self-play scheduler assumes one model: reusing it unchanged for two opponents
would mix model inference. Cross-model batching is deferred pending a separate
correctness and measured performance comparison. No evaluation batching speedup
or RTX 3060 throughput gain is claimed by this release.

CLI logs project bounded incremental summaries while persistent JSON retains
complete history. This removes repeated history/game serialization from worker
logs; it does not remove history from checkpoint or progress APIs. Wall-clock
phase times remain distinct from overlapping activity counters, with no extra
CUDA synchronization added to ordinary inference.

## Shield maiden benchmark

The independent [Shield maiden protocol and measured results](SHIELD_MAIDEN.md)
compare matched-position decision latency, evaluated moves per second, balanced
playing strength, whole-game throughput and actual single-opponent neural evaluation.
It uses no training updates and never substitutes ply caps for rule draws.

```sh
.venv/bin/python -m hnefatafl.cli benchmark-heuristics \
  --config configs/benchmarks/shield-maiden.json --output work/shield-maiden.json
```

The default benchmark optionally imports PyTorch solely to time the existing
training evaluator; Shield maiden gameplay itself remains tensor independent.

## Optional OpenVINO inference

See [OpenVINO inference and full-training benchmarks](OPENVINO.md) for opt-in setup, defaults, checkpoint portability and measured limitations.
