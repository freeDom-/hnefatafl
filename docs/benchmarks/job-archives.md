# Portable job archive benchmarks

[Training data](../TRAINING_DATA.md#moving-data-between-installations)

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
[raw measurements](job-archives.json) retain all samples and source hashes. JSON
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

### Compression level

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

### Concurrent training

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
