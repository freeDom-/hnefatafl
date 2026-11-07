# Job archive final compatibility review

[Training data](../TRAINING_DATA.md#moving-data-between-installations)

These changes address the final review of `ab1c7e88`: full import responses are
again the API default; the workshop explicitly requests `?summary=true`; loaded
histories and nested analysis survive summary refreshes; imported summaries retain
bounded best-model IDs for the existing catalog-name search. No archive writer,
extractor, snapshot, checkpoint, SQLite persistence or training-loop code changed.
The [preceding measurements](job-archive-second-review.md) remain historical evidence.

## Results

Fresh measurements on 7 November 2026 use Python 3.12.14, Node 24.19.0, Chromium
154.0.8037.57 and a two-CPU cgroup quota. Values are medians of three samples.
The control is the original full-response implementation at `9b58c884`; the current
column uses the workshop's explicit compact request. This repeats the preceding
full-response comparison to verify its response/memory savings survive API
compatibility restoration. It does not compare two default API requests: current
clients omitting the query intentionally receive the complete job again.
The [raw measurements](job-archive-final-review.json) include all reported samples,
fixture hashes and source hashes.
The measured implementation is `b889b01`; the final follow-up only replaces
authoritative live phase-counter maps during frontend merging, with a focused
transition regression. Archive/import response code is identical. The reported
timings and heap samples were captured before that counter-only follow-up.

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

## Method and limits

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
