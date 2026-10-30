# Parallel search processes

Independent comparison against main `5a343668411033ed3283ed51e01323454975723a`.
The [raw report](training-parallel-search.json) records every sample, source hash,
work fingerprint, environment and startup-containing warm-up. No other tests or
benchmarks ran during collection. Three fresh-process pairs alternate order.

The candidate uses a persistent two-process pool and one math thread per actor;
the parent/baseline also uses one math thread. CPU actors bind frozen CPU weights.
GPU/OpenVINO inference stays in the parent, with separate model queues/caches.
CPU caches are per actor/model and never cross a weight generation. Ordered
replay is bounded behind a slow earlier game. Default `search_workers: 0` keeps
the existing scheduler. Workers advertise `parallel_search`; legacy workers can
claim disabled/default jobs without the unknown key, but cannot claim a positive
worker count. Resume downloads apply the same legacy-config filtering.

The recommended networks use eight 16-ply/32-visit evaluation games, or four
eight-ply/32-visit self-play games; fixed seed/weights, FP32, 300-second ceilings.
Each process discards a startup-containing warm-up, then measures one sample
with the pool alive. A training run reuses its pool across iterations/phases.

| Ruleset | Evaluation before/after median seconds | Evaluation speedup | Self-play before/after median seconds | Self-play speedup |
| --- | ---: | ---: | ---: | ---: |
| Brandubh | 0.9346 / 0.5778 | 1.62x | 0.5655 / 0.5179 | 1.09x |
| Tablut | 2.1420 / 1.6559 | 1.29x | 0.9745 / 1.0704 | 0.91x |
| Copenhagen | 9.1726 / 5.3326 | 1.72x | 2.4905 / 2.0561 | 1.21x |

Every recorded work hash matches. Evaluation hashes cover outcomes/sides/seeds;
self-play hashes also cover encoded inputs and policy targets. These bounded
games remain unfinished when capped. The Tablut self-play median regresses about
10%, with overlapping sample ranges: no speedup is established there. This is why
the setting is opt-in. Cold startup can outweigh the steady-state benefit; the
report keeps each warm-up duration rather than presenting it as free work.
Measure complete iterations on the intended worker before enabling this.

Tests exercise real spawned processes, CPU and parent-owned inference, frozen
model rebinding, all evaluation opponents and paired seeds, ordered legal replay,
real updates/checkpoint reload, exact uninterrupted/resumed CPU weights,
cancellation and worker death. GPU execution skips explicitly without hardware.
These measurements establish neither GPU speedup nor stronger play.

Reproduce after extracting the baseline package:

```sh
mkdir -p work/search-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/search-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature workers --variant baseline --source work/search-baseline --output work/search-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature workers --variant candidate --output work/search-after.json
```

Repeat for each ruleset and `--feature workers-selfplay`. Use `--repeats 1` in
three alternating fresh-process pairs for the collection protocol. Add
`--device cuda` on a compatible GPU host. Risks include multiplied CPU runtime,
model/cache memory, IPC latency, CPU oversubscription and deadline-dependent
results. Cancellation and failed actors close the pool; no CUDA tensors/models
are sent to actors, and inference waits remain inside each search's deadline.

A follow-up corrects minimum-batch reporting when a game has only cache hits;
the measured cache-off workloads always have nonempty forward batches. The raw
source fingerprints retain the measured version before that reporting guard.
