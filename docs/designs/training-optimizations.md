# Training optimization integration

Integration target: `feat/training-optimizations`, initially main
`5a343668411033ed3283ed51e01323454975723a`. Each implementation is developed and
measured independently against that application baseline. This branch initially
contains shared measurement tooling and this checklist; open feature PRs remain
separate for review. PR merges and a production release are separate actions.

| Priority | Branch | Review |
| --- | --- | --- |
| 1: model-aware batched evaluation | `perf/batched-evaluation` | [PR 37](https://github.com/freeDom-/hnefatafl/pull/37) |
| 2: pure-engine fast paths | `perf/engine-fast-paths` | [PR 38](https://github.com/freeDom-/hnefatafl/pull/38) |
| 3: persistent CPU search actors | `perf/parallel-search-workers` | [PR 39](https://github.com/freeDom-/hnefatafl/pull/39) |
| 4: packed replay and CUDA prefetch | `perf/replay-pipeline` | [PR 40](https://github.com/freeDom-/hnefatafl/pull/40) |
| 5: lazy Jarl successor groups | `perf/lazy-jarl-successors` | [PR 41](https://github.com/freeDom-/hnefatafl/pull/41) |

The shared [benchmark runner](../../scripts/benchmark_training_optimization.py)
accepts an extracted baseline package via `--source`, selected feature, device,
ruleset and explicit baseline/candidate variant. Check out the corresponding
feature branch for candidate measurements: unsupported candidates fail before
timing. Run warm-ups and alternating fresh-process pairs on otherwise idle
hardware; retain all samples, source fingerprints, actual work and numerical
parity evidence. Feature reports under `docs/benchmarks/` arrive with their PRs.

Integration acceptance includes all five independent PR checks and a combined
preview, legal game/search behavior, model ownership, paired seeds and ordered
replay, optimizer/RNG/checkpoint parity, cancellation and actor failure cleanup,
legacy-worker negotiation, config import and resume controls. Combined validation
does not replace the isolated performance measurements or imply additive gains.
All feature PRs target this branch. A draft aggregate PR can target main so its
checks rerun as reviewed changes are integrated.

Current measured scope is CPU. CUDA/OpenVINO GPU and MPS execution/performance
require target hardware; skipped hardware tests are recorded explicitly. Packed
CPU throughput is mixed, so automatic CPU/MPS mode retains legacy collation.
Multiprocessing remains opt-in because startup, RAM and scheduling overhead can
outweigh steady-state gains. Fixed-depth Jarl equivalence establishes search
correctness; timed play and model strength require separate evidence.
