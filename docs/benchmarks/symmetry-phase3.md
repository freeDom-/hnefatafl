# Phase 3 evidence: D4 training augmentation

[Documentation index](../README.md) · [Performance](../PERFORMANCE.md#phase-3-training-symmetry-augmentation)

Everything here was produced by `python -m hnefatafl.cli benchmark-symmetry`
(`hnefatafl/symmetry_benchmark.py`) from the checked-in configurations. Raw JSON
is kept beside this file. Reproduce with:

```sh
.venv/bin/python -m hnefatafl.cli benchmark-symmetry \
  --config docs/benchmarks/symmetry-phase3-cpu-config.json --output /tmp/cpu.json
.venv-cuda/bin/python -m hnefatafl.cli benchmark-symmetry \
  --config docs/benchmarks/symmetry-phase3-cuda-config.json --output /tmp/cuda.json
```

| File | Content |
| --- | --- |
| `symmetry-phase3-cpu-config.json`, `symmetry-phase3-cpu.json` | CPU consistency, throughput, held-out learning and paired strength, all three rulesets |
| `symmetry-phase3-cuda-config.json`, `symmetry-phase3-cuda.json` | RTX 3060 FP32 optimizer throughput |
| `symmetry-phase3-cuda-amp-config.json`, `symmetry-phase3-cuda-amp.json` | RTX 3060 mixed-precision optimizer throughput |

## Environment and caveats

- Linux/WSL2 on 12 logical CPUs, PyTorch 2.14.1 (CPU wheel for the CPU run,
  `+cu130` for CUDA), 4 CPU threads, one NVIDIA GeForce RTX 3060.
- During the CPU run an unrelated remote-worker training job was consuming about one
  core on the same machine, so CPU timings are noisy; per-repetition ratios are
  reported rather than only medians. The CUDA runs began after that job was stopped.
- Models are tiny (16 channels, 1 block), searches are 16 visits and games are capped
  at 80 plies. The numbers show mechanism and cost, **not** the effect on a
  production-size network or on playing strength.
- Each ruleset was run once with fixed seed 20260901; learning rows are three
  replicates that differ in initial weights. Replay (16 games) and held-out positions
  (6 games, different seeds and games) come from the same untrained network.

## A. Geometric consistency (what augmentation changes)

For each fixed position (the real opening plus seeded legal-play positions, 96 per
ruleset) the network evaluates P and all seven T(P); policies are mapped back to P's
orientation and compared on legal moves (total variation distance); values are
compared directly. “Top-move disagreement” excludes near-ties (gap ≤ 1e-6).
Trained for 12 epochs on one frozen 16-game replay; mean over 3 replicates.

| Ruleset | Mode | Policy TV distance | Mean value difference | Max value difference | Top-move disagreement |
| --- | --- | ---: | ---: | ---: | ---: |
| Brandubh | untrained | 0.277 | 0.0020 | — | — |
| Brandubh | off | 0.538 | 0.0327 | 0.289 | 0.94 |
| Brandubh | random_d4 | **0.117** | **0.0022** | **0.017** | **0.79** |
| Tablut | untrained | 0.255 | 0.0016 | — | — |
| Tablut | off | 0.473 | 0.0068 | 0.098 | 0.96 |
| Tablut | random_d4 | **0.088** | **0.0010** | **0.010** | **0.71** |
| Copenhagen | untrained | 0.255 | 0.0012 | — | — |
| Copenhagen | off | 0.419 | 0.0021 | 0.016 | 0.98 |
| Copenhagen | random_d4 | **0.065** | **0.0008** | **0.009** | **0.73** |

Augmentation reduces the policy gap by 4.6× (Brandubh), 5.4× (Tablut) and 6.5×
(Copenhagen) relative to unaugmented training of the same replay. Without it,
training on one orientation makes the network *less* consistent than at
initialisation. Consistency is not equivariance: top-move disagreement stays at
about 70–80%, so predictions for rotated positions are still not interchangeable
and are never shared by inference or search. Copenhagen's value targets are ≈0 in this
small run (value loss rounds to 0.000, consistent with games hitting the 80-ply cap and
being bootstrapped from an untrained network; the evaluation games likewise mostly
ended unfinished), so its value rows are uninformative.

## B. Held-out losses (what the network learned)

Mean policy / value loss on positions from games that were **never** in the
optimization replay. “All orientations” scores each held-out position under all
eight transforms.

| Ruleset | Mode | Held-out policy, original | Held-out policy, all 8 | Held-out value, original | Training-set policy |
| --- | --- | ---: | ---: | ---: | ---: |
| Brandubh | off | 3.758 | 3.982 | 0.879 | 2.725 |
| Brandubh | random_d4 | **3.476** | **3.476** | 0.914 | 3.431 |
| Tablut | off | 4.406 | 4.584 | 1.120 | 3.512 |
| Tablut | random_d4 | **4.196** | **4.202** | 1.122 | 4.167 |
| Copenhagen | off | 4.774 | 4.957 | 0.000 | 4.173 |
| Copenhagen | random_d4 | **4.636** | **4.635** | 0.000 | 4.625 |

With the same replay, weights, batches, steps and seed, augmentation raises
training-set policy loss (it fits less of one orientation) and lowers held-out
policy loss on both the original and rotated positions, in every replicate of every
ruleset (3/3 each). Unaugmented training overfits the small replay (training 2.7 vs
held-out 3.8 in Brandubh). Held-out value loss is not consistently different: Brandubh
changes by +0.042, −0.148 and +0.213 across the three replicates (mean 0.879 →
0.914, noise-dominated); Tablut changes by +0.021, −0.007, −0.006; Copenhagen by 0. These
networks are essentially untrained, so this shows regularisation in an overfitting
regime. It does not show the same advantage with a large replay, and it is not a
strength measurement.

## C. Optimizer throughput and memory

Same frozen replay, same initial weights, same batches and update counts, order
alternated each repetition, one discarded warm-up. Median optimization wall time:

| Device | Ruleset | off (s) | random_d4 (s) | Ratio | Augmentation share | Per-repetition ratios |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| CPU | Brandubh | 3.110 | 3.237 | 1.041 | 1.8% | 0.87, 1.00, 1.13 |
| CPU | Tablut | 4.616 | 4.578 | 0.992 | 1.7% | 0.96, 0.98, 1.09 |
| CPU | Copenhagen | 12.638 | 12.298 | 0.973 | 1.8% | 1.36, 1.10, 0.95 |
| CUDA FP32 | Brandubh | 2.076 | 1.955 | 0.942 | 1.8% | 1.11, 1.12, 0.95, 0.92, 1.02 |
| CUDA FP32 | Tablut | 2.196 | 2.303 | 1.049 | 1.9% | 1.08, 0.96, 1.08, 1.00, 1.09 |
| CUDA FP32 | Copenhagen | 6.444 | 6.529 | 1.013 | 1.8% | 0.97, 0.88, 0.92, 0.92, 1.24 |
| CUDA AMP | Brandubh | 2.462 | 2.736 | 1.111 | 1.4% | 1.06, 1.12, 1.07, 1.10, 1.02 |
| CUDA AMP | Tablut | 2.679 | 3.066 | 1.144 | 1.5% | 0.94, 0.96, 1.10, 1.29, 1.14 |
| CUDA AMP | Copenhagen | 7.788 | 8.049 | 1.034 | 1.5% | 1.17, 1.16, 1.02, 1.01, 1.04 |

Directly measured augmentation work is **1.4–1.9%** of optimization time in every
case. Ratio medians scatter between 0.94 and 1.14 with ranges that straddle 1.0 in
most rows, so end-to-end overhead is “within measurement noise, plausibly up to
about +10% on CUDA mixed precision”; only the 1.4–1.9% is a clean measurement.
Augmentation is host-side: no extra device kernels or copies are added. Process RSS
was unchanged (326→326, 338→343, 367→367 MiB; replay is not multiplied) and peak
GPU allocation was identical (21, 29 and 41 MiB) for both modes. Steps and sample
counts were identical.

## D. Paired strength (functional smoke only)

Two independently trained 4-iteration, 8-game models per replicate (off and
random_d4, same seed and settings), then 16 side-swapped, matched-seed games per
replicate, 2 replicates, treatment = random_d4:

| Ruleset | Wins | Losses | Draws | Unfinished | Score of completed games (Wilson 95%) |
| --- | ---: | ---: | ---: | ---: | --- |
| Brandubh | 14 | 16 | 2 | 0 | 0.469 (0.309–0.636) |
| Tablut | 18 | 10 | 3 | 1 | 0.629 (0.454–0.776) |
| Copenhagen | 2 | 7 | 0 | 23 | 0.222 (0.063–0.547), 72% unfinished |

Every interval contains 0.5 and the games are not independent of training seed, so
**no strength change is established, in either direction**. Copenhagen is
dominated by unfinished games (reported separately from rule draws) and is not
informative. This section only demonstrates that the pipeline trains, compares and
plays matched models.

## E. What was not measured

No production-size training run, no multi-seed strength study and no larger replay.
The suggested experiment is in [PERFORMANCE.md](../PERFORMANCE.md#recommended-experiment).
