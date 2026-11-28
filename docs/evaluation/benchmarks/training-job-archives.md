# Training job-archive compatibility and performance

Iterative reviews of portable training-job archives. Later reviews supersede earlier measurements where they say so; each section keeps its original revision, sample and conclusion.

<!-- Consolidated from job-archives.md -->

## Portable job archive benchmarks

[Training data](../../training/models.md#move-data-between-installations)

The benchmark uses real CPU smoke-training checkpoints and synthetic long metric
histories made by repeating the measured iteration. It does not claim 1,000 real
optimizer updates, GPU behavior, model strength or production-scale throughput.
All compression/serialization is requested explicitly by archive operations;
training, inference, monitoring and remote-worker sources are unchanged.

Reproduce from the repository root, using the pre-change implementation at
`0407d88` as the baseline:

```sh
mkdir -p work
git show 0407d88:hnefatafl/job_archives.py > work/legacy_job_archives.py
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python scripts/benchmark_job_archives.py \
  --root work/archive-benchmark --baseline work/legacy_job_archives.py \
  --repetitions 3 --training-repetitions 3 --output work/archive-benchmark.json
```

Choose a fresh `--root` for each run. Every archive export/import is measured in a
fresh process after imports/fixture loading; RSS is process peak including Python,
PyTorch and fixture objects. CPU seconds use the process CPU clock. The shipped
baseline stores ZIP members without compression. Its strict 16 MiB JSON limit is
measured unchanged; `zip-relaxed` lifts that guard **only in the benchmark module**
to permit a size/time comparison when the shipped exporter refuses the workload.
The fix does not use that workaround.

Concurrent measurements warm up four real training iterations, synchronize an
export and the next 40 real iterations with a barrier, then measure their overlap.
A control runs the same training without export. Orders alternate across repeats;
startup is excluded. These short, single-thread CPU smoke runs cannot establish
zero contention on every host. Full tensor/replay checkpoints remain untouched in
the training process. Archive tests separately verify that polling and deletion
remain responsive while compression is paused and that decompression does not
hold lifecycle locks.

Measured on 6 November 2026 with Python 3.12.14, CPU-only PyTorch, three available
CPUs, one training CPU thread, and three samples per scenario. The
[raw measurements](./raw/training/job-archives.json) retain all samples and source hashes. JSON
sharing uses strict representation equality: booleans/integers/floats and the sign
of zero remain distinct. The 1,000-iteration timing fixture fits the original guard;
a separate regression makes **each** report exceed 16 MiB and verifies a complete
round trip.

| History iterations | Stored ZIP bytes | TAR.GZ level-1 bytes | ZIP export / import seconds | TAR.GZ export / import seconds |
| --- | ---: | ---: | ---: | ---: |
| 1 | 226,461 | 44,882 | 0.050 / 0.089 | 0.049 / 0.092 |
| 100 | 2,392,169 | 52,778 | 0.210 / 0.299 | 0.243 / 0.343 |
| 1,000 | 22,084,180 | 131,926 | 1.961 / 2.638 | 2.195 / 2.901 |

Times are medians. The 1,000-iteration archive is 99.40% smaller.
Export CPU time is 1.955 seconds for ZIP and
2.193 for TAR.GZ. Export takes about
12% longer and
import about 10% longer
with strict JSON comparison, streaming serialization and document reconstruction.
This history-heavy fixture compresses especially well and has small checkpoints;
other jobs need not achieve this ratio.

Linux peak RSS comes from `/proc/self/status` `VmHWM`, avoiding `ru_maxrss`'s
inherited pre-exec parent peak on this runtime. Values include Python, PyTorch and
loaded job/fixture objects. Median process peaks at 1,000 iterations are:

| Operation | ZIP peak MiB | TAR.GZ peak MiB |
| --- | ---: | ---: |
| Export | 284.1 | 244.8 |
| Import | 281.1 | 286.6 |

#### Compression level

| gzip level | 1,000-iteration archive bytes | Export seconds |
| --- | ---: | ---: |
| 1 | 131,926 | 2.195 |
| 3 | 122,359 | 2.148 |
| 6 | 67,869 | 2.215 |

JSON processing dominates this fixture, making level timing differences small
relative to process/run variability. A separate seeded checkpoint probe covers
the tensor-heavy tradeoff: `PolicyValueNet('brandubh', 128, 8)`, seed 17, saved with
`save_checkpoint`, produces 9,530,329 bytes. It is an **untrained network**, not a
claim about training quality. Compressing those exact bytes three times at each
level with `gzip.GzipFile(filename='', mtime=0, compresslevel=level)` gives:

| gzip level | Compressed checkpoint bytes | Median seconds |
| --- | ---: | ---: |
| 1 | 8,710,881 | 0.303 |
| 3 | 8,709,042 | 0.346 |
| 6 | 8,676,973 | 0.370 |

Level 6 takes about 22% longer for only 0.39% fewer checkpoint bytes. Level **1**
is selected to limit CPU contention as tensor payloads grow, while the chunk
layout and gzip already remove most repeated history bytes. There is one outer
gzip pass; tensor ZIP payloads are not individually recompressed first.

#### Concurrent training

| Scenario | Mean iterations/second | Observed range | Mean self-play games/hour |
| --- | ---: | ---: | ---: |
| No export | 18.662 | 18.205–19.056 | 67,185 |
| Existing ZIP export | 17.107 | 15.861–17.898 | 61,584 |
| TAR.GZ export | 18.461 | 18.109–18.706 | 66,458 |

The ZIP concurrency scenario uses the benchmark-only relaxed guard; that guard is
not reached in this fixture, which produces the same inventory and archive size
under the strict baseline. TAR.GZ averages 7.92%
above concurrent ZIP and 1.08% below the no-export
control, with overlapping TAR/control ranges. These short runs show no clear
additional regression relative to the existing exporter; they do **not** establish
zero resource contention or predict GPU/large-replay throughput. There are no
training-loop, inference, metric-persistence or remote-worker source changes.
Serialization/compression holds no lifecycle/catalog lock. Concurrency regressions
separately test responsive polling/deletion, pinned snapshots and decompression.

Only inactive jobs and inactive study contributors can be exported. Physical
members and reconstructed documents have separate absolute budgets; JSON parsing
still materializes one bounded document, and the existing SQLite record stores
full histories. This is not unlimited, constant-memory import. GPU inference,
long production training, disk saturation and cross-filesystem snapshot fallback
performance were not benchmarked. Cross-filesystem correctness is tested.

<!-- Consolidated from job-archive-review.md -->

## Job archive review benchmarks

[Training data](../../training/models.md#move-data-between-installations)

These measurements compare the review fixes with PR #58 at
`c85f3d4dc58af142ed06d56847aeadcebc4f35ae`, using schema-3 TAR.GZ and gzip level 1
on both sides. The [original migration benchmarks](#portable-job-archive-benchmarks) remain
historical evidence against the pre-migration ZIP writer. The
[raw review measurements](./raw/training/job-archive-review.json) retain every sample, environment
information and measured source hashes.

Measured on 6 November 2026 with Python 3.12.14, CPU-only PyTorch, three reported
CPUs with a two-CPU cgroup quota, and three samples per case. Cases run in fresh
processes, alternating order for catalog/admission and concurrent-training
comparisons. Archive import/export
timings measure the Python archive functions, excluding HTTP transport and the
full-detail response serialization. API regression tests separately validate the
import response, full endpoint and repeated lightweight navigator polls.

### Round trips and large histories

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

### Model catalogs and lock duration

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

### Concurrent admission

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

### Training during export

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

### Reproduction

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

<!-- Consolidated from job-archive-second-review.md -->

## Job archive second-review measurements

[Training data](../../training/models.md#move-data-between-installations)

These measurements compare the remaining review fixes with PR #58 at
`9b58c884210456d9d487edc1a8efd4a7f5d0df33`. Both use schema-3 TAR.GZ with gzip
level 1 and identical validation limits. The [previous review](#job-archive-review-benchmarks)
and [original migration](#portable-job-archive-benchmarks) measurements remain historical evidence.

### Results

Measured on 7 November 2026 with Python 3.12.14, Node 24.19.0, Chromium
154.0.8037.57, three reported CPUs and a two-CPU cgroup quota. Times and memory
values below are medians of three samples. The [raw measurements](./raw/training/job-archive-second-review.json)
retain all samples, archive hashes and reproduction-source hashes.

| Workload | Formatted source report MiB | Before POST bytes | After POST bytes | Before HTTP seconds | After HTTP seconds | Before server RSS MiB | After server RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 iterations | 5.1 | 7,212,006 | 5,782 | 4.531 | 3.463 | 300.7 | 301.0 |
| Timing vectors (>16 MiB) | 34.5 | 50,414,120 | 5,800 | 18.010 | 14.371 | 745.1 | 769.7 |
| Large valid JSON document | 67.6 | 138,334,104 | 5,799 | 34.093 | 26.845 | 1296.3 | 1296.4 |

Compact responses remain around 5.8 KB regardless of history size. Import HTTP
completion is approximately 20–24% faster on these fixtures. Peak server RSS does
not improve: reconstruction, validation and existing SQLite serialization still
dominate it. The timing-vector median rises about 3.3%, with overlapping observed
RSS ranges; the other two cases are essentially flat. No memory budget was raised.
Full histories remain intact and are retrieved through the detail endpoint.

| Workload | Before / after initial UI seconds | Before / after selection seconds | Before / after observed peak JS heap MiB | Before / after retained JS heap MiB |
| --- | ---: | ---: | ---: | ---: |
| 1,000 iterations | 5.877 / 4.996 | 4.937 / 4.347 | 19.6 / 14.9 | 11.9 / 8.8 |
| Timing vectors (>16 MiB) | 22.722 / 18.557 | 19.048 / 14.932 | 56.1 / 26.2 | 43.3 / 24.5 |
| Large valid JSON document | 38.081 / 29.689 | 35.110 / 27.198 | 275.5 / 150.6 | 262.0 / 133.9 |

Every browser sample issued exactly one full-detail GET. The fix removes the
additional full history previously returned by POST. Observed peak JS heap falls
about 53% for timing vectors and 45% for the large document; retained heap falls
about 43% and 49% respectively. Initial UI loading is 15–22% faster. CSS bundle
bytes are identical; the history-loading markup reuses existing localization and
component classes.

| Catalog entries, two references | Before / after protected snapshot ms | Before / after unlocked export ms |
| --- | ---: | ---: |
| 2 | 8.46 / 7.20 | 7.23 / 7.10 |
| 1,000 | 8.43 / 7.50 | 10.25 / 10.08 |
| 10,000 | 7.55 / 8.24 | 46.38 / 46.55 |
| 1,000 (cross-device) | 7.61 / 7.36 | 10.04 / 13.64 |

Global work gains one catalog-index pin, independent of catalog size: same-device
cases use two hard links and eleven stat calls in that interval, versus one link
and nine stat calls before. Required model weights are pinned later. Captured
metadata is reused when the atomically published index still has the same inode;
catalog changes or cross-device index copies require another metadata scan under
the registry lock. No model serialization or compression holds global locks.
The initial lock order remains job → catalog → registry; later model pinning
holds only the registry lock and never reacquires global locks.

| Artifacts / descriptor limit | Before retained process descriptors | After retained process descriptors |
| --- | ---: | ---: |
| 300 / 512 | 304 | 4 |
| 1,200 / 64 | EMFILE at artifact 62 | 4; completes |
| 1,200 / 2,048 | 1,204 | 4 |

With 1,200 artifacts and a 2,048 limit, protected snapshot duration is 148 ms
before and 141 ms after; unlocked copying is 164 / 167 ms. With a 64 limit,
the new snapshot/copy phases take 140 / 146 ms and survive deletion of the
original source. Artifact data is copied outside global locks; source staging
retains paths to private hard links rather than open file handles. Regression
tests additionally perform a full export/import of 1,200 real checkpoint files
under the 64 limit, and cover disk-full errors, cancellation and unsupported pins.

| Training scenario | Mean iterations/second | Observed range |
| --- | ---: | ---: |
| No import | 18.678 | 18.375–18.921 |
| Reviewed implementation | 18.314 | 18.112–18.513 |
| Review fixes | 18.735 | 18.680–18.797 |

There is no observed material training regression in this CPU smoke workload.
Training uses one CPU thread and independent storage. These short runs do not
establish zero contention for every host, shared-disk workload, GPU or large replay
buffer. Production training/inference/remote-worker loops are unchanged.

### Methodology and reproduction

The import benchmark starts a fresh authenticated Uvicorn process for each API
or browser sample. Fixture creation, login and frontend builds precede timing.
Linux `VmHWM` measures peak server RSS, including startup and PyTorch. The HTTP
client runs in a separate process, so its response buffers are excluded from
server RSS. Browser cases separately measure import completion, automatic job
selection and initial history loading, including a 350 ms network-settling window.
All variants use one shared detail request. Before the fix, that request follows
another complete history in the import response; after the fix, the import response
contains only summary data.

Browser memory uses Chromium CDP `JSHeapUsedSize`, sampled every 20 ms. This is an
observed peak, not a guaranteed exact V8 maximum. Retained heap is measured after
explicit garbage collection once history has loaded. Browser process RSS, DOM/native
memory and GPU memory are excluded. Both variants use production builds, the same
Chromium executable and a 1440 × 1000 desktop viewport.

Each fixture has a real one-iteration CPU smoke checkpoint, including optimizer,
replay and RNG state, plus 1,000 synthetic metric points with distinct iteration
numbers. The large-history case adds timing vectors; the large-document case adds
bounded diagnostic text. These are not 1,000 real optimizer updates. Current and
baseline exporters produce byte-identical fixtures; SHA-256 hashes are retained
with the raw measurements. CPU concurrency tests separately warm up four real
iterations, then overlap import with 40 real training iterations. Orders alternate
across three repetitions, with an idle control.

The resource benchmark measures the protected snapshot interval, selected-model
filesystem operations, actual cross-device copying and source deletion. Descriptor
probes use source-filesystem hard-link staging with simulated EXDEV destinations.
They compare 300 and 1,200 retained artifact files under descriptor limits of 64,
512 and 2,048. Checkpoint validity and full archive restoration with 1,200 real
checkpoint files are separately tested under a limit of 64 descriptors.

Import still strictly parses and validates bounded documents and preserves full
database and file reports. The fix removes response reloading/serialization and
Python decoding of imported navigator histories. It does not replace the JSON
parser, change SQLite persistence, raise resource limits or provide constant-memory
imports. The catalog index remains JSON: its metadata scan occurs outside global
lifecycle locks; only required model files are pinned. A concurrent deletion of a
required model causes a retryable conflict. Private source hard links bound file
descriptors without copying artifact contents under global locks.

Reproduce from the repository root with a fresh benchmark directory:

```sh
mkdir -p work/second-review-baseline
git archive 9b58c884 hnefatafl frontend configs pyproject.toml | tar -x -C work/second-review-baseline
ln -s "$PWD/frontend/node_modules" work/second-review-baseline/frontend/node_modules
npm --prefix work/second-review-baseline/frontend run build
npm --prefix frontend run build
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python scripts/benchmark_job_import_review.py \
  --root work/second-review-imports --baseline-tree work/second-review-baseline \
  --repetitions 3 --training-repetitions 3 --chromium /usr/bin/chromium \
  --output work/second-review-imports.json
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python scripts/benchmark_job_archive_review.py \
  --root work/second-review-resources --baseline-tree work/second-review-baseline \
  --repetitions 3 --descriptors --output work/second-review-resources.json
```

Select an installed Chromium path with `--chromium`. The browser driver uses
existing Playwright dependencies. All credentials are disposable local fixture
accounts; no external account or worker infrastructure is required.

<!-- Consolidated from job-archive-final-review.md -->

## Job archive final compatibility review

[Training data](../../training/models.md#move-data-between-installations)

These changes address the final review of `ab1c7e88`: full import responses are
again the API default; the workshop explicitly requests `?summary=true`; loaded
histories and nested analysis survive summary refreshes; imported summaries retain
bounded best-model IDs for the existing catalog-name search. No archive writer,
extractor, snapshot, checkpoint, SQLite persistence or training-loop code changed.
The [preceding measurements](#job-archive-second-review-measurements) remain historical evidence.

### Results

Fresh measurements on 7 November 2026 use Python 3.12.14, Node 24.19.0, Chromium
154.0.8037.57 and a two-CPU cgroup quota. Values are medians of three samples.
The control is the original full-response implementation at `9b58c884`; the current
column uses the workshop's explicit compact request. This repeats the preceding
full-response comparison to verify its response/memory savings survive API
compatibility restoration. It does not compare two default API requests: current
clients omitting the query intentionally receive the complete job again.
The [raw measurements](./raw/training/job-archive-final-review.json) include all reported samples,
fixture hashes and source hashes.
The measured implementation is `b889b01`; the final follow-ups handle authoritative
live phase counters and omitted between-trial timers during frontend merging,
with focused transition regressions. Archive/import response code is identical,
and these guards apply to live jobs, while all measured imported jobs are terminal.
The reported timings and heap samples were captured before those live-state guards.

| Workload | Full-response POST bytes | Explicit compact POST bytes | Control / current HTTP seconds | Control / current server peak RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| 1,000 iterations | 7,216,056 | 5,805 | 4.406 / 3.401 | 300.8 / 300.6 |
| 34.5 MiB timing report | 50,416,144 | 5,820 | 18.197 / 14.037 | 745.2 / 745.2 |
| 67.6 MiB valid JSON report | 138,336,130 | 5,821 | 34.369 / 27.103 | 1295.8 / 1295.8 |

The workshop continues to receive approximately 5.8 KB regardless of report size.
HTTP completion is 21–23% faster than the full-response control. Peak server RSS
does not improve; reconstruction and SQLite serialization still dominate it.
Full selected histories remain available through one detail GET. The API's default
and explicit `?summary=false` modes preserve complete progress/result histories;
focused tests exercise all three response choices with schema-1/2 historical ZIP
writers and schema-3 TAR.GZ.

| Workload | Control / current initial UI seconds | Control / current observed peak JS heap MiB | Control / current retained JS heap MiB |
| --- | ---: | ---: | ---: |
| 1,000 iterations | 5.697 / 4.837 | 18.2 / 11.8 | 11.9 / 8.9 |
| 34.5 MiB timing report | 22.570 / 18.374 | 56.2 / 26.2 | 43.3 / 24.5 |
| 67.6 MiB valid JSON report | 37.224 / 29.706 | 273.9 / 149.1 | 262.0 / 133.9 |

Each browser sample issued exactly one full-detail GET.
Observed peak JS heap is 53% lower for timing vectors and 46% lower for the large
document; initial UI loading is 15–20% faster. CSS bundle bytes are
identical to both `ab1c7e88` and the control. The workshop keeps its existing
localized loading state, components, responsive layout and ARIA behavior.

| Concurrent-import CPU smoke training | Mean iterations/s | Observed range |
| --- | ---: | ---: |
| No import | 18.949 | 18.844–19.074 |
| Full-response control | 18.246 | 18.073–18.378 |
| Current compact workshop import | 18.207 | 17.728–18.650 |

Current training throughput is 0.2% below the full-response control, within the
observed ranges. Both import cases run approximately 4% below the idle control;
there is no observed material additional training regression in this workload.

### Method and limits

The existing benchmark starts a fresh authenticated Uvicorn process for each API
and browser sample, excluding fixture setup and builds. API client allocations are
separate from server RSS (`VmHWM`). Browser memory is CDP `JSHeapUsedSize` sampled
every 20 ms, with retained heap measured after explicit GC; native/DOM/GPU memory
and browser-process RSS are excluded. UI completion includes a 350 ms network
settling window. Initial setup-overlap samples and samples preceding the final UI
bundle were discarded and rerun before any full test suite; the raw record names
the fixed discarded cases. No timing-based outlier filtering was used.

Each fixture contains a real one-iteration CPU checkpoint with optimizer, replay
and RNG state, plus 1,000 synthetic metric points with distinct iteration numbers.
These are not 1,000 optimizer updates. Current/control exports are byte-identical
for each fixture. Training measurements separately warm up four actual iterations
and time 40 real updates during an import, with three alternating-order samples
and an idle control. These short CPU runs do not establish GPU, large-replay or
shared-disk throughput. Server reconstruction/validation/SQLite serialization still
needs substantial memory; this remains a bounded-document importer rather than a
constant-memory importer. Full selected analysis intentionally occupies browser
memory; preserving previously discarded metadata is required correctness.

Scalar/map summary caches retain their 32 KiB budget. Best-model associations
add only unique 24-character IDs, capped at 2,048 references, the existing archive
member ceiling. Model records and histories are excluded. Missing/deleted models
cannot match unrelated catalog names. A deleted model without a catalog entry
supplies no model-name match of its own. No extraction, upload or document limit changed.

Focused regressions cover 1,200-point nested histories/metrics, complete results,
evaluation/performance/tuning metadata, repeated summaries for terminal and active
jobs, late detail responses, job switching, fresh/deduplicated model imports,
result-only associations and bounded IDs. Full archive/security/resume, admission,
model deletion and cross-filesystem regressions remain in the project suites.

Reproduce after installing the locked dependencies and Chromium:

```sh
mkdir -p work/final-compat-baseline
git archive 9b58c884 hnefatafl frontend configs pyproject.toml | tar -x -C work/final-compat-baseline
ln -s "$PWD/frontend/node_modules" work/final-compat-baseline/frontend/node_modules
npm --prefix work/final-compat-baseline/frontend run build
npm --prefix frontend run build
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 CI=true .venv/bin/python scripts/benchmark_job_import_review.py \
  --root work/final-compat-imports --baseline-tree work/final-compat-baseline \
  --repetitions 3 --training-repetitions 3 --chromium /usr/bin/chromium \
  --output work/final-compat-imports.json
```

Use fresh directories and run benchmarks separately from builds/tests. The driver
requests `?summary=true`; the historical control ignores the unknown query and
retains its full-response contract. To compare only the final patch with the
preceding compact implementation, substitute `ab1c7e88` for the baseline commit;
both workshop cases then use compact responses. All accounts and data are isolated
local fixtures.

<!-- Consolidated from job-archive-security-review.md -->

## Job archive security and identity review

[Training data](../../training/models.md#move-data-between-installations)

This review compares the credential sanitizer at `6b31d9f` with the streaming
replacement. Recognized quoted credentials are removed completely, including
escaped quotes, serialized JSON and values crossing reads. Exported and imported
worker logs share the same scanner with `portable()` diagnostic strings. Absolute
path filtering and archive limits remain in place. This is a heuristic sanitizer;
archives with arbitrary diagnostic data remain potentially sensitive.

Frontend summaries now follow the backend's missing/null run-identity semantics.
An incompatible execution or trial replaces its own reports, while cumulative
tuning history survives child changes. Model attributes merge only within the
same known ID. Imported compact projections reserve identities before optional
metadata, within their existing budget. A stale detail triggers at most one
immediate replacement request; persistent conflicts use the existing localized
error channel. Compatible histories keep their existing lazy-loading behavior.

### Log and diagnostic measurements

Measurements on 7 November 2026 use Python 3.12.14 and a two-CPU cgroup quota.
Each table value is the median of three fresh-process samples, in alternating
before/after order. Tests and builds did not run concurrently. The
[raw record](./raw/training/job-archive-security-review.json) contains all 144 timing samples,
15 separate traced-memory samples, 18 training samples and source hashes.
The measured sources are from `642170a`. Subsequent test synchronization and
the guard retaining identities when optional empty maps exceed the summary
budget do not change the redactor or the measured fixture projections.

| Streamed log | 1 MiB before / after seconds | 8 MiB before / after seconds | 64 MiB before / after seconds |
| --- | ---: | ---: | ---: |
| Single ordinary word | 0.065 / 0.054 | 0.544 / 0.440 | 4.233 / 3.484 |
| Ordinary metric lines | 0.091 / 0.059 | 0.733 / 0.453 | 5.806 / 3.612 |
| Repeated `password` without assignment | 0.065 / 0.021 | 0.534 / 0.163 | 4.250 / 1.346 |
| One quoted credential spanning the log | 0.066 / 0.018 | 0.530 / 0.133 | 4.278 / 1.013 |
| Every line a quoted credential | 0.086 / 0.125 | 0.708 / 1.023 | 5.631 / 7.833 |

Nonsecret stream outputs have identical SHA-256 hashes before/after. All new
credential samples remove the synthetic secret; all corresponding baseline
samples leak it. Ordinary 64 MiB metric logs improve by 38%. Credential-dense
64 MiB logs cost 39% more time to sanitize completely (about 8.2 MiB/s). Both
ordinary and adversarial workloads scale approximately linearly; stronger
redaction does not improve every workload.

| Streamed input | Traced peak at 1 / 8 / 64 MiB, KiB | Process peak RSS range, MiB |
| --- | ---: | ---: |
| Ordinary long line | 386.5 / 387.1 / 389.6 | 21.5–21.6 |
| One long quoted value | 258.1 / 262.8 / 261.2 | 21.2–21.4 |

Reads are bounded to 65,536 characters. Only bounded suffixes, quote/escape
counters and a capped credential-container stack cross reads. Complete source
lines and secret values are never retained. The log output also remains subject
to the existing per-artifact byte cap, including redaction-marker expansion.

| Resident diagnostic string | 1 MiB before / after seconds | 8 MiB before / after seconds | 64 MiB before / after seconds |
| --- | ---: | ---: | ---: |
| Ordinary word | 0.068 / 0.052 | 0.502 / 0.415 | 4.185 / 3.333 |
| Repeated `password`, no assignment | 0.065 / 0.064 | 0.500 / 0.509 | 4.079 / 4.248 |
| One quoted credential | 0.065 / 0.028 | 0.523 / 0.228 | 4.235 / 1.752 |

`clean_text()` necessarily receives an already resident string and returns a
string; it is not a constant-memory JSON importer. Additional traced allocation
is approximately 1.1 KiB for ordinary words, 2.0 KiB for the repeated keyword and
130.5–145.8 KiB for the collapsing quoted value. Whole-process RSS for these
64 MiB string workers reaches 149 MiB, including fixture reading and the output
hash sink. Tracing is separate from timing; hashes, encoding and sink work are
included equally in stream timings. Timing workers do not retain output logs.

### Concurrent training and archive compatibility

The fixture contains a real CPU checkpoint with optimizer/replay/RNG state and
1,000 synthetic historical iterations, plus an 8 MiB ordinary worker log. It does
not represent 1,000 optimizer updates. Before/after exports have identical bytes
and SHA-256 hashes. Training independently warms up four actual iterations and
times 40 actual updates per sample, with three alternating-order samples.

| Concurrent workload | Idle mean iterations/s | Baseline mean (range) | Current mean (range) |
| --- | ---: | ---: | ---: |
| Export | 18.948 | 18.764 (18.183–19.201) | 19.117 (18.726–19.736) |
| Import | 19.069 | 17.789 (16.885–18.358) | 18.211 (17.610–18.542) |

Current rates are 1.9% and 2.4% above the baseline, with overlapping sample
ranges; these short samples establish no additional observed throughput
regression, not a statistically proven training speedup. Current imports still
run about 4.5% below their idle control. Median concurrent export time is
2.555 → 2.280 seconds; import HTTP completion is 4.281 → 4.094 seconds. Compact
workshop responses remain small (5,817 → 5,757 bytes); the default full-response
API is unchanged. No training, inference or remote-worker protocol changed.
GPU workloads, large replay buffers and shared disks were not measured here.

Regression suites retain schema-3 round trips, authentic schema-1/2 ZIP imports,
reports exceeding 16 MiB, full checkpoint resumption, frozen dependencies,
model-deletion snapshots, bounded cross-filesystem descriptors, cancellation,
rollback and extraction/admission security. New tests cover quoted and malformed
diagnostics, Unicode, escape/buffer boundaries, real archive log contents,
identity budgets, legacy/modern trial transitions, model removal, pending detail
responses and job switching. Desktop/mobile browser coverage checks removal of
stale graphs and the existing accessible loading indicator. Historical
large-import response and browser-memory measurements above
remain separate evidence; browser memory was not rebenchmarked for this patch.

### Reproduction

Use the locked dependencies and a fresh directory, separately from tests/builds:

```sh
mkdir -p work/security-review-baseline
git archive 6b31d9f hnefatafl pyproject.toml | tar -x -C work/security-review-baseline
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 CI=true \
  HNEFATAFL_DATA_DIR="$PWD/work/security-review-data" \
  .venv/bin/python scripts/benchmark_archive_redaction.py \
  --root work/security-review-benchmark --baseline-tree work/security-review-baseline \
  --output work/security-review-benchmark.json --repetitions 3 --training-repetitions 3
```

All accounts, jobs and credentials are synthetic local fixtures. RSS is the
process high-water mark (`ru_maxrss`); traced peaks cover sanitizer work only.
The benchmark preserves optional plain diagnostic data and asserts that
nonsecret archive bytes remain unchanged. Arbitrary encodings, unlabeled
secrets and credentials in unrelated artifacts are outside the recognizer's
guarantees; inspect archives before sharing them.
