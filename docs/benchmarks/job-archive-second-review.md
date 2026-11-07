# Job archive second-review measurements

[Training data](../TRAINING_DATA.md#moving-data-between-installations)

These measurements compare the remaining review fixes with PR #58 at
`9b58c884210456d9d487edc1a8efd4a7f5d0df33`. Both use schema-3 TAR.GZ with gzip
level 1 and identical validation limits. The [previous review](job-archive-review.md)
and [original migration](job-archives.md) measurements remain historical evidence.

## Results

Measured on 7 November 2026 with Python 3.12.14, Node 24.19.0, Chromium
154.0.8037.57, three reported CPUs and a two-CPU cgroup quota. Times and memory
values below are medians of three samples. The [raw measurements](job-archive-second-review.json)
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

## Methodology and reproduction

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
