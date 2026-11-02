# Training optimization integration

Integration target: `main`, after the batched-evaluation, engine-fast-path, lazy-Jarl
and monitoring PRs merged. Each implementation is developed and measured
independently against that application baseline. Open feature PRs remain separate for
review. PR merges and a production release are separate actions.

| Priority | Branch | Review |
| --- | --- | --- |
| 1: model-aware batched evaluation | `perf/batched-evaluation` | [PR 37](https://github.com/freeDom-/hnefatafl/pull/37) |
| 2: pure-engine fast paths | `perf/engine-fast-paths` | [PR 38](https://github.com/freeDom-/hnefatafl/pull/38) |
| 3: persistent CPU search actors | `perf/parallel-search-workers` | [PR 39](https://github.com/freeDom-/hnefatafl/pull/39) |
| 4: packed replay and CUDA prefetch | `perf/replay-pipeline` | [PR 40](https://github.com/freeDom-/hnefatafl/pull/40) |
| 5: lazy Jarl successor groups | `perf/lazy-jarl-successors` | [PR 41](https://github.com/freeDom-/hnefatafl/pull/41) |

PR 39 was redesigned after GPU measurement showed its first implementation bounded
game concurrency by the process count. The worker count now sizes CPU parallelism
only; concurrency, batch limits and the ordered replay window stay independent, and
every path shares one cooperative scheduler. See
[parallel search](../benchmarks/training-parallel-search.md) for the corrected CPU and
GPU evidence and the retained first-implementation report.

A follow-up review pass corrected five further issues in that redesign: the pool no
longer retains a copy of every completed result, worker capacities come from a
quotient/remainder split so the configured concurrency is a hard upper bound, an
explicit `sequential` execution mode overrides the worker pool, CUDA evaluation
benchmarks compare against the batched evaluator, and evaluation inference backends are
closed exactly once through an exit stack even when a later backend fails to initialize
or refresh. Each correction carries a regression test; see
[parallel search](../benchmarks/training-parallel-search-redesign.md).

The shared [benchmark runner](../../scripts/benchmark_training_optimization.py)
accepts an extracted baseline package via `--source`, selected feature, device,
ruleset, worker count, budgets and explicit baseline/candidate variant. Check out the
corresponding feature branch for candidate measurements: unsupported candidates fail
before timing. Run warm-ups and alternating fresh-process pairs on otherwise idle
hardware; retain all samples, source fingerprints, actual work and numerical parity
evidence. Feature reports under `docs/benchmarks/` arrive with their PRs.

Integration acceptance includes all five independent PR checks and a combined
preview, legal game/search behavior, model ownership, paired seeds and ordered
replay, optimizer/RNG/checkpoint parity, cancellation and actor failure cleanup,
legacy-worker negotiation, config import and resume controls. Combined validation
does not replace the isolated performance measurements or imply additive gains.
A draft aggregate PR can target main so its checks rerun as reviewed changes are
integrated.

Current measured scope is CPU plus a single RTX 3060 CUDA host. MPS execution and
performance require target hardware; skipped hardware tests are recorded explicitly.
Packed CPU throughput is mixed, so automatic CPU/MPS mode retains legacy collation.
Multiprocessing remains opt-in because startup, RAM and scheduling overhead can
outweigh steady-state gains. Fixed-depth Jarl equivalence establishes search
correctness; timed play and model strength require separate evidence.
