# Self-play and evaluation optimizations

Independent measurements of engine fast paths, batched evaluation, lazy Jarl construction and evaluation-progress reporting. These are throughput measurements, not playing-strength results.

<!-- Consolidated from training-engine-fast-paths.md -->

## Engine fast paths and self-play

Compared independently against main `5a343668411033ed3283ed51e01323454975723a`.
The [raw A/B samples](./raw/training/training-engine-fast-paths.json) retain source hashes,
environment, counters, configurations and work hashes. Three fresh-process pairs
alternate baseline/candidate order and discard a warm-up in each process. No
other tests or benchmarks ran during measurement. CPU FP32, one math thread.

The engine sample applies every legal move at four seeded positions, repeated
eight times. The self-play sample uses recommended networks, four concurrent
eight-ply games, 32 visits per move, seed 42 and a 300-second search ceiling.

| Ruleset | Successor-generation speedup | Self-play before/after median seconds | Self-play speedup |
| --- | ---: | ---: | ---: |
| Brandubh | 2.86x | 0.6778 / 0.6130 | 1.11x |
| Tablut | 5.27x | 0.9278 / 0.8560 | 1.08x |
| Copenhagen | 2.58x | 2.2463 / 1.7195 | 1.31x |

All successor-result and complete self-play input/policy/work hashes match the
baseline across repetitions. Self-play rates include inference and scheduling;
the larger microbenchmark ratios must not be presented as full-training gains.
Short timings have visible noise; these are bounded CPU diagnostics, not GPU or
playing-strength evidence. Unfinished games remain unfinished.

The same engine change measured end-to-end in GPU batched self-play is recorded in
[GPU measurements on the RTX 3060 host](../../training/../evaluation/benchmarks/inference/gpu.md):
the ratio of medians was 1.232× with matching work hashes, but one of the three
pairs showed no improvement, so the median paired gain (~1.056×) is the
conservative reading.

The change adds an allocation-free legal-move existence query, immutable cached
movement rays/neighbors/corners, integer-target validation, and an encirclement
early exit when one edge-accessible defender disproves total enclosure. Movement
order, restricted-square pass-through, captures, repetition and no-move outcomes
are unchanged. Independent tests compare legal ordering with a direct geometric
oracle and encirclement with defender-to-edge flood searches, including no-Move-
allocation existence checks. Existing engine, symmetry and MCTS tests pass.

Reproduce from the repository root:

```sh
mkdir -p work/engine-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/engine-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature engine --variant baseline --source work/engine-baseline --output work/engine-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature engine --variant candidate --output work/engine-after.json
```

Repeat with `--feature selfplay` and each `--ruleset`. For the collection protocol
use `--repeats 1` in three alternating fresh-process pairs. Caches warm before
measurement, contain immutable geometry only, and have bounded board-size scope.

<!-- Consolidated from training-evaluation-batching.md -->

## Batched training evaluation

Baseline: `5a343668411033ed3283ed51e01323454975723a` (0.33.0 main).
The [raw samples](./raw/training/training-evaluation-batching.json) retain source SHA-256 hashes,
environment, budgets, inference counters and outcome hashes. Three fresh-process
samples per mode and ruleset alternate baseline/candidate order; each process
discards one warm-up. No tests or other benchmarks ran concurrently.

CPU PyTorch FP32, one math thread, recommended network sizes, eight evaluation
games, 32 visits, 16-ply limit, seed 42 weights, 300-second search ceiling.
The candidate explicitly requests eight concurrent evaluation games and batches
up to eight. CPU auto remains sequential. Games hitting the cap stay unfinished.

| Ruleset | Sequential median seconds | Batched median seconds | Speedup |
| --- | ---: | ---: | ---: |
| Brandubh | 0.8822 | 0.6217 | 1.42x |
| Tablut | 2.3165 | 1.4828 | 1.56x |
| Copenhagen | 7.6043 | 5.6710 | 1.34x |

Recorded game-result hashes match across all repetitions and modes. These hashes
cover sides, seeds, winners, plies and reasons, not bitwise equality of every
intermediate prediction. Regression tests additionally compare paired results,
route distinguishable candidate/incumbent models, exercise cancellation and real
update/resume, and verify post-update backend refresh and cleanup. CUDA tests
skip explicitly on this CPU-only host. No GPU speedup or playing-strength gain is
established by these timings.

