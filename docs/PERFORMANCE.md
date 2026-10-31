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

Pure-engine existence queries avoid building complete move lists during terminal
adjudication; immutable board geometry is cached, and encirclement stops once an
accessible defender disproves enclosure. Move order and adjudication remain the
same. See [engine and self-play measurements](benchmarks/training-engine-fast-paths.md).

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

Optional `search_workers` (0 disables; 1–32 processes) moves independent search
into persistent spawned CPU actors. CPU inference uses read-only actor snapshots;
CUDA/OpenVINO inference stays in the parent and batches pending leaves by model.
Each actor uses one math thread; GPU job slots remain separate. More processes
consume more RAM and can be slower. Pools span a training run; standalone calls
pay startup. Parallel traversal wall contribution is reported as unavailable
because actor activity overlaps. See [isolated worker measurements](benchmarks/training-parallel-search.md).

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

Three commands produce the checked-in evidence. `benchmark` compares self-play
scheduling, `benchmark-training` compares complete CPU/OpenVINO training iterations
and `benchmark-symmetry` measures D4 augmentation cost, geometric consistency,
held-out learning and paired strength:

```sh
.venv/bin/python -m hnefatafl.cli benchmark-symmetry \
  --config docs/benchmarks/symmetry-phase3-cpu-config.json --output data/symmetry.json
```

Compare modes without starting a training job or registering a model. `--output`
also saves the JSON report; `--config` selects an explicit training-style budget:

```sh
.venv/bin/python -m hnefatafl.cli benchmark --output data/benchmark.json
.venv/bin/python -m hnefatafl.cli benchmark --config configs/train-smoke.json
```

With no config it performs three repetitions of cache off/exact in both sequential
and batched modes. Cache-mode order alternates between repetitions. It uses Brandubh, a 4-channel/0-block network, four eight-ply games,
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

