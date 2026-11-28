# CUDA GPU measurements (RTX 3060)

[Benchmarks index](../README.md) · [Performance guide](../../../training/performance.md) · [Raw GPU evidence](../raw/training/training-gpu-rtx3060.json)

These measurements are historical and host-specific. They do not establish GPU
performance on another device or playing strength.

## Host and workload

The CPU diagnostics for [batched evaluation](../training-selfplay-optimizations.md)
and [engine fast paths](../training-selfplay-optimizations.md) could not show GPU
behavior, so both changes were re-measured on CUDA. Host: NVIDIA GeForce RTX 3060
(12 GB) under Windows 11/WSL2 Ubuntu, PyTorch 2.14.1+cu126 (CUDA runtime 12.6),
float32 with mixed precision disabled, Brandubh 7×7 and the recommended
32-channel/2-block network. Workloads were 64 evaluation games (64 visits, 32-ply
cap) and 64 batched self-play games (128 visits, 150-ply cap, concurrency 64,
inference batch 64, exact-input cache off, seed 0). The baseline is unoptimized main
`5a343668411033ed3283ed51e01323454975723a`; each candidate is the isolated PR head.
Every comparison is three independent baseline/candidate pairs with alternating
execution order, one fresh process per run and one discarded warm-up sample per
process. Speedup is the median baseline duration divided by the median candidate
duration. Seeds, model initialization and workload fingerprints are fixed, and CUDA
is synchronized at measurement boundaries. Raw samples with configurations,
environment and per-file source hashes are in the
[GPU evidence JSON](../raw/training/training-gpu-rtx3060.json). These are benchmark
workloads, not complete training runs or trained-model strength evaluations.

### Batched neural evaluation (PR #37)

[PR #37](https://github.com/freeDom-/hnefatafl/pull/37) replaces one-game-at-a-time
neural evaluation with cooperative batching across independent evaluation games;
candidate and Champion models keep separate inference queues. Candidate runs request
batched mode with concurrency 64 and inference batch 64; baseline runs stay
sequential.

| Opponent | Sequential median | Batched median | Speedup | Matching work hashes |
| --- | ---: | ---: | ---: | ---: |
| Shieldbearer | 138.119 s | 14.234 s | 9.70× | 3/3 |
| Champion | 236.020 s | 42.107 s | 5.61× | 0/3 |

Individual measurements in seconds: Shieldbearer sequential 191.051 / 85.024 /
138.119 and batched 14.234 / 11.666 / 17.553; Champion sequential 301.709 /
234.852 / 236.020 and batched 43.464 / 42.107 / 41.122.

Shieldbearer evaluation produced the same game-summary work hash in all six
measurements. **Champion evaluation did not**: every sequential run hashed
`72c363e44a65cae0…` and every batched run hashed `44cd8b71f5b000f8…` (16-character
prefixes). The hashes cover game outcomes, sides, seeds, plies and termination
reasons, not complete move sequences, so the Champion timings establish a throughput
improvement only. They do **not** establish equivalent game outcomes, identical
search trajectories, equivalent evaluation scores or equivalent promotion
decisions. Numerical differences between batched and single-position CUDA execution
and differences in MCTS scheduling are both plausible causes, and neither has been
isolated yet. Treat the Champion result as unresolved until the comparison is
repeated with outcome-level verification, and do not describe it as identical-work
or behaviorally equivalent.

### Engine fast paths in GPU self-play (PR #38)

[PR #38](https://github.com/freeDom-/hnefatafl/pull/38) optimizes CPU-side engine
operations: an allocation-free legal-move existence check, cached immutable geometry
and an encirclement early exit. They were measured end-to-end in the same batched
CUDA self-play workload:

| Pair | Original engine | Optimized engine | Paired speedup |
| --- | ---: | ---: | ---: |
| 1 | 95.377 s | 95.433 s | ~1.00× |
| 2 | 141.336 s | 133.858 s | 1.06× |
| 3 | 128.702 s | 104.461 s | 1.23× |
| Median | 128.702 s | 104.461 s | 1.232× |

All six measurements recorded the same work hash, which supports equivalent
generated training examples and search work in these scenarios. Timing variation is
large and one pair showed effectively no improvement, so 1.232× is the ratio of
medians and not a guaranteed per-run gain; the median of the three paired ratios is
about 1.056×, the more conservative reading. The engine changes are CPU-side, and
they reduce end-to-end time here only because neural inference runs on CUDA.

### Execution stability and CUDA version

A 50-iteration training run on the combined PR #37/#38 branch completed without CUDA
inference stalls on the same host with CUDA 12.6 and float32. That is a stability
observation, not a benchmark: it has no isolated baseline and does not establish
long-term stability across environments. Earlier testing with a CUDA 13.2 PyTorch
build showed intermittent inference stalls, which is why this evidence uses CUDA
12.6; do not generalize it to other CUDA runtimes without testing.

### What these GPU numbers do not show

They measure the evaluation phase, end-to-end self-play generation and, for the
engine fast paths, the CPU work inside it. They exclude optimizer updates,
checkpointing, evaluation cadence and complete training throughput, and they do not
show improved playing strength or confirmed Champion decision equivalence.
