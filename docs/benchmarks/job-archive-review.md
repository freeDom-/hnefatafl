# Job archive review benchmarks

[Training data](../TRAINING_DATA.md#moving-data-between-installations)

These measurements compare the review fixes with PR #58 at
`c85f3d4dc58af142ed06d56847aeadcebc4f35ae`, using schema-3 TAR.GZ and gzip level 1
on both sides. The [original migration benchmarks](job-archives.md) remain
historical evidence against the pre-migration ZIP writer. The
[raw review measurements](job-archive-review.json) retain every sample, environment
information and measured source hashes.

Measured on 6 November 2026 with Python 3.12.14, CPU-only PyTorch, three reported
CPUs with a two-CPU cgroup quota, and three samples per case. Cases run in fresh
processes, alternating order for catalog/admission and concurrent-training
comparisons. Archive import/export
timings measure the Python archive functions, excluding HTTP transport and the
full-detail response serialization. API regression tests separately validate the
import response, full endpoint and repeated lightweight navigator polls.

## Round trips and large histories

Fixtures contain real one-iteration CPU smoke checkpoints and synthetic histories
made by repeating that measured iteration. They do not represent 1,000 optimizer
updates. The large case adds diagnostic vectors to every history point, making
each progress/result report 36,154,396 bytes before archive canonicalization.
Both importers restore all history points; distinct database/file report tests
also check their original values and numeric representations.

| History | Before export / import seconds | After export / import seconds | Archive bytes, both |
| --- | ---: | ---: | ---: |
| 1 iteration | 0.054 / 0.099 | 0.054 / 0.099 | 44,751 |
| 100 iterations | 0.264 / 0.355 | 0.257 / 0.366 | 50,936 |
| 1,000 iterations | 2.230 / 2.986 | 2.218 / 3.024 | 140,727 |
| 1,000 iterations, large reports | 8.435 / 14.414 | 8.674 / 14.035 | 3,814,605 |

Times are medians. At 1,000 ordinary iterations export is 0.5% faster and import
1.3% slower; large-report export is 2.8% slower and import 2.6% faster. These small
differences are host/run variability rather than a general speed guarantee.
Byte-identical archive sizes and the unchanged schema/chunk layout confirm that
the compact summary cache does not add a redundant history document to exports.

| Workload / operation | Before peak RSS MiB | After peak RSS MiB | Before temporary disk MiB | After temporary disk MiB |
| --- | ---: | ---: | ---: | ---: |
| 1,000 / export | 245.3 | 245.1 | 3.57 | 3.57 |
| 1,000 / import | 287.1 | 294.3 | 17.39 | 15.94 |
| Large reports / export | 442.7 | 442.6 | 27.60 | 27.60 |
| Large reports / import | 822.5 | 822.2 | 120.36 | 120.36 |

RSS is Linux `VmHWM` for the entire fresh process, including Python, PyTorch and
fixture objects. Temporary disk is allocated file blocks sampled every 10 ms,
deduplicated by inode and excluding initial source inodes and published job data.
Export download outputs and SQLite journals are included. Directory blocks and
very short-lived allocations between samples are excluded. The sampler has the
same observation overhead on both sides. Import still materializes bounded JSON
documents and retains original database histories; this is not constant-memory
or unlimited-size import. A separate regression imports a newer >16 MiB file
report with an older small database record, verifies that SQLite stays small,
and prevents any large-report deserialization during navigator refreshes.

## Model catalogs and lock duration

Each catalog has two referenced models and a small job artifact. Unrelated
historical entries reuse a safe toy checkpoint through hard links, avoiding
large committed files. Counters wrap filesystem calls during `snapshot_job()`;
that is the API's global job/catalog lock interval. Total snapshot/export time
also includes the subsequent unlocked discovery, model pinning and compression.

| Catalog entries | Before global-lock ms | After global-lock ms | Before / after total snapshot + export ms | Before / after links under global locks | Before / after stat calls under global locks |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2 | 7.13 | 6.76 | 14.61 / 14.30 | 3 / 1 | 15 / 9 |
| 1,000 | 46.01 | 7.21 | 92.46 / 17.01 | 1,001 / 1 | 3,009 / 9 |
| 10,000 | 383.58 | 7.88 | 779.15 / 49.96 | 10,001 / 1 | 30,009 / 9 |

Totals add the median protected and unlocked intervals. Required model pins now
happen after reference discovery, outside global lifecycle locks and under the
registry lock. Exactly two model files are pinned; separate tests cover four
references distributed across metadata, checkpoint state, metric files and remote
best-model uploads. The registry's single JSON index still requires an O(N)
metadata decode/scan under its own lock; the fix removes O(N) model filesystem
operations and global lock contention, without introducing a new catalog store.

On actual different devices (`/workspace` and `/tmp`), a 1,000-entry catalog gave:

| Metric | Before | After |
| --- | ---: | ---: |
| Global-lock interval | 86.29 ms | 7.68 ms |
| Total snapshot + export | 122.55 ms | 18.12 ms |
| File-content copies under global locks | 1,001 | 0 |
| Bytes copied under global locks | 7,112,029 | 0 |

Open descriptors retain source inodes until copying completes after lock release.
Regressions delete the original job before deferred copying, including an Optuna
study with a frozen dependency, and then import the resulting archive.

## Concurrent admission

Nine simultaneous 2 MiB multipart uploads target a real authenticated ASGI app.
The first archive worker pauses at extraction; eight competing requests then
arrive. The baseline queues all of them; the fix returns eight HTTP 409 responses
with an actionable message and `Retry-After: 1`, before consuming their bodies.

| While the first worker is paused | Before | After |
| --- | ---: | ---: |
| Staged uploads | 9 | 1 |
| Multipart spools | 9 | 1 |
| Staged + spooled bytes | 36 MiB | 4 MiB |
| Competing responses completed | 0 / 8 | 8 / 8 |
| Median busy-response latency | Waiting for release | 14.2 ms |
| Median ordinary summary latency | 5.70 ms | 4.74 ms |
| Temporary uploads after all requests | 0 | 0 |

These counts scale with admitted capacity rather than request count. Existing
512 MiB archive and multipart-envelope limits remain unchanged. Tests additionally
cover simultaneous import/export, authorization, malformed/oversized bodies,
multipart disconnect/cancellation, abandoned workers, output cleanup and late
publication refusal. There is no extra worker infrastructure or dependency.

## Training during export

The same real trainer warms up four iterations and then overlaps 40 more real
iterations with a 1,000-point archive export. Three repeats alternate control and
export order. Training uses one CPU thread, tiny smoke settings and independent
storage, as in the original benchmark.

| Scenario | Mean iterations/second | Observed range |
| --- | ---: | ---: |
| No export | 19.220 | 18.784–19.609 |
| Initial PR export | 18.555 | 17.958–19.037 |
| Review fixes export | 19.246 | 19.174–19.371 |

After-fix throughput is 3.7% above the initial PR and 0.1% above control in this
short sample. There is no observed material regression; these CPU smoke results
do not establish zero contention on every host or predict GPU/large-replay
throughput. Training, inference and remote-worker production sources are unchanged.

## Reproduction

Use fresh `--root` directories. The archived baseline tree is needed for the
HTTP admission benchmark; the module baseline suffices for archive round trips.

```sh
mkdir -p work/review-baseline/full
git show c85f3d4:hnefatafl/job_archives.py > work/review-baseline/job_archives.py
git archive c85f3d4 hnefatafl pyproject.toml | tar -x -C work/review-baseline/full
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python scripts/benchmark_job_archives.py \
  --root work/review-roundtrips --baseline work/review-baseline/job_archives.py \
  --baseline-format tar --repetitions 3 --training-repetitions 3 --measure-disk \
  --output work/review-roundtrips.json
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python scripts/benchmark_job_archives.py \
  --root work/review-large --baseline work/review-baseline/job_archives.py \
  --baseline-format tar --iteration-counts 1000 --large-history --repetitions 3 \
  --training-repetitions 0 --measure-disk --output work/review-large.json
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python scripts/benchmark_job_archive_review.py \
  --root work/review-resources --baseline-tree work/review-baseline/full \
  --repetitions 3 --output work/review-resources.json
```

The `pr` label in round-trip raw samples denotes the initial PR baseline; `tar`
denotes the review fixes. Resource samples use `before` and `after` explicitly.
