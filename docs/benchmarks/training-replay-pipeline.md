# Packed replay and CUDA prefetch

Independent A/B against main `5a343668411033ed3283ed51e01323454975723a`.
The [raw samples](training-replay-pipeline.json) retain source hashes, complete
settings, loss/update counts and final-weight fingerprints. Three alternating
fresh-process pairs per ruleset/mode each discard one warm-up; tests and other
benchmarks did not run concurrently. CPU FP32, one math thread, recommended
networks, 512 frozen replay positions, two epochs, random D4, fixed initial
weights (42), replay order (9) and augmentation RNG (10). Final-weight hashing
runs outside the measured optimization interval. Optimizer creation, replay
packing and normal metric recording are inside that interval for both sources.

| Ruleset | Auto before/after median seconds | Auto ratio | Packed before/after median seconds | Packed ratio |
| --- | ---: | ---: | ---: | ---: |
| Brandubh | 0.3083 / 0.3268 | 0.94x | 0.2997 / 0.3073 | 0.98x |
| Tablut | 1.0169 / 1.0283 | 0.99x | 1.0307 / 0.9823 | 1.05x |
| Copenhagen | 3.0345 / 3.2202 | 0.94x | 3.1062 / 2.9564 | 1.05x |

All work/final-weight hashes match. Sample ranges overlap; no reliable CPU
speedup is established. Preliminary packed CPU results also varied by ruleset,
so automatic CPU/MPS mode conservatively keeps legacy collation. Explicit
`packed` remains available for representative larger-replay/epoch experiments.
Automatic CPU timing ratios below one are retained rather than omitted.
No CUDA or MPS throughput is claimed on this CPU-only host.

Packed replay stacks inputs and sparse action/probability/legal arrays once per
optimization phase, with row offsets. Minibatches gather selected rows and apply
the same D4 permutations before dense target/mask scatter. Dense targets are not
allocated for the entire replay. CUDA uses one CPU preparation thread, pinned
host tensors and a private transfer stream, with bounded lookahead and two device
batches. Each consumer waits for its batch and records its compute stream.
Loss scalars transfer once per CUDA epoch; finite-loss checks, clipping, update
counts, optimizer and GradScaler behavior stay unchanged.

Lookahead owns a private augmentation RNG. Only consumed batches commit that
state and their counters, so cancelling does not consume transforms for unused
future batches. Cleanup cancels pending preparation, joins the thread and drains
the transfer stream. CPU tests prove exact input/mask/target, loss, optimizer
weight and RNG parity with legacy, including a legacy-to-packed full checkpoint
resume. Hardware tests compare CUDA batch/RNG ownership and mixed-precision
optimizer/scaler parity; both explicitly skip here. No synthetic GPU pass is
claimed. `replay_pipelines` worker capabilities gate explicit packing; legacy
workers receive the legacy automatic fallback without unknown keys, including
sanitized resume configuration.

Reproduce after extracting the baseline package:

```sh
mkdir -p work/replay-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/replay-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature replay --variant baseline --source work/replay-baseline --output work/replay-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature replay --variant candidate --replay-pipeline packed --output work/replay-after.json
```

Repeat each ruleset and use `--replay-pipeline auto` for automatic fallback.
For the collection protocol use `--repeats 1` in three alternating process pairs.
On CUDA hardware add `--device cuda`, then run `tests/test_replay_pipeline.py`
before enabling it for a long job. Risks include extra packed host storage,
pinned RAM/device batch buffers and transfer/CPU overhead for small workloads.
`legacy` is the rollback path; checkpoint formats and replay contents are unchanged.
