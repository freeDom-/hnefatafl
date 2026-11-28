# Benchmark methodology

This page defines how Hnefatafl measurements are reported. Individual reports
contain the exact command, revision, host and sample; do not copy numbers here
without their context.

## Units and phases

| Term | Meaning |
| --- | --- |
| Simulation | One MCTS visit/iteration, including dependent neural evaluation |
| Completed depth | Deepest fully completed alpha-beta iteration, not the requested depth |
| Optimization batch | Replay positions used in one gradient update |
| Inference batch | Positions grouped into one neural forward pass |
| Games per second / positions per second | End-to-end throughput of the named phase, including overhead |
| Fixed work | Same completed search work or same number of games/positions |
| Fixed time | Same wall-clock budget; completed work may differ |

State the phase (self-play, evaluation, optimization, inference-only), device,
backend, precision, ruleset, model architecture/weights, search budget, game/ply
caps, concurrency and batch size. CPU and GPU numbers use separate tables.

## Fair comparisons

1. Use the same source revision, model and ruleset unless the change is the subject.
2. Compare fixed work when claiming a speedup; compare fixed time only for
   throughput/behaviour, and report completed work.
3. Run a warm-up and repeat seeded measurements. Report variance or the raw runs.
4. Count unfinished and truncated games separately from rule draws.
5. Keep evaluation and training throughput separate. Evaluation games do not
   become training data.
6. For alpha-beta, verify correctness against an independent minimax before
   interpreting speed. For search strength, use equal-time series with confidence
   intervals.
7. For training changes, prove real updates, checkpoint reload and legal play.
   A successful run does not establish stronger play.

## How to read the reports

Each benchmark page states its own scope. Historical records may use older hardware,
software, models or schemas. They are retained for technical comparison only; they
do not become current after a documentation edit.

- [Search benchmarks](benchmarks/search-alpha-beta.md),
  [parallel search](benchmarks/search-parallel.md),
  [MCTS](benchmarks/search/mcts-phase2.md)
- [Training benchmarks](benchmarks/training-job-archives.md) and
  [self-play optimizations](benchmarks/training-selfplay-optimizations.md)
- [Inference and symmetry/caching](benchmarks/inference/symmetry-and-caching.md),
  [OpenVINO](benchmarks/inference/openvino.md)
- [Browser/backend measurements](benchmarks/browser/backend-read.md),
  [frontend bundle](benchmarks/browser/frontend-bundle.md)
- [Browser-local analysis](browser-analysis/README.md)

## Reproduction

```sh
.venv/bin/python -m hnefatafl.cli devices
.venv/bin/python -m hnefatafl.cli benchmark --config configs/benchmarks/<name>.json --output work/benchmark.json
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta --config <config> --output work/alpha-beta.json --summary-output work/alpha-beta-summary.md
.venv/bin/python -m hnefatafl.cli benchmark-training --config <config> --output work/training.json
```

Use an isolated `HNEFATAFL_DATA_DIR` and disposable output paths. Benchmark
commands must not overwrite production data or register models. See
[performance](../training/../evaluation/methodology.md) for the
full CLI options and metric interpretation.

## Limitations

- The development host may have no CUDA, MPS or Intel GPU; hardware-gated tests
  skip and are not validation.
- Browser emulation is not physical-device evidence; WebGPU execution is not
  verified without an adapter and per-operator evidence.
- Long training runs, competitive strength and production capacity are outside
  routine benchmark reports.
- External links and third-party benchmark claims are not validated by `make docs`.
