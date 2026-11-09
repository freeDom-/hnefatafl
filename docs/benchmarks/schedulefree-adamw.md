# Schedule-Free AdamW CPU measurements

[Training guide](../TRAINING.md#schedule-free-adamw) · [Raw measurements](schedulefree-adamw.json)

Measured 2026-11-09 on Linux x86_64, AMD EPYC 9V74, Python 3.12.14,
PyTorch 2.14.1+cpu, schedulefree 1.4.1; two PyTorch/OMP/MKL threads.
CUDA and MPS were unavailable. These are short runtime/memory measurements on a
shared cloud CPU, not playing-strength evidence or a general speedup claim.

Reproduce from the repository root:

```sh
.venv/bin/python scripts/benchmark_schedulefree.py --output work/schedulefree-benchmark.json
```

Each optimizer/architecture runs in a fresh subprocess, with seed 0, the same
initial model, 256 identical replay inputs/targets, batch 32 and two epochs.
Three repetitions execute 16 updates each (48 actual updates per case).
Optimization is the median repetition, including the Schedule-Free train/eval
transition; it excludes self-play, evaluation and serialization. Both use base
rate 0.001 and weight decay 0.0001; Schedule-Free warm-up is 32 updates.

Iteration time covers the first repetition plus real identical-budget self-play
(two four-ply games, two MCTS visits/move, five-second ceilings), replay collation
and two two-ply evaluation games. Each case generates eight self-play positions;
search deadlines are not the intended budget limit. These are bounded benchmark
iterations rather than full recommended training runs. A separate 100-pair loop
measures idempotent lifecycle train/eval overhead after optimization.

| Channels/blocks | Optimizer | Optimization / 16 updates (s) | Iteration (s) | Train/eval pair (ms) | Peak RSS (MiB) | Checkpoint (MiB) | Resume (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4/0 | AdamW | 0.0706 | 0.1170 | 0.0001 | 336.0 | 12.403 | 1.156 |
| 4/0 | Schedule-Free AdamW | 0.0452 | 0.0856 | 0.0586 | 336.5 | 12.399 | 1.109 |
| 32/2 | AdamW | 0.1187 | 0.1534 | 0.0001 | 338.3 | 13.047 | 1.069 |
| 32/2 | Schedule-Free AdamW | 0.1136 | 0.1566 | 0.1149 | 338.5 | 13.040 | 1.028 |
| 64/4 | AdamW | 0.4488 | 0.5038 | 0.0001 | 346.7 | 17.067 | 1.229 |
| 64/4 | Schedule-Free AdamW | 0.4661 | 0.4578 | 0.3093 | 346.9 | 17.058 | 1.086 |
| 128/8 | AdamW | 2.1118 | 2.7891 | 0.0001 | 419.4 | 48.749 | 1.071 |
| 128/8 | Schedule-Free AdamW | 2.2493 | 2.4119 | 0.7034 | 420.0 | 48.734 | 1.280 |

The checkpoint workload stores 8,000 independently cloned Brandubh replay
positions (recommended capacity), evaluation weights, the full optimizer,
portable selected-model/benchmark snapshots and RNG/scaler fields. It uses the
existing tensor-only format and unchanged 128 MiB limit. All measured cases fit,
including the maximum 128-channel/eight-block architecture at about 49 MiB.
Replay action-list lengths and selection/history retention vary in real jobs;
this measurement does not guarantee every arbitrary full configuration fits.
No global limit change or special checkpoint-size exception is needed here.

Schedule-Free stores two parameter-sized buffers (`z` and `exp_avg_sq`), matching
AdamW's `exp_avg`/`exp_avg_sq` tensor footprint: approximately 0.310 MiB at 32/2,
2.311 MiB at 64/4 and 18.137 MiB at 128/8. Differences in scalar counters and
serialization are small. Process RSS includes the Python/PyTorch runtime, model,
optimizer, replay creation and safe checkpoint load; it is not incremental tensor
memory. GPU memory is recorded as null, not inferred from CPU measurements.
The raw JSON also records individual repetitions, self-play/evaluation times,
checkpoint-write time, parameter counts and optimizer tensor bytes.

The short runs show modest switching costs and similar memory footprints on this
host. Timing differences include noise and cold-state effects; longer controlled
runs and actual CUDA/MPS hardware are needed for hardware-specific conclusions.
No strength improvement is established by this benchmark.
