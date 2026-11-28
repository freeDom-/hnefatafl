# Training evaluation cost

[Benchmarks index](../README.md) · [Performance guide](../../../training/performance.md)

## Measured evaluation cost

Training runs one selected evaluator, rather than baseline plus previous-best.
Use `evaluation_frequency` independently of checkpoint cadence and inspect
`evaluation_seconds` and each evaluation record's `duration`. Jarl uses the real
alpha-beta search and its own depth/deadline; it may cost more than neural play.
Champion uses equal neural budgets on both models. `evaluation_mode` selects
`auto`, `sequential` or `batched`; auto batches CUDA and keeps PyTorch CPU/MPS
sequential. `evaluation_concurrency` (default 16) and `evaluation_batch_size`
(default 32) are independent of self-play settings. Each model has its own queue
and cache. Evaluation stays FP32. `evaluation_inference_backend: training` opts
into a fresh post-update OpenVINO graph when that backend is selected; default
`pytorch` preserves the historical numerical backend. Alternate backend results
use a separate benchmark identity. See [isolated evaluation evidence](../training-selfplay-optimizations.md).
Workers advertise `evaluation_batching`; legacy workers retain the sequential
default and do not receive unknown execution keys, while explicit new modes
require that capability. Resume downloads apply the same filtering.
Queue waiting counts against wall deadlines, so compare completed search work
and cutoff counts on the target worker before choosing concurrency.

CLI logs project bounded incremental summaries while persistent JSON retains
complete history. This removes repeated history/game serialization from worker
logs; it does not remove history from checkpoint or progress APIs. Wall-clock
phase times remain distinct from overlapping activity counters, with no extra
CUDA synchronization added to ordinary inference.