The same change measured on an RTX 3060 CUDA host is recorded in
[GPU measurements on the RTX 3060 host](../../training/../evaluation/benchmarks/inference/gpu.md).
Batched evaluation was 9.70× faster against Shieldbearer and 5.61× faster against
Champion, but the Champion work hashes differed between the two execution modes,
so equivalent Champion outcomes and promotion decisions are not established.

Reproduce from the repository root (use the candidate script for both sources):

```sh
mkdir -p work/evaluation-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/evaluation-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature evaluation --variant baseline --source work/evaluation-baseline --ruleset brandubh --output work/evaluation-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature evaluation --variant candidate --ruleset brandubh --output work/evaluation-after.json
```

Use `--repeats 1` in alternating fresh-process order three times to reproduce the
collection protocol; repeat for Tablut/Copenhagen. On a CUDA host add
`--device cuda`. Lower deadlines and full-length games need their own comparison:
interleaving changes queue wait and floating-point kernels can change close
decisions. Champion comparisons always retain separate model queues/caches and
ordered side-swapped pair results. This optimization does not overlap training
and evaluation or alter promotion thresholds.

<!-- Consolidated from training-lazy-jarl.md -->

## Lazy Jarl successor construction

Isolated comparison with main `5a343668411033ed3283ed51e01323454975723a`, without
the other training optimizations. The [raw report](./raw/training/training-lazy-jarl.json)
retains source hashes, actual search counters, sample ranges and work hashes.
Three alternating fresh-process pairs per ruleset/case discard one warm-up each.
No tests or other benchmarks ran concurrently. CPU, one math thread, FP32 model
inference, fixed seeds/weights and generous 300-second ceilings.

Fixed-position search uses four seeded opening/midgame positions, Brandubh depth
3 and Tablut/Copenhagen depth 2. Actual production training evaluation uses four
side-swapped four-ply games, eight candidate neural visits and Jarl depth 2;
recommended networks. Both evaluation variants collect identical diagnostics
through a matched observer, included in timing. Capped games remain unfinished.

| Ruleset | Search before/after median seconds | Search speedup | Evaluation before/after median seconds | Evaluation speedup |
| --- | ---: | ---: | ---: | ---: |
| Brandubh | 1.2923 / 0.6121 | 2.11x | 0.4528 / 0.1812 | 2.50x |
| Tablut | 1.4135 / 0.3594 | 3.93x | 2.8633 / 0.9056 | 3.16x |
| Copenhagen | 9.3687 / 0.8486 | 11.04x | 12.8811 / 2.1326 | 6.04x |

All work hashes match. Fixed-position hashes include values, completed depths and
chosen moves. Evaluation hashes additionally cover every decision's board, turn,
move, value and completed depth. Actual evaluation successor construction falls
7,842→1,579, 35,140→8,241 and 63,337→5,106 respectively; searched nodes and leaf
evaluations are unchanged. Every recorded Jarl interruption count is zero. These
gains avoid unvisited child construction/scoring; they do not reduce search depth,
simulations or game budgets. They are not full-training or GPU speedup claims.

The optimized interior search shuffles once up front, then freezes the same
TT/killer/history priority buckets as eager ordering. Only demanded groups build
and score states, keeping the same stable static order within each group.
Freezing is essential because deeper cutoffs mutate the shared history table
while the iterator is suspended. Iterator cleanup finalizes skipped-successor
counters on cutoff, cancellation and error. Root construction and the legacy
search stay unchanged. `_lazy_successors: false` is an internal diagnostic
ablation, not a new public `search` setting.

367 focused tests pass, including complete lazy/eager child ordering under all
seven ordering strategies, both heuristic modes and near/deep horizons; mutation
of history/RNG while suspended; identical fixed-depth moves/values/node counts;
existing independent minimax oracles, symmetry/history rules, legal deadline
fallback and cancellation. Fixed-time searches can complete more work and choose
different moves; confirm any strength effect separately. Bounded priority-group
storage replaces eagerly materialized unused children, but no memory reduction
measurement is claimed.

Reproduce from the repository root:

```sh
mkdir -p work/jarl-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/jarl-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature jarl --variant baseline --source work/jarl-baseline --output work/jarl-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature jarl --variant candidate --output work/jarl-after.json
```

