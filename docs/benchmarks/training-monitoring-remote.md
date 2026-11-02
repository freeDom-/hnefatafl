# Remote training monitoring overhead

The [raw report](training-monitoring-remote.json) records every sample, the
workload definition and the environment. All measurements are CPU-only and need
no GPU or remote host: the worker report and the server's persistence step are
measured directly.

Reproduce in a fresh process without other benchmarks or tests:

```sh
.venv/bin/python scripts/benchmark_remote_monitoring.py \
  --output docs/benchmarks/training-monitoring-remote.json --repeats 8
```

Each workload is one job whose retained snapshot has a realistic history row per
iteration (~100 B of metrics and timings each). The worker republishes at
`PROGRESS_REPORT_SECONDS = 2.0`, so 30 reports per minute.

## Worker report

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

## Server persistence

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

## What is not measured

These numbers are protocol and serialization costs, not real-world remote
throughput. No network, TLS, remote CPU or GPU was exercised, and no claim is
made about training throughput. GPU-dependent behavior is out of scope: the
monitoring thread never issues a CUDA call, so nothing here depends on it.