The [0.17.0 measurements](releases/0.17.0.md#performance-evidence) completed all
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
Champion uses equal neural budgets on both models. `evaluation_mode` selects
`auto`, `sequential` or `batched`; auto batches CUDA and keeps PyTorch CPU/MPS
sequential. `evaluation_concurrency` (default 16) and `evaluation_batch_size`
(default 32) are independent of self-play settings. Each model has its own queue
and cache. Evaluation stays FP32. `evaluation_inference_backend: training` opts
into a fresh post-update OpenVINO graph when that backend is selected; default
`pytorch` preserves the historical numerical backend. Alternate backend results
use a separate benchmark identity. See [isolated evaluation evidence](benchmarks/training-evaluation-batching.md).
Workers advertise `evaluation_batching`; legacy workers retain the sequential
default and do not receive unknown execution keys, while explicit new modes
require that capability. Resume downloads apply the same filtering.
Queue waiting counts against wall deadlines, so compare completed search work
and cutoff counts on the target worker before choosing concurrency.

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


## Exact-input cache and symmetry foundation

`inference_cache_mode` accepts `off` (default) or `exact`. Capacity defaults to
4096 entries with a validated range of 1–65536. Off forwards each active neural
request to inference; exact enables both LRU reuse and within-batch deduplication
through the shared inference pipeline. Cache lookup applies to nonterminal neural inputs
only: ruleset, board contents **in the original orientation**, and side to move.
The current encoder has no history plane, so different nonterminal histories can
share a pure evaluation; each engine/search state keeps its own repetition history
and terminal handling. Legal move support depends only on that board/turn/ruleset
after terminal handling. Policies are normalized over that legal support.

Each self-play generation owns a cache. Training discards it before optimization;
backend refresh has an explicit generation, including PyTorch's in-place weight
updates. Reusing the public cache also checks tensor version counters, parameter
identities, device, dtype and precision settings. External writes bypassing tensor
version counters (for example `.data`) require an explicit `cache.clear()`; managed
training does not use such writes between cached requests. Cache only evaluation
mode models. Compact immutable legal probabilities/value live on the host, never
full action logits, GPU tensors, terminal adjudications or search noise. Every
consumer receives its own mutable dictionary. Memory depends on legal-policy size;
capacity is an entry bound, not a byte guarantee. No per-request logging or new
CUDA synchronization is used.

The independent pure-Python `symmetry.py` supports identity, clockwise 90/180/270°
rotations and left/right reflection followed by each rotation. Cached coordinate
permutations transform indices, moves, boards and complete ordered repetition
histories; inverse transforms preserve side identities and terminal metadata.
Canonical board ties use enum order; full state ties use globally transformed
history before enum order. `BoardKey` is geometry only; `StateKey` retains ruleset,
board, turn, ply, winner, reason and full ordered history. Neither is used by search
or the inference cache in this phase. Neural predictions need not be invariant
under D4, so symmetry-equivalent inputs are deliberately never merged.

### Cache metrics

All new fields are additive in existing performance JSON and the workshop's
Performance view. Terminal requests are excluded from neural request counts.

| Field | Meaning |
| --- | --- |
| `inference_requests` | Logical active neural requests, before deduplication |
| `neural_evaluations` | Actual positions evaluated by the network, preserving historical meaning |
| `inference_cache_hits`, `inference_cache_misses` | One lookup per distinct exact input per batch; off counts every active request as a miss |
| `inference_batch_duplicates` | Additional requests for the same exact input within a batch, including duplicates of hits |
| `inference_cache_hit_rate` | hits / (hits + misses), zero when no lookups |
| `inference_cache_entries`, `inference_cache_peak_entries` | Current and peak resident entry counts |
| `inference_cache_lookup_seconds` | Host time for generation validation, input keys, LRU lookups and duplicate grouping |

For completed requests, `requests = hits + misses + duplicates`; misses equal
actual model evaluations. Duplicate requests count once as a lookup, avoiding
inflated hit rates. `inference_batches` and observed batch sizes describe actual
forwards after deduplication. An all-hit batch adds no forward/timing batch.
Existing inference host/CPU timers measure encoding, forward and policy decoding;
lookup time is separately exposed and remains part of total self-play wall/CPU
time. CUDA activity remains benchmark-only. Search traversal's residual timing
includes cache work on the sequential reference path; use total wall time for
end-to-end comparisons and do not add overlapping timers.

### Reproducible comparisons

The existing `benchmark` command accepts benchmark-only `repetitions` (default 3,
1–20), `cache_modes` (`["off", "exact"]`, or `["off"]` for the old comparison) and
`inference_workload_batches` (default 16, 1–256). These are not training settings.
Self-play rows retain mode/device and add cache mode, repetition, total process CPU
time, evaluations eliminated, speedup against the same mode with cache off, and a
seeded trajectory/search-target digest comparison. Compare actual simulation
counts and deadline cutoffs. Float32 legal probabilities/value parity tolerances
are 1e-6 absolute / 1e-5 relative for PyTorch cache tests and 3e-5 / 1e-4 for
OpenVINO; changing batch shape can change kernel rounding.

`inference_workloads` isolates repeated identical boards (duplicate-heavy) and
unique exact inputs (unique), including lookup overhead. Generation of the unique
pool is excluded. Identical seeded weights, rulesets, seeds, budgets and precision
are reused. Warm-up/model initialization is excluded; CUDA, when available, is
synchronized at scenario boundaries with activity and allocator peaks recorded.
CPU-only runs cannot establish CUDA speedup; smaller deduplicated batches may
reduce GPU efficiency. These are self-play and inference comparisons, not evidence
of faster complete training or stronger models.

Runnable five-repetition budgets live in
[exact-small.json](../configs/benchmarks/exact-small.json) and
[exact-network.json](../configs/benchmarks/exact-network.json). Change only `ruleset`
to repeat the small budget on Tablut/Copenhagen. Run in isolation from tests or
other training jobs, then compare medians and variation. The 0.24.0 measured
results and raw-report provenance are recorded below and in the release notes.
### Measured CPU results (2026-10-24)

AMD EPYC 9V74, Python 3.12.14, PyTorch 2.14.1+cpu, two math threads,
seed 17. Five repetitions per off/exact/mode/budget combination, alternating cache
order, run after test services stopped. Raw wall/CPU timing, games/hour, simulations/s,
batch distributions, lookup time, occupancy and work counters are retained in the
[complete JSON report](benchmarks/exact-input-cpu-2026-10-24.json).

| Budget | Schedule | Median off → exact (s) | Exact speedup (ratio of medians) | Evaluations off → exact | Completed simulations |
| --- | --- | --- | --- | --- | --- |
| brandubh-32x2 | sequential | 2.470 → 2.109 | 1.171× | 2890 → 1835 | 1936 |
| brandubh-32x2 | batched | 1.075 → 1.118 | 0.962× | 2890 → 1835 | 1936 |
| brandubh-small | sequential | 0.491 → 0.533 | 0.921× | 1061 → 666 | 688 |
| brandubh-small | batched | 0.349 → 0.343 | 1.018× | 1061 → 666 | 688 |
| copenhagen-small | sequential | 2.348 → 2.398 | 0.979× | 1166 → 758 | 768 |
| copenhagen-small | batched | 2.100 → 2.174 | 0.966× | 1166 → 758 | 768 |
| tablut-small | sequential | 0.978 → 0.998 | 0.980× | 1180 → 751 | 768 |
| tablut-small | batched | 0.803 → 0.848 | 0.947× | 1180 → 751 | 768 |

Small budgets: 4 channels/0 blocks, 8 games × at most 12 plies, 8 visits per move.
The Brandubh 32×2 budget: 32 channels/2 blocks, 8 games × at most 16 plies,
16 visits per move. Concurrency and inference limit are eight, FP32, capacity 4096.
Actual simulation counts match with caching off/exact, including existing winning-root
shortcuts. All 80 self-play runs preserved the seeded trajectory/policy-target digest
within their execution mode, and none hit a search deadline. Counts and timing
ranges are in the raw report; ratios of medians here differ from median paired
speedups in the JSON summary.

Evaluations fell about 35–37%, but wall time did not improve consistently.
Keep caching off by default; select exact only after measuring a representative
network/budget. These short, truncated-game comparisons are not complete training
throughput or model-strength evidence.

| Controlled inference workload (256 requests, batches of eight) | Budget | Median off → exact (ms) | Evaluations off → exact | Median exact lookup (ms) |
| --- | --- | --- | --- | --- |
| duplicate-heavy | brandubh-small | 41.85 → 8.74 | 256 → 1 | 1.58 |
| unique | brandubh-small | 40.11 → 45.06 | 256 → 256 | 2.08 |
| duplicate-heavy | brandubh-32x2 | 64.35 → 10.22 | 256 → 1 | 2.55 |
| unique | brandubh-32x2 | 61.99 → 67.89 | 256 → 256 | 3.34 |

The duplicate-heavy test intentionally repeats one board throughout and is an
artificial upper-benefit workload. The unique pool has no exact reuse, exposing
cache/key/copy overhead. Smaller effective batches and Python search processing
explain why an inference-only gain need not carry through to self-play.

The pre-change 0.23.0 sequential baseline (three repetitions) was 0.501 s; final cache-off median was 0.491 s (-2.0% runtime).
The pre-change 0.23.0 batched baseline (three repetitions) was 0.351 s; final cache-off median was 0.349 s (-0.5% runtime).
Both used 1,061 evaluations and 688 actual simulations. Treat these separate short
runs as a regression check with timing variation, not a universal speed estimate.
No CUDA, MPS or Intel GPU timing/memory claim is possible on this host. OpenVINO
CPU cache/snapshot correctness passed real inference and full training/resume
tests; its CPU validation does not establish an Intel GPU speedup.

## Phase 2: retaining MCTS predictions

Previously a newly created neural child yielded for its value, discarded the
policy and yielded again when a later simulation first expanded it. Nodes now
own the legal priors and value from that first prediction. Subsequent traversal
uses those priors while visit counts and backed-up values continue to change on
every completed simulation. A smaller request count does not reduce the configured
simulation budget. No final move, cross-game tree, transposition or symmetry
prediction is reused.

The existing PUCT formula, first-tie order, weighted widening sampler and root
noise remain intact. The parent's square root is calculated once per selection;
`children**2 <= visits` replaces an equivalent square-root widening comparison.
Root tactical checks remain rules-aware and now check cancellation between moves.
Inference, terminal adjudication and repetition histories remain independent per
search. Production counters add no per-simulation logging or CUDA synchronization.

| Field | Meaning |
| --- | --- |
| `mcts_neural_requests` | Neural requests actually yielded by MCTS, including roots; excludes unfinished-game bootstrap |
| `mcts_node_prediction_reuses` | Requests avoided by using a node's saved prediction: delayed policy expansion or a revisited nonterminal leaf |

These fields appear in self-play result/performance JSON and the workshop MCTS
group; older workers display missing values as a dash. `inference_requests` counts
requests arriving at the inference pipeline, including bootstrap. Cache hits,
within-batch duplicates and actual `neural_evaluations` retain their existing
meanings. Do not add node reuse to cache hits: reuse never reaches the cache.

Trees own legal dictionaries at expanded nodes and packed host arrays at deferred
leaves, with uint16 action IDs and exact double probabilities. They remain bounded
by the search budget. Leaf policies trade host memory for avoided legal
masking/encoding/inference work. A tree is released at move completion or context
closure. Models and backends must remain frozen during a pending search; managed
training finishes all searches before refreshing weights and uses a new cache
generation afterward. Existing checkpoints, optimizer/replay formats and remote
worker protocol are unchanged.

### Fixed-model benchmark options

The existing `benchmark --config FILE --output FILE` command additionally accepts:

| Benchmark-only option | Default and purpose |
| --- | --- |
| `checkpoint` | Omitted: deterministic seeded weights; otherwise validated `last.pt` or inference export, loaded without restoring training |
| `devices` | `["cpu", "cuda"]`; choose either/both; unavailable CUDA is reported as skipped |
| `self_play_modes` | `["sequential", "batched"]`; choose either/both |
| `cache_modes` | `["off", "exact"]`; choose either/both, in either order |
| `benchmark_mixed_precision` | `false`; enable CUDA autocast in both self-play modes; CPU stays float32 |
| `include_inference_workloads` | `true`; set false to time only actual MCTS self-play |

Architecture is inferred from a checkpoint; explicit conflicting architecture
settings are rejected. The source checkpoint is never saved or registered, and
CLI output may not overwrite it (including aliases). Each report records a
checkpoint SHA-256 when supplied and a tensor-weight fingerprint in all cases.
Device/backend, CPU/Python/PyTorch metadata, trajectory hashes and raw per-run
counters accompany median summaries. Without a sequential/off reference, its
comparison fields are null. Execution and cache order alternate across repetitions.
The old command/defaults remain valid; ordinary training's `mixed_precision`
setting does not override the benchmark's explicit precision control.

`process_peak_rss_bytes` is the process-lifetime high-water mark, including the
model/runtime, warm-up and retained examples. It cannot be reset between scenarios:
use fresh processes and alternate scenario order for memory comparisons. It is
not the incremental size of a search tree. CUDA allocator metrics remain separate.
See the [Phase 2 evidence](benchmarks/mcts-phase2.md) for measured workloads,
profiling boundaries, limitations and reproducible configurations.

For OpenVINO fixed-model measurements, set `devices: ["cpu"]`,
`inference_backend: "openvino"` and an explicit `inference_device` such as `"CPU"`.
The graph is compiled/refreshed once per device and warmed before timing; all
scenarios use that frozen snapshot. Conversion time is excluded from this
self-play benchmark. Use `benchmark-training` to measure full refresh/optimizer
iteration costs instead.

## Phase 3: training symmetry augmentation

`training_symmetry_mode: "random_d4"` transforms each replay example presented to
the optimizer (see [Training](TRAINING.md#training-symmetry-augmentation)). It is a
learning-quality feature, not a speed feature, and it is separate from the Phase 1
exact-input cache and Phase 2 node-level prediction reuse: those avoid repeated
evaluation of the *same* orientation; augmentation changes training samples only and
never merges or shares predictions across orientations. A network trained with
augmentation is closer to, but not exactly, D4-equivariant.

| Field | Meaning |
| --- | --- |
| `training_exposures` | Examples presented to the optimizer this iteration (`epochs × replay`) |
| `augmentation_exposures` | Exposures that were transformed (0 when off) |
| `augmentation_identity` | Exposures that drew the identity transform |
| `augmentation_seconds` | Host time computing transformed inputs, actions and masks |
| `augmentation_optimization_share` | `augmentation_seconds / optimization_seconds` |
| `metrics.augmentation.transform_counts` | Eight per-transform counts in `Symmetry` enum order |

Original replay positions, games, simulations and neural evaluations keep their
existing meanings. Index tables are cached per board size (`[8, n²]` squares,
`[8, n⁴]` actions) and applied with vectorized gather/index operations before one
device transfer.

### Measured results (2026-10-25)

Full tables, environment and raw JSON: [symmetry-phase3.md](benchmarks/symmetry-phase3.md).
With matched replay, weights, batches and seed on tiny networks: measured augmentation
work is 1.4–1.9% of optimization time; end-to-end optimizer-time ratios (random_d4/off)
medians were 0.97–1.04 on CPU, 0.94–1.05 on CUDA FP32 and 1.03–1.14 on CUDA mixed
precision (RTX 3060), with per-repetition scatter spanning roughly ±20% (CPU timings
shared the host with another training job). Memory was unchanged. Mean policy
total-variation distance between a position and its mapped-back rotations fell from
0.54/0.47/0.42 to 0.12/0.09/0.07 (Brandubh/Tablut/Copenhagen) and held-out policy loss
fell in 9/9 replicates, but top-move agreement remained far from exact and a 32-game
paired smoke match per ruleset showed no distinguishable strength difference. CUDA
FP32 and mixed-precision optimization, training and resume were executed on the RTX
3060; MPS and OpenVINO GPU were not exercised for this feature (OpenVINO inference
does not touch the optimizer path).

### Recommended experiment

To decide whether `random_d4` should become a default, start from one shared full
checkpoint (`last.pt`) per ruleset and resume two new jobs with equal iterations,
games, search budgets, seed-derived settings and replay capacity, differing only in
`training_symmetry_mode`. Repeat over at least five seeds/starting checkpoints and
train long enough that the replay buffer is a meaningful fraction of its capacity.
Evaluate with the champion/paired evaluation (or tournaments) with at least 200
side-swapped games per pair at equal search budgets, reporting wins, losses, rule
draws and unfinished games with Wilson or paired intervals, plus held-out loss on
positions from games not used for optimization. Do not infer strength from loss or
symmetry consistency alone.


## Phase 4: alpha-beta symmetry search

The production Jarl alpha-beta search now owns a bounded, depth-aware
transposition table with exact/lower/upper bounds and improved move ordering.
Optional D4-canonical transpositions and full-state symmetry move pruning are
available for heuristic-backed search only; neural value heads are not assumed
equivariant. The features are independently toggleable through the internal
`search` configuration (see [AI opponents](AI.md#search-optimization-configuration)).

Reproduce the ablation and correctness benchmark with:

```sh
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-symmetry.json \
  --output work/alpha-beta-symmetry.json
```

The benchmark runs the real `choose_move` path on a deterministic corpus of
openings, seeded midgames, symmetric/asymmetric positions, immediate wins, king
threats, restricted-square tactics, Copenhagen shieldwalls/encirclement/forts,
repetition-sensitive positions and long histories. It compares six feature
ablations (legacy reference, ordinary table, ordering, canonical table, symmetry
pruning, all). Fixed-depth scenarios must match the legacy minimax value; a
separate independent exhaustive minimax oracle validates feasible positions.
Fixed-time scenarios use equal wall-clock budgets and report the last completed
depth. Repetitions alternate scenario order, and median/min/max variability is
reported. Memory is traced once per scenario outside the timed repetitions.

Measured CPU results and the raw report are kept in
[Phase 4 alpha-beta evidence](benchmarks/alpha-beta-symmetry.md). On WSL2
Intel i7-8750H, two math threads, the fixed-depth median wall time by ruleset was:

| Ruleset (depth) | legacy | transposition | ordering | symmetry_pruning | all |
| --- | ---: | ---: | ---: | ---: | ---: |
| Brandubh (3) | 2.507 s | 2.472 s | 2.505 s | 0.603 s | 0.608 s |
| Tablut (2) | 0.179 s | 0.150 s | 0.196 s | 0.125 s | 0.115 s |
| Copenhagen (2) | 0.581 s | 0.628 s | 0.568 s | 0.484 s | 0.428 s |

All six ablations matched the legacy minimax value on every fixed-depth position,
and an independent exhaustive oracle found an optimal legal move for all 37
feasible positions. The symmetry scenarios are much faster because symmetric
positions dominate the corpus total; per-position analysis shows they are pure
wins on symmetric positions (0.12–0.65×) but cost 7–25% on generic asymmetric
midgames. Because canonicalization cost grows with board size and history length,
the symmetry features remain optional defaults; the ordinary table and move
ordering are the safe default and enable them only after measuring a
representative corpus on the target host. The ordinary table was neutral
(median ratio 0.96–1.03) and a 1.0-second depth-5 probe showed move ordering
recovering a lost ply on Tablut and full symmetry reaching one ply deeper on the
symmetric openings.

## Move ordering and symmetry-pruning refinements

Phase 4 left three follow-ups: history-based move ordering was skipped whenever no
principal-variation, transposition or killer move existed; the `symmetry_hits`
counter incremented on any canonical-table hit rather than genuine
cross-orientation reuse; and symmetry-equivalent moves were pruned only after
`_ordered` had already built and statically evaluated every successor.

Raw numbers and the before/after comparison are kept in
[alpha-beta control refinements evidence](benchmarks/alpha-beta-controls.md).

### What changed

The production search now applies one deterministic ordering priority (principal
variation, transposition move, killers, history, stable static order), consults
history even without a preferred move and skips sorting when every history score is
zero. Symmetry pruning computes the full-state move orbits first and builds
successors only for the retained representatives, so discarded moves cost no
`apply_move` or static evaluation. The table entry records its canonical
orientation; `transposition_cross_orientation_hits` counts probes whose stored
orientation differs from the query's canonical orientation, and
`transposition_successful_lookups` records any probe that found an entry.

Reproduce the same ablation and compare against the Phase 4 baseline with:

```sh
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-symmetry.json \
  --output work/alpha-beta-controls.json
```

### Measured results

On the same WSL2 host and two math threads, two repetitions, every fixed-depth
ablation still matched the legacy minimax value (zero mismatches) and the
independent oracle found an optimal legal move for all 37 feasible positions. The
fixed-depth median wall time by ruleset was:

| Ruleset (depth) | legacy | transposition | ordering | symmetry_pruning | all |
| --- | ---: | ---: | ---: | ---: | ---: |
| Brandubh (3) | 1.448 s | 1.496 s | 1.507 s | 0.351 s | 0.377 s |
| Tablut (2) | 0.082 s | 0.077 s | 0.076 s | 0.047 s | 0.050 s |
| Copenhagen (2) | 0.463 s | 0.478 s | 0.486 s | 0.321 s | 0.319 s |

The symmetry scenarios remain the clear win (`all` is 0.20–0.70× of legacy); the
ordinary table and ordering are timing-neutral within run-to-run noise. A
same-host before/after comparison against the pre-refinement code reproduced the
same minimax and oracle results and moved the symmetry scenarios by less than 7%
in either direction, so the structural pruning change is reported as correctness
and work-avoidance rather than a wall-clock claim.

The cross-orientation counter is meaningful: with canonical transpositions alone,
9 of 37 corpus positions recorded genuine cross-orientation reuse (up to 101
reuses on one position), while the full-symmetry scenario recorded none because
move pruning had already collapsed each orbit before a differently oriented probe
could occur. Isolating history ordering by passing an empty history dictionary to
the same priority function produced mixed per-position node counts at depth 3–4
(2 positions improved, 6 worsened, no completed-depth or value change), which
matches the intended priority rather than a universal speedup claim. The value of
the ordering change is that history is always consulted in the documented priority
order and the ordering is deterministic; the unit tests assert the priority
directly.

### Equal-time strength, memory bound and deadline

The shipped `configs/alpha-beta-search-comparison.json` (legacy vs full symmetry,
brandubh, equal 2-second budgets, 20 games, seed 20261026) was played with
`scripts/alpha_beta_strength.py`. Legacy scored 7W/3D/10L (0.425, 95% interval
[0.220, 0.630], mean completed depth 2.46); full symmetry scored 10W/3D/7L (0.575,
mean depth 2.29). The interval contains 0.5, so **strength did not measurably
improve**, and full symmetry reached a slightly shallower mean depth because
canonicalization consumes part of the fixed budget. Speed is not reported as
strength.

The transposition-table capacity ceiling is reduced from 5,000,000 to 500,000. A
repetition-aware key grows with history (measured 457 bytes/entry with no history
to ~2,057 bytes at 200 entries), so the ceiling bounds the worst case; a synthetic
full 500,000-entry table peaks at 228 MB (no history) to 1,028 MB (200 entries).
On long games the search stores very few entries and the actual `tracemalloc` peak
stays under 7 MB (brandubh 200-ply 6.3 MB, tablut 300-ply 5.7 MB, copenhagen
400-ply 4.0 MB). The bound is a safety valve rather than a practical limit.

The monotonic deadline is now observed inside move ordering, canonicalization and
symmetry pruning through a cooperative check callback. On a synthetic symmetric
copenhagen position with a 400-entry history, a 1 ms budget returns in ~1.2 ms
instead of overrunning by a full ~45 ms stabilizer. Raw numbers are in the
[control-refinement evidence](benchmarks/alpha-beta-controls.md).

## Alpha-beta profiling and root-window pruning

A follow-up investigation reproduced the reported bottlenecks. The full report is
in [alpha-beta profiling and comparison](benchmarks/alpha-beta-profiling.md).
The two findings that changed the code are:

- The root passed a full `(-inf, +inf)` window to every child, so the widest part
  of the tree was a minimax fan-out and move ordering could not prune there. The
  optimized search now narrows the root window with the best value so far. An
  isolated A/B on the 37-position corpus cut median interior nodes 1.9× and
  median leaf evaluations 7.3× (median wall time 1.3×), with identical values;
  reproduce it with `configs/benchmarks/alpha-beta-root-window.json`.
- The ordinary transposition key was the full ordered history. It is now a
  compact, order-independent repetition signature. The key is correct and does
  find entries, but every hit was depth-incompatible under iterative deepening
  (zero exact hits, zero table cutoffs), so the table remains compatibility
  overhead rather than a measured speedup.

Move ordering was benchmarked as seven isolated strategies. The principal
variation is the only priority that measurably reduces successors; killer,
history and transposition-move priorities were indistinguishable from noise, and
the sort itself costs ~3 ms. Eager successor construction in `_ordered`
dominates the remaining wall time and is shared with the legacy search. D4
symmetry is verified value-safe but helps only genuinely symmetric positions, so
the symmetry features stay optional and off by default. The legacy search stays
available as the historical reference.
