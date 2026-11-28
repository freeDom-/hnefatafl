# Benchmarks

Reports are grouped by the feature they measure. Raw seed-level reports live under
[`raw/`](raw/) and are retained even when a narrative report summarises them.
Historical results keep their original revision, host, model and sample.

## Search

- [Alpha-beta search, symmetry, move ordering and root-window pruning](search-alpha-beta.md)
- [Parallel self-play search processes](search-parallel.md)
- [Browser/native MCTS measurements](search/mcts-phase2.md)

## Training

- [Self-play and evaluation optimizations](training-selfplay-optimizations.md)
- [Live training monitoring overhead](training-monitoring.md)
- [Portable job-archive compatibility and performance](training-job-archives.md)
- [Schedule-Free AdamW CPU measurements](training/schedulefree-adamw.md)
- [Online learning-rate adaptation overhead](training/online-adaptation.md)
- [Champion-adaptation controller](training/champion-adaptation.md)
- [Learning metrics and dashboards](training/learning-metrics.md)
- [Win-streak progression fast path](training/progression-streaks.md)
- [Completed-trial bookkeeping](training/tuning-bookkeeping.md)

## Inference, symmetry and caching

- [D4 training symmetry and exact-input caching](inference/symmetry-and-caching.md)
- [Optional OpenVINO](inference/openvino.md)

## Browser and backend

- [Backend read workload](browser/backend-read.md)
- [Frontend feature splitting and initial bundle](browser/frontend-bundle.md)
- [Browser-local analysis phases and evidence](../browser-analysis/README.md)

## Agents

- [Shield maiden and agent comparisons](../shield-maiden.md)

## Comparability

A report is comparable only when the revision, hardware, software, model, ruleset,
budget, batch/concurrency, side assignment and game/ply caps are compatible. Do not
compare CPU and GPU throughput, fixed-depth and fixed-time strength, or smoke and
competitive models as if they were the same measurement. See
[methodology](../methodology.md) and
[performance](../../training/performance.md#benchmark-protocol-and-evidence).
