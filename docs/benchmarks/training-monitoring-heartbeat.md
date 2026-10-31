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
| Counter update | 0.74 µs per call |

At the measured self-play rate (117 positions in 0.61 s ≈ 192/s) the update costs
about 0.14 ms per second of training (0.02%). A much faster GPU self-play phase
at 1,000 positions/s would still spend 0.74 ms per second (0.07%).

## Publication

The retained progress snapshot carries the complete iteration history and is
rewritten only on real progress events. Heartbeats go to a separate bounded file,
so a one-second cadence never rewrites retained history.

| File | Size for a 120-iteration job | Median atomic write |
| --- | ---: | ---: |
| `heartbeat.json` (bounded projection) | 294 B | 0.17 ms |
| `progress.json` (retained history) | 119,775 B | 6.33 ms |

A one-second heartbeat therefore writes ~0.3 KB/s instead of the ~120 KB/s the
retained snapshot would cost at the same cadence.

## Workload comparison

Instrumentation delta on fixed workloads, measured as paired alternating runs
with the first pair discarded as warm-up.

| Workload | Monitor calls per run | Median baseline | Median monitored | Median delta | Run-to-run noise |
| --- | ---: | ---: | ---: | ---: | ---: |
| Self-play (8 games, 16 plies) | 117 | 608.0 ms | 612.4 ms | +4.4 ms | ±120.4 ms |
| Optimization (4 batches) | 4 | 26.1 ms | 24.6 ms | −1.5 ms | ±9.5 ms |

Both deltas are inside the run-to-run spread of the workload itself, so the
instrumentation is not measurable against these workloads at this size. The
directly measured per-call cost above is the meaningful figure; extrapolating it
puts the added work well below 0.1% for a normal training run.

These numbers measure monitoring overhead only. They are not evidence of training
throughput, model strength or GPU behavior, and no CUDA synchronization or
`torch.cuda` query runs on the heartbeat thread.
