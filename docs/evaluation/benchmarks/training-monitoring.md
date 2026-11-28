# Training monitoring overhead

Overhead and responsiveness measurements for live training monitoring, covering local and remote workers. Sample counts and host details remain with their original sections.

<!-- Consolidated from training-monitoring-heartbeat.md -->

## Live training monitoring overhead

The [raw report](./raw/training/training-monitoring-heartbeat.json) records every sample, the
source-independent workload definition and the environment. All measurements are
CPU-only, single-threaded (`OMP_NUM_THREADS=1`, `torch.set_num_threads(1)`) and
need no GPU.

Reproduce in a fresh process without other benchmarks or tests:

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python scripts/benchmark_training_heartbeat.py \
  --output docs/evaluation/benchmarks/raw/training/training-monitoring-heartbeat.json --repeats 8
```

### Hot path

The training loop updates aggregated counters once per searched position and once
per optimizer batch. The update is a plain dict accumulation behind one lock; the
monitor thread never touches the search, inference or CUDA state.

| Measurement | Value |
| --- | ---: |
| Counter update | 0.65 µs per call |

At the measured self-play rate (117 positions in 0.61 s ≈ 192/s) the update costs
about 0.14 ms per second of training (0.02%). A much faster GPU self-play phase
at 1,000 positions/s would still spend 0.74 ms per second (0.07%).

### Publication

The retained progress snapshot carries the complete iteration history and is
rewritten only on real progress events. Heartbeats go to a separate bounded file,
so a one-second cadence never rewrites retained history.

| File | Size for a 120-iteration job | Median atomic write |
| --- | ---: | ---: |
| `heartbeat.json` (bounded projection) | 294 B | 0.13 ms |
| `progress.json` (retained history) | 119,775 B | 9.11 ms |

A one-second heartbeat therefore writes ~0.3 KB/s instead of the ~120 KB/s the
retained snapshot would cost at the same cadence.

### Study-level publication

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

### Workload comparison

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

<!-- Consolidated from training-monitoring-remote.md -->

## Remote training monitoring overhead

The [raw report](./raw/training/training-monitoring-remote.json) records every sample, the
workload definition and the environment. All measurements are CPU-only and need
no GPU or remote host: the worker report and the server's persistence step are
measured directly.

Reproduce in a fresh process without other benchmarks or tests:

```sh
.venv/bin/python scripts/benchmark_remote_monitoring.py \
  --output docs/evaluation/benchmarks/raw/training/training-monitoring-remote.json --repeats 8
