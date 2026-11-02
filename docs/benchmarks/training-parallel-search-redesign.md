# Parallel search redesign

The first `search_workers` implementation (PR 39) bounded effective game concurrency
by the number of worker processes, which collapsed GPU batch occupancy. This report
covers the redesigned pool, corrected CPU and GPU measurements, and the work-hash
investigation. The [first-implementation report](training-parallel-search.md) is
retained unchanged as historical evidence.

## Scheduling model

`hnefatafl/cooperative.py` owns one scheduler for every path: in-process self-play,
in-process evaluation and each spawned CPU actor. A game is a generator that yields
`(model identity, state)` and receives exactly its own evaluation, so no simulation
advances on another game's inference.

`search_workers` sizes the process pool and never bounds concurrency.
`self_play_concurrency` and `evaluation_concurrency` bound active games across the
whole pool, and each worker receives a static round-robin share of the game indices
with an active window of `ceil(concurrency / workers)`. Static assignment is what
makes batch composition, and therefore the work fingerprint, reproducible; the first
implementation assigned each index to whichever worker happened to be free.

CPU models are bound as frozen read-only snapshots in every actor, and each actor
evaluates its own leaves in one batched forward pass with a single math thread.
CUDA/OpenVINO models, tensors, streams and caches stay in the parent. The parent
collects the outstanding requests of the whole pool, merges them into as few forward
passes as the batch limit allows, and routes every result back by explicit request
token. It waits for a sibling actor only inside a bounded merge window (8 ms by
default) and stops the moment that sibling delivers, so the window bounds merging
rather than delaying a batch to fill it.

Ordered replay stays bounded: with `retain_examples=False` the pool hands each worker
one slice of its share at a time and only grants the next slice while
`granted - committed < 2 * concurrency`, matching the in-process window. Results are
committed in game-index order and a slow early game cannot retain an iteration of
examples.

## Root cause of the first implementation's GPU regression

`parallel_search.py` set effective concurrency to
`min(len(processes), games, self_play_concurrency)`. Each actor ran exactly one game
and blocked on its own inference result, so two workers allowed at most two
outstanding requests even with 64 configured games. The parent therefore executed
133,613 forward passes of average size 1.16 instead of 7,477 of average size 21.09,
and median runtime rose from 101.19 s to 1,188.29 s — about 11.7x. The fix is the
scheduling model above: many game contexts per actor, batched leaves per actor, and a
parent that merges the requests of the whole pool.

## Work-hash investigation

The baseline produced one work hash across repeated runs; the first implementation
produced a different hash each time. The cause is scheduling-dependent batch shape
feeding numerical inference, not result routing or RNG:

- The same positions evaluated at batch sizes 1–64 on this network differ by at most
  `6e-8` in policy and `1.5e-8` in value. That is invisible on its own, but PUCT
  selection and the sampled move consume thousands of sequential decisions per game,
  so a `1e-8` difference eventually selects a different child and the trajectory
  diverges.
- Identical work measured with different worker counts and merge windows produces
  different, internally consistent fingerprints. The 16-game workload in the tuning
  table below is insensitive (one hash for every arm) because it has too few
  sequential decisions to amplify the difference; the 64-game workload is not.
- The redesigned pool still shows this residue on GPU. In the alternating-pair GPU
  protocol the 0-worker arm produced a single fingerprint for all three pairs while
  each worker arm produced three different ones; two samples of one fixed
  configuration gave `3b16aefc…` and `366def9b…` at two workers and `03e08b60…` and
  `3876d1c5…` at four. The first implementation's divergence is the same effect with
  a much larger batch-shape spread.
- Routing is separately established. `plan_batches` is unit-tested against
  out-of-order and oversized requests, and end-to-end results at four workers match an
  in-process reference that uses the same deliberately state-dependent backend.
- Per-game RNG streams are preserved: the recorded policy targets of every game
  replay exactly into legal moves under that game's own seed.
