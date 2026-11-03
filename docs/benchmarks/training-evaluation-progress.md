# Evaluation progress reporting

> This measures the progress-reporting fix on `perf/batch-evaluation-progress`. It
> is separate from the historical [parallel-search](training-parallel-search.md)
> and [monitoring](training-monitoring-heartbeat.md) reports, whose numbers are
> unchanged and are not restated here.

The [raw report](training-evaluation-progress.json) retains the per-arm samples,
latency samples, fixed workload and environment. All measurements are CPU-only
Brandubh with the recommended 32-channel/2-block network, exact-input cache off,
mixed precision off and one math thread per process.

## Root cause

`_ActorProgress` batched progress into messages with `PROGRESS_DELTAS = 64` and
`PROGRESS_INTERVAL_SECONDS = 0.25`, but the actor's `refill()` callback flushed the
accumulator after every cooperative scheduler step. Evaluation ticks once per
neural batch (`evaluation_plies=len(states)`), and the scheduler steps once per
batch, so every batch became its own IPC message. The batching bounds were
defeated: the measured message rate was 1.000 messages per inference batch at both
two and four workers.

## Fix

The refill callback now only records the active-game count; the next tick or
lifecycle boundary flushes it. Self-play ticks once per played move and keeps its
per-move cadence. Evaluation uses a separate policy: at most one message per second
with a 16,384-leaf work cap. Lifecycle flushes at game completion, slice end and
cancellation still deliver the final delta, so counters stay exact. Trigger reasons,
send time and parent handle time are reported as bounded phase metrics.

## Champion evaluation A/B

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

## Monitoring responsiveness

Parent-side sink timestamps on the Champion workload:

| Arm | First update | Median gap | Max gap | Applied updates |
| --- | ---: | ---: | ---: | ---: |
| before, 2 workers | 0.010 s | 0.0021 s | 0.011 s | 7671 |
| after, 2 workers | 1.008 s | 0.0254 s | 1.002 s | 53 |
| after, 4 workers | 1.017 s | 0.0124 s | 0.987 s | 100 |

The optimized path still updates at least once per second while search runs and
immediately on each game completion, inside the 0.5–2 s target. The independent
training heartbeat is unchanged and continues to cover liveness between updates.

## Wall-clock

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

## Regressions

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

## Limitations

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