```

Each workload is one job whose retained snapshot has a realistic history row per
iteration (~100 B of metrics and timings each). The worker republishes at
`PROGRESS_REPORT_SECONDS = 2.0`, so 30 reports per minute.

### Worker report

A report whose child revision is unchanged forwards only the bounded liveness
projection; the retained history, metrics and log tail stay on the server.

| Retained iterations | Repeated full snapshot | Bounded heartbeat | Reduction |
| --- | ---: | ---: | ---: |
| 10 | 1,476 B | 446 B | 3× |
| 200 | 8,697 B | 448 B | 19× |
| 2000 | 205,849 B | 448 B | 460× |

| Retained iterations | Bytes per minute before | Bytes per minute now |
| --- | ---: | ---: |
| 10 | 44,280 B | 13,380 B |
| 200 | 260,910 B | 13,440 B |
| 2000 | 6,175,470 B | 13,440 B |

Serializing the retained snapshot costs 0.5 ms (10 iterations), 1.3 ms (200) and
3.9 ms (2000) per report; the bounded projection is 0.02 ms. The worker no longer
re-reads the large retained file while the child revision is unchanged: it reads
the bounded heartbeat file, which it needs to detect a change.

### Server persistence

| Retained iterations | Stored row | Full snapshot | Bounded heartbeat |
| --- | ---: | ---: | ---: |
| 10 | 1.5 KB | 1.4 ms | 1.4 ms |
| 200 | 8.7 KB | 5.0 ms | 5.6 ms |
| 2000 | 205.7 KB | 10.5 ms | 9.4 ms |

The row write is dominated by the retained history the dashboard still reads, so
it is unchanged by the bounded report: the job row keeps the history, and the
same row must be written to persist the lease expiry and the report sequence that
make reconciliation and out-of-order rejection authoritative. Skipping that write
for liveness refreshes would either lose lease renewal or weaken the sequence
guard, so it is deliberately not skipped. At 30 reports per minute the largest
workload above costs about 5 ms per second (0.5% of one core) and ~6 MB per
minute of SQLite writes; a 200-iteration job costs 0.2% of one core.

### What is not measured

These numbers are protocol and serialization costs, not real-world remote
throughput. No network, TLS, remote CPU or GPU was exercised, and no claim is
made about training throughput. GPU-dependent behavior is out of scope: the
monitoring thread never issues a CUDA call, so nothing here depends on it.

<!-- Consolidated from training-monitoring-responsiveness.md -->

## Training monitoring responsiveness

The original optimization-branch measurement and the isolated port to main are
recorded separately. The [main comparison](#isolated-main-port) measures the
monitoring changes without the engine or evaluation optimizations.

### Original optimization-branch measurement

Baseline: merged training optimization branch
`49dd32a682d6eae13de8f7daa784f38ca2aaeb72`. The [raw report](
./raw/training/training-monitoring-responsiveness.json) retains every request sample, response
size, normalized navigator/worker fingerprint, source SHA-256 and environment.

The synthetic workload contains 80 completed remote jobs, 120 history points per
job and four registered workers. Histories, results and log tails are populated;
all training metrics in the fixture are synthetic. Real authenticated ASGI
requests exercise the SQLite repository and HTTP serialization. No training or
GPU computation is measured.

Three fresh-process A/B pairs alternate baseline/candidate order. Each process
discards one warm-up request, then measures five requests per endpoint. The table
uses the median of the three process medians. No other benchmarks or tests ran
during collection. Application startup, fixture creation and login are excluded.

| Endpoint used by monitoring | Before median | After median | Response bytes before / after |
| --- | ---: | ---: | ---: |
| Job navigator | 1.2583 s | 0.02593 s | 7,987,121 / 24,641 |
| Worker list | 0.6084 s | 0.00482 s | 2,301 / 2,301 |

All normalized navigator and worker fingerprints match. The old server ignores
the summary query and returns its ordinary full list. The updated navigator
requests summaries and fetches full detail only for its selection; this payload
change is intentional. The full list remains the default for existing clients.
API regression tests separately verify full history/detail retention, unchanged
saved records, active lease counts, local progress without repeated database
writes, legacy counters and one-time recovery of late local result files.

The API changes remove repeated decoding of archived histories and per-job
database reads. An additive SQLite expression index supports active-status
queries; worker counts use one grouped query. Archived local summaries avoid
loading progress/result/log files. Terminal records missing a final result can
recover it once without replacing retained history with a summary.

Frontend regressions reproduce slow-response starvation and verify that a
pending poll is coalesced rather than superseded by each timer tick. Hardware and
worker requests cannot hold up job data. Selected detail requests are independent,
obsolete selections are cancelled, and older detail cannot replace newer live
summary progress. Unit cases cover error recovery, unmount and React StrictMode;
desktop/mobile browser cases exercise a delayed poll past multiple timer ticks.

Reproduce from the candidate checkout with development dependencies installed:

```sh
mkdir -p work/monitoring-baseline
git archive 49dd32a682d6eae13de8f7daa784f38ca2aaeb72 hnefatafl | tar -x -C work/monitoring-baseline
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python scripts/benchmark_training_monitoring.py --source work/monitoring-baseline --output work/monitoring-before.json
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python scripts/benchmark_training_monitoring.py --source . --output work/monitoring-after.json
```

Repeat in three alternating fresh-process pairs for the collection protocol.
Absolute timings depend on the server and data size. This evidence establishes
monitoring API latency and payload reduction; it does not establish recovery of
any reported GPU simulations/second. Compare completed self-play phases with
identical game counts, concurrency, precision, model, backend and search budgets.

### Isolated main port

Baseline: main at `5a343668411033ed3283ed51e01323454975723a`. Candidate:
`228178c2b09426cbf23b4b01a2ea12a47487c398`, containing only the two monitoring
fix commits cherry-picked from PR #43. The [main raw report](
./raw/training/training-monitoring-main.json) retains the separate samples and source hashes;
the original report above is unchanged.

This collection uses the same synthetic fixture and three alternating
fresh-process pairs described above, without other tests or benchmarks running.
All shared navigator and worker fingerprints match.

| Endpoint used by monitoring | Main median | Isolated fix median |
| --- | ---: | ---: |
| Job navigator | 1.20172 s | 0.02644 s |
| Worker list | 0.58048 s | 0.00449 s |

Only `server.py`, `remote_training.py`, `storage.py` and the new
`training_monitoring.py` change within the Python package. The other 47 existing
package files are byte-identical to the main baseline, including engine, search,
self-play, evaluation, network and optimizer code. Neither PR #37 nor PR #38 is
an ancestor of the isolated candidate. These API timings do not measure CUDA
training throughput.

To reproduce against main, use the commands above with
`5a343668411033ed3283ed51e01323454975723a` as the archived baseline and run the
candidate from the isolated monitoring branch. Keep the same three-pair protocol.