Repeat with `--feature jarl-evaluation` and each ruleset. Use `--repeats 1` in
three alternating process pairs for the collection protocol. Risks are ordering,
RNG, tie and deadline regressions; exact eager-order tests and the retained eager
ablation are the reference for investigating them.

<!-- Consolidated from training-evaluation-progress.md -->

## Evaluation progress reporting

> This measures the progress-reporting fix on `perf/batch-evaluation-progress`. It
> is separate from the historical [parallel-search](./search-parallel.md)
> and [monitoring](./training-monitoring.md) reports, whose numbers are
> unchanged and are not restated here.

The [raw report](./raw/training/training-evaluation-progress.json) retains the per-arm samples,
latency samples, fixed workload and environment. All measurements are CPU-only
Brandubh with the recommended 32-channel/2-block network, exact-input cache off,
mixed precision off and one math thread per process.

### Root cause

`_ActorProgress` batched progress into messages with `PROGRESS_DELTAS = 64` and
`PROGRESS_INTERVAL_SECONDS = 0.25`, but the actor's `refill()` callback flushed the
accumulator after every cooperative scheduler step. Evaluation ticks once per
neural batch (`evaluation_plies=len(states)`), and the scheduler steps once per
batch, so every batch became its own IPC message. The batching bounds were
defeated: the measured message rate was 1.000 messages per inference batch at both
two and four workers.

### Fix

The refill callback no longer flushes every scheduler step. It only records the
active-game count; the first self-play refill announces the initial active contexts
before the first expensive move, and later refills leave the count to the next tick or
lifecycle boundary. Self-play ticks once per played move and keeps its per-move
cadence. Evaluation uses a separate policy: a nominal one-second reporting interval
with a 16,384-leaf work cap. This is not a strict global rate limit. Evaluation
flushes earlier when the work cap is reached, and immediately at game completion,
slice end, cancellation and failure, so several messages can legitimately arrive
within one second. The policy reduces redundant messages while keeping the final
delta exact. Trigger reasons, send time and parent handle time are reported as bounded
phase metrics.

### Champion evaluation A/B

Workload: 16 games, 128 visits, 24-ply cap, 16 concurrency, batch 16, fixed
candidate and Champion snapshots, one discarded warm-up per arm and three retained
samples. This is the task's workload with games/concurrency/batch scaled down 4x for
a 4-core CPU-only host; visits and network match. The full 64-game workload produced
~4.5x more batches than the scaled run, consistent with the previously observed
~69,500 messages across two phases (two phases of the scaled run would produce about
15,300 messages).

| Workers | Monitoring | Median seconds | Progress messages (samples) | Inference batches | Messages / batch |
| ---: | --- | ---: | --- | ---: | ---: |
| 2 | before | 16.873 | 7655 / 7655 / 7655 | 7651 | 1.001 |
| 2 | after | 17.557 | 39 / 60 / 45 | 7651 | 0.006 |
| 4 | before | 18.764 | 14324 / 14324 / 14324 | 14320 | 1.000 |
| 4 | after | 18.065 | 84 / 78 / 79 | 14320 | 0.006 |

Progress-message reduction is **170×** at two workers and **181×** at four workers.
Neural evaluations, inference forward passes and the evaluation work hash
(`7b1fe4eb…`) are identical in every arm, including monitoring on and off, so the
optimization changes neither the evaluated trajectories nor the scores. Monitoring
off is byte-for-byte the previous path and produces zero progress messages.

### Monitoring responsiveness

Parent-side sink timestamps on the Champion workload:

| Arm | First update | Median gap | Max gap | Applied updates |
| --- | ---: | ---: | ---: | ---: |
| before, 2 workers | 0.010 s | 0.0021 s | 0.011 s | 7671 |
| after, 2 workers | 1.008 s | 0.0254 s | 1.002 s | 53 |
| after, 4 workers | 1.017 s | 0.0124 s | 0.987 s | 100 |

The optimized path still updates at least once per second while search runs and
immediately on each game completion, inside the 0.5–2 s target. The one-second
interval is nominal, not a hard limit: a fast evaluator that reaches 16,384 leaves
earlier reports sooner, and lifecycle events flush immediately. The independent
training heartbeat is unchanged and continues to cover liveness between updates.

### Wall-clock