- No budget is altered. `search_deadline_cutoffs` is 0, and simulation and evaluation
  counts match the reference.

On CPU the redesign is exact. Three independent alternating pairs of four worker
counts across three rulesets and both phases each produced a single fingerprint, and
every one of those fingerprints equals the `main` baseline's.

## CPU measurements

Small workloads: 4 self-play games (8 plies, 32 visits) and 8 Shieldbearer evaluation
games (16 plies, 32 visits), one math thread per process, `main` extracted at
`84c7290`. Median of three independent, alternating-order pairs; each process discards
one warm-up sample. Speedup is the `main` median divided by the arm median.

| Phase | Ruleset | `main` | workers 0 | workers 1 | workers 2 | workers 4 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Evaluation | Brandubh | 1.4717 s | 0.92x | 1.89x | 2.26x | 2.44x |
| Evaluation | Tablut | 3.1240 s | 0.97x | 1.59x | 1.85x | 2.12x |
| Evaluation | Copenhagen | 9.7563 s | 0.98x | 1.46x | 2.18x | 2.66x |
| Self-play | Brandubh | 0.7936 s | 1.02x | 0.96x | 1.21x | 1.33x |
| Self-play | Tablut | 1.4738 s | 0.96x | 1.02x | 1.25x | 1.46x |
| Self-play | Copenhagen | 3.3120 s | 1.04x | 1.06x | 1.46x | 1.58x |

`workers 0` on this branch reproduces `main` within 4% and with an identical work
fingerprint, so the refactor that introduced the shared scheduler did not change the
default path. The first implementation reported evaluation 1.62x/1.29x/1.72x and
self-play 1.09x/0.91x/1.21x for Brandubh/Tablut/Copenhagen at two workers; the
redesign reports 2.26x/1.85x/2.18x and 1.21x/1.25x/1.46x.

Larger workloads (12 games, 16 plies, 48 visits, Brandubh, same protocol):

| Phase | `main` | workers 0 | workers 2 |
| --- | ---: | ---: | ---: |
| Evaluation | 2.8399 s | 0.93x | 2.14x |
| Self-play | 4.8604 s | 1.06x | 1.27x |

Every arm of every scenario — three rulesets, two phases, two workload sizes, four
worker counts, three pairs each — produced exactly one work fingerprint, and every one
of those equals the `main` baseline's. Raw samples:
[`training-parallel-search-redesign.json`](training-parallel-search-redesign.json).

Gains grow with worker count but not linearly: legal-move generation, traversal and
policy decoding are CPU work that the pool parallelises, while a single actor's
batched forward pass still runs on one thread. Cold startup is excluded by the
per-process warm-up, and a real training run reuses one pool across iterations and
phases.

## GPU measurements

RTX 3060 12 GB, WSL2, PyTorch 2.14.1+cu126, CUDA 12.6, FP32, 32x2 Brandubh network,
64 games, 64 configured concurrency, batch limit 64, 128 MCTS iterations, 150-ply
cap. Median of three independent, alternating-order pairs per arm; each process
discards one warm-up sample.

| Arm | Median seconds | Forward passes | Average batch | Simulations/s | Speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| `search_workers=0` | 87.05 | 7,477 | 21.09 | 1,803 | 1.00x |
| `search_workers=4` | 93.88 | 7,500 | 21.08 | 1,670 | 0.93x |
| `search_workers=8` | 93.34 | 7,487 | 21.07 | 1,681 | 0.93x |

Batch occupancy is preserved: 21.07–21.09 average over roughly 7,490 forward passes,
against the first implementation's 133,613 passes of average size 1.16. The 0-worker
arm produced one workload fingerprint for all three pairs; the worker arms produced a
different fingerprint per pair, which is the GPU numerical residue described above.
The remaining cost is the pool's own overhead: inter-process traffic, and the extra
traversal the actors account for (`cpu_search_seconds` 22–26 s against 16.6 s). On
this workload the worker arms are about 7% slower than the single-process scheduler,
against 11.7x slower before the redesign. Raw samples:
[`training-parallel-search-gpu.json`](training-parallel-search-gpu.json).

