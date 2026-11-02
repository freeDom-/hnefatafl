# Live training monitoring overhead

The [raw report](training-monitoring-heartbeat.json) records every sample, the
source-independent workload definition and the environment. All measurements are
CPU-only, single-threaded (`OMP_NUM_THREADS=1`, `torch.set_num_threads(1)`) and
need no GPU.

Reproduce in a fresh process without other benchmarks or tests:

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python scripts/benchmark_training_heartbeat.py \
  --output docs/benchmarks/training-monitoring-heartbeat.json --repeats 8
```

## Hot path

The training loop updates aggregated counters once per searched position and once
per optimizer batch. The update is a plain dict accumulation behind one lock; the
monitor thread never touches the search, inference or CUDA state.

| Measurement | Value |
| --- | ---: |
| Counter update | 0.65 µs per call |

At the measured self-play rate (117 positions in 0.61 s ≈ 192/s) the update costs
about 0.14 ms per second of training (0.02%). A much faster GPU self-play phase
at 1,000 positions/s would still spend 0.74 ms per second (0.07%).

## Publication

The retained progress snapshot carries the complete iteration history and is
rewritten only on real progress events. Heartbeats go to a separate bounded file,
so a one-second cadence never rewrites retained history.

| File | Size for a 120-iteration job | Median atomic write |
| --- | ---: | ---: |
| `heartbeat.json` (bounded projection) | 294 B | 0.13 ms |
| `progress.json` (retained history) | 119,775 B | 9.11 ms |

A one-second heartbeat therefore writes ~0.3 KB/s instead of the ~120 KB/s the
retained snapshot would cost at the same cadence.

## Study-level publication

A tuning study publishes its own bounded heartbeat between trials, keeping the last
trial's data without its live stage, timers or counters.

| Measurement | Value |
| --- | ---: |
| `heartbeat.json` (bounded study projection) | 340 B, 0.25 ms |
| Retained study snapshot (same history) | 132,269 B, 15.46 ms |
| Trial projection (`without_live_timing`) | 7.6 µs |

The study's heartbeat costs the same bounded write as a trial's, and its heartbeat
thread reads only the study's in-memory state: it performs no Optuna or SQLite
query. The projection is a key filter, not a copy of the retained history.

The handover after a trial publishes one bounded heartbeat and one retained study
snapshot from memory, then reads the study's trials once to reconcile the
completed-trial count with Optuna's own state. That read is a storage access, never
a heartbeat-thread activity, and it only triggers a second retained write when it
raises the count, so a study still writes at most one retained snapshot per trial
boundary regardless of the heartbeat cadence.

## Workload comparison

Instrumentation delta on fixed workloads, measured as paired alternating runs
with the first pair discarded as warm-up.

| Workload | Monitor calls per run | Median baseline | Median monitored | Median delta | Run-to-run noise |
| --- | ---: | ---: | ---: | ---: | ---: |
| Self-play (8 games, 16 plies) | 117 | 618.4 ms | 615.2 ms | −34.2 ms | ±215.3 ms |
| Optimization (4 batches) | 4 | 30.3 ms | 29.8 ms | +0.3 ms | ±18.7 ms |

Both deltas are inside the run-to-run spread of the workload itself, so the
instrumentation is not measurable against these workloads at this size. The
directly measured per-call cost above is the meaningful figure; extrapolating it
puts the added work well below 0.1% for a normal training run.

These numbers measure monitoring overhead only. They are not evidence of training
throughput, model strength or GPU behavior, and no CUDA synchronization or
`torch.cuda` query runs on the heartbeat thread.
