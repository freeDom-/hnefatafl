# Job archive security and identity review

[Training data](../TRAINING_DATA.md#moving-data-between-installations)

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

## Log and diagnostic measurements

Measurements on 7 November 2026 use Python 3.12.14 and a two-CPU cgroup quota.
Each table value is the median of three fresh-process samples, in alternating
before/after order. Tests and builds did not run concurrently. The
[raw record](job-archive-security-review.json) contains all 144 timing samples,
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

## Concurrent training and archive compatibility

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
[large-import response and browser-memory measurements](job-archive-final-review.md)
remain separate evidence; browser memory was not rebenchmarked for this patch.

## Reproduction

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
