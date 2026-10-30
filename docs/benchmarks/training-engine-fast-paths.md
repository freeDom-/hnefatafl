# Engine fast paths and self-play

Compared independently against main `5a343668411033ed3283ed51e01323454975723a`.
The [raw A/B samples](training-engine-fast-paths.json) retain source hashes,
environment, counters, configurations and work hashes. Three fresh-process pairs
alternate baseline/candidate order and discard a warm-up in each process. No
other tests or benchmarks ran during measurement. CPU FP32, one math thread.

The engine sample applies every legal move at four seeded positions, repeated
eight times. The self-play sample uses recommended networks, four concurrent
eight-ply games, 32 visits per move, seed 42 and a 300-second search ceiling.

| Ruleset | Successor-generation speedup | Self-play before/after median seconds | Self-play speedup |
| --- | ---: | ---: | ---: |
| Brandubh | 2.86x | 0.6778 / 0.6130 | 1.11x |
| Tablut | 5.27x | 0.9278 / 0.8560 | 1.08x |
| Copenhagen | 2.58x | 2.2463 / 1.7195 | 1.31x |

All successor-result and complete self-play input/policy/work hashes match the
baseline across repetitions. Self-play rates include inference and scheduling;
the larger microbenchmark ratios must not be presented as full-training gains.
Short timings have visible noise; these are bounded CPU diagnostics, not GPU or
playing-strength evidence. Unfinished games remain unfinished.

The change adds an allocation-free legal-move existence query, immutable cached
movement rays/neighbors/corners, integer-target validation, and an encirclement
early exit when one edge-accessible defender disproves total enclosure. Movement
order, restricted-square pass-through, captures, repetition and no-move outcomes
are unchanged. Independent tests compare legal ordering with a direct geometric
oracle and encirclement with defender-to-edge flood searches, including no-Move-
allocation existence checks. Existing engine, symmetry and MCTS tests pass.

Reproduce from the repository root:

```sh
mkdir -p work/engine-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/engine-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature engine --variant baseline --source work/engine-baseline --output work/engine-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature engine --variant candidate --output work/engine-after.json
```

Repeat with `--feature selfplay` and each `--ruleset`. For the collection protocol
use `--repeats 1` in three alternating fresh-process pairs. Caches warm before
measurement, contain immutable geometry only, and have bounded board-size scope.
