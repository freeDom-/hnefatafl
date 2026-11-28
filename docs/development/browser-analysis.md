# Browser-local analysis architecture

[Development index](README.md) · [Active roadmap](roadmap/browser-analysis.md) · [Analysis](analysis.md)

Browser-local analysis executes the existing Python rules/search in a pinned Pyodide
worker and the trained policy/value network through a validated ONNX artifact in ORT
Web. It does not add a second rules/search implementation.

## Components

- Pinned, digest-verified runtime and model artifacts served from the application
  origin; no CDN at runtime.
- A Pyodide search worker that advances the existing MCTS generator one slice at a
  time.
- An independent ORT Web inference worker for single/batched WASM (and
  evidence-gated WebGPU) inference.
- A coordinator that owns request identity, generations, cancellation, deadlines,
  provider evidence and fallback.
- Deployment policy (`browser_only`, `browser_preferred`, `server_only`, `disabled`)
  resolved at startup; no player provider selector.

## Security and publication

Only administrator-authorized, validated ONNX exports can be published. Artifacts are
immutable and digest-addressed; model/reference/runtime identities are pinned.
The CSP changes only when delivery is enabled (`script-src 'self' 'wasm-unsafe-eval'`,
`worker-src 'self'`). Disabled deployments keep the strict policy unchanged.

## Status

Phases 0–5 are implemented and software/browser validated within their scope.
Competitive checkpoints, physical devices, verified GPU execution, sustained memory,
distribution licensing and production acceptance remain open. Offline readiness,
single-tree batching, subtree reuse and server-analysis decommissioning are deferred.
The [roadmap](roadmap/browser-analysis.md) is the canonical phase/evidence record;
phase reports and raw evidence are under
[Evaluation](../evaluation/browser-analysis/README.md).