### Merge window

The window trades latency against the number of forward passes. On the same 64-game
workload:

| Workers | 1 ms | 4 ms | 8 ms | 20 ms |
| --- | --- | --- | --- | --- |
| 2 | 10,743 passes, avg 14.47 | 8,573, avg 18.59 | 7,540, avg 21.14 | – |
| 4 | 9,964 passes, avg 16.08 | 7,709, avg 20.68 | 7,527, avg 21.01 | 7,576, avg 20.87 |
| 8 | 7,638 passes, avg 20.65 | 7,523, avg 21.02 | 7,495, avg 21.04 | – |

Batch occupancy is the state-robust part of that sweep; its absolute timings were
collected in one uninterrupted block and are not comparable with the alternating-pair
medians above. Raw samples:
[`training-parallel-search-window.json`](training-parallel-search-window.json).

An 8 ms window reaches baseline occupancy at every worker count, while 1 ms leaves up
to a third of the batch empty at two and four workers and 20 ms only over-waits. At
eight workers the window no longer changes occupancy: three
alternating pairs at 1 ms measured a 91.16 s median against 93.34 s at 8 ms, inside
the observed spread. Sixteen-game GPU workloads are insensitive too, because their
forward passes are dominated by the drain tail where few games remain.

A GPU worker mode that stays slower than `search_workers=0` is not recommended, so
GPU training keeps `search_workers=0` unless traversal is the measured bottleneck, in
which case 4–8 workers keep batch occupancy but do not pay for themselves on this
host.

## Reproducing

```sh
mkdir -p work/main-source
git archive <main-head> hnefatafl | tar -x -C work/main-source

.venv/bin/python scripts/benchmark_parallel_search.py \
  --baseline-source work/main-source --scenarios selfplay evaluation \
  --rulesets brandubh tablut copenhagen --workers 0 1 2 4 --pairs 3 \
  --output docs/benchmarks/training-parallel-search-redesign.json

.venv-cu126/bin/python scripts/benchmark_parallel_search.py --device cuda \
  --python .venv-cu126/bin/python --baseline-source work/main-source \
  --scenarios gpu-selfplay --rulesets brandubh --workers 0 4 8 --pairs 3 \
  --skip-main --work work/parallel-search-gpu \
  --output docs/benchmarks/training-parallel-search-gpu.json
```

The runner shells out to `scripts/benchmark_training_optimization.py`, which owns the
workload budgets, worker count and merge-window overrides. Add `--reuse` to
re-aggregate existing samples, and `--flush-seconds` to sweep the merge window.

Divergence between two sources or two runs is localised with:

```sh
.venv/bin/python scripts/diagnose_parallel_search.py --source work/main-source \
  --workers 2 --repeat 3 --games 8 --max-game-plies 24 --mcts-iterations 32
```

It reports the first game and ply whose recorded example differs, the move each run
played there, and the per-run batch counters.

## Limitations

- GPU trajectories are not bit-reproducible across runs or worker counts: batch shape
  depends on when sibling actors deliver, and the resulting `1e-8` numerical
  difference can select a different move thousands of decisions later. CPU is exact.
  Use `search_workers=0` when a GPU run must be bit-identical.
- The measured host is one RTX 3060 and one 12-core WSL2 machine. Absolute times are
  workload- and host-specific; batch occupancy and the direction of the changes are
  the portable results. MPS was not measured.
- The 8 ms merge window is tuned on the 64-game workload. Smaller GPU workloads are
  dominated by their drain tail, where merging cannot help; `--flush-seconds` exposes
  the knob for other shapes.
- Pools span a training run but not a process: a resumed run pays startup again, and
  standalone calls include it in the first sample only after the discarded warm-up.
- `search_workers` remains opt-in. A GPU worker mode that stays slower than
  `search_workers=0` should not be used for GPU training.