The cross-source monitor-off arms were collected at different machine states
(before off median 16.108 s, after off median 18.077 s) even though that path is
unchanged, so a raw before/after median is dominated by host drift. Four alternating
fresh-process pairs at a smaller workload (12 games, 96 visits, 12 concurrency,
batch 12) isolate the monitoring cost within each source:

| Pair | Before off → on | After off → on |
| ---: | ---: | ---: |
| 1 | 8.712 → 9.006 s | 8.271 → 8.361 s |
| 2 | 8.549 → 8.901 s | 8.377 → 8.674 s |
| 3 | 8.337 → 9.467 s | 9.095 → 8.806 s |
| 4 | 8.359 → 9.556 s | 8.722 → 8.330 s |
| Median | **+0.74 s** | **−0.10 s** |

The before implementation paid about 0.7 s of monitoring overhead per ~8.5 s phase
(4/4 pairs positive); the after implementation is within noise (2/4 positive). The
removed overhead is real but small in absolute terms and does not establish a
throughput claim on its own. The robust result is the ~170–180× IPC reduction; the
wall-clock benefit is the removed monitoring cost, roughly 5–9% of this phase's
runtime, and it scales with the message rate rather than with search work.

### Regressions

Shieldbearer evaluation (12 games, 64 visits) and a normal self-play workload
(12 games, 64 visits) were measured with two workers:

| Workload | Monitoring | Before messages | After messages | Before on/off | After on/off |
| --- | --- | ---: | ---: | ---: | ---: |
| Shieldbearer evaluation | on | 1233 | 12 | 2.278 / 1.934 s | 1.793 / 2.856 s |
| Self-play | on | 214 | 216 | 5.094 / 4.957 s | 4.946 / 5.012 s |

Shieldbearer evaluation messages fall by **103×**. Self-play message counts are
unchanged (one per played move), confirming the evaluation fix does not degrade
self-play monitoring. Shieldbearer evaluation work hashes match before and after
(`7fa0f2ed…`). The short shieldbearer/self-play wall times are within host noise.

### Post-review corrections

The [review report](./raw/training/training-evaluation-progress-review.json) appends new
measurements without changing the historical values above. The reusable benchmark
now runs a properly paired protocol: one warmed pool per worker count, both
monitoring modes warmed once and discarded, then alternating off/on pairs with the
first arm reversed every pair. It reports paired absolute and percentage overhead
in addition to the per-arm totals.

Evaluation paired overhead on the 16-game/128-visit workload:

| Workers | Before median overhead | After median overhead | After progress messages | Messages / batch |
| ---: | ---: | ---: | --- | ---: |
| 2 | +0.134 s (+0.62%) | −1.038 s (−4.84%) | 52 / 53 / 51 | 0.007 |
| 4 | +1.901 s (+8.19%) | −0.705 s (−3.07%) | 92 / 95 / 99 | 0.007 |

The after medians are negative because the remaining monitoring cost is below this
host's run-to-run noise. The message-count reduction is ~150× in these runs and the
evaluation work hashes are unchanged. The earlier 170–181× figures remain the
historical record.

Self-play initial active reporting (8 games, 64 visits, 16 plies, 8 concurrency):

| State | First active update | First position tick |
| --- | ---: | ---: |
| Before the review fix (`730a946`) | 0.225–0.378 s | 0.225–0.378 s |
| After the review fix | 0.005–0.018 s | 0.267–0.301 s |

Before the fix the active count became visible only when the first move completed,
so it arrived at the same instant as the first position. After the fix the initial
active count arrives in milliseconds, before the first expensive move, at the cost
of two bounded messages per phase (one announcement per worker slice). Self-play
messages were 117 before and 119–121 after; positions and simulations stayed exact.

### Limitations

- The full 64-game/32-concurrency workload was not run repeatedly; the scaled
  workload's per-batch message rate is the quantity being compared and is scale
  independent.
- Wall-clock measurements come from a shared 4-core CPU host and carry run-to-run
  noise larger than the sub-second monitoring difference. The message-count and
  latency results do not depend on that noise.
- GPU/OpenVINO and remote-worker execution were not re-benchmarked; the change is
  parent/actor IPC and does not alter inference backends or the remote protocol.
- Progress batching remains an internal implementation detail; no configuration or
  frontend control was added.
