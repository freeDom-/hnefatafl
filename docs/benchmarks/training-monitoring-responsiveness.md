# Training monitoring responsiveness

Baseline: merged training optimization branch
`49dd32a682d6eae13de8f7daa784f38ca2aaeb72`. The [raw report](
training-monitoring-responsiveness.json) retains every request sample, response
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
