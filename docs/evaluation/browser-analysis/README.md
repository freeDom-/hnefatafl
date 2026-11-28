# Browser-local analysis

This section preserves the research and evidence for executing the existing
Python rules/MCTS analysis in a browser. The canonical status, phase table,
acceptance criteria and remaining work are in the
[roadmap](../../development/roadmap/browser-analysis.md); this page is the
evidence index.

The feature is opt-in and disabled by default. It is **not** a production
analysis executor, does not establish competitive playing strength and does not
prove physical-device or verified-GPU performance.

## Phase reports

| Phase | Report |
| --- | --- |
| 0 | [Feasibility prototype](browser-analysis-prototype.md), [neural feasibility](browser-neural-feasibility.md) |
| 1 | [Full-strength Jarl and ONNX inference](browser-analysis-phase1.md) |
| 2 | [Browser neural MCTS](browser-analysis-phase2.md) |
| 3A | [Optimization, profiling and compact transport](browser-analysis-phase3.md) |
| 3B | [Cache, direct transport and batching](browser-analysis-phase3b.md) |
| 3C-A | [Competitive validation, tournaments, memory and device runs](browser-analysis-phase3c.md) |
| 3C-B | [Competitive measurements and readiness](browser-analysis-phase3c-b.md) |
| 4 | [Production security and runtime integration](browser-analysis-phase4.md) |
| 5 | [Transparent browser-first integration](browser-analysis-phase5.md) |
| Physical devices | [Device matrix](device-matrix-phase3c.md) |

## Raw evidence

`evidence/` contains the checked-in JSON and text evidence referenced by the
reports: native/browser parity, Jarl fixed-work and fixed-time runs, neural export
manifests, WASM/WebGPU capability records, MCTS parity and benchmark runs, memory
profiles, tournament harness records, and phase-specific acceptance manifests.
Files retain their original names and revisions; superseded runs are not deleted.

## Status summary

- Implemented and software-validated: shared encoder, Pyodide search driver,
  ONNX export/parity, WASM inference, browser neural MCTS, compact legal-only
  inference, bounded exact cache, independent batching, production runtime
  distribution, publication controls and the transparent execution policy.
- Open external gates: competitive 32×2+ checkpoints for all rulesets, physical
  desktop/mobile/Safari/Firefox validation, verified WebGPU execution, sustained
  memory acceptance, meaningful playing-strength tournaments, distribution
  licensing and a controlled rollout decision.
- Deferred: offline service-worker/runtime execution, single-tree batching,
  subtree reuse, server analysis decommissioning.

Do not convert a software validation into a competitive or production claim. Read
the roadmap's status table before assigning further work, and check current pull
request/merge state because it is a point-in-time record.
