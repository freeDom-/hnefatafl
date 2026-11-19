# Browser analysis Phase 1 validation (1A Jarl, 1B neural inference)

Fresh measurements produced by the implementation agent on 2026-11-19 for the
Phase 1A and Phase 1B scope. They are **not** the project owner's earlier Phase 0
run and do not overwrite it. The owner's externally supplied Phase 0 numbers are
recorded separately in
[browser-analysis-prototype.md](browser-analysis-prototype.md) and the
[roadmap](../designs/browser-analysis-roadmap.md).

Machine-readable evidence:
[native Jarl depth 3](browser-analysis-jarl-native-2026-11-19.json) ·
[browser Jarl depth 3](browser-analysis-jarl-browser-2026-11-19.json) ·
[native Jarl depth 5 Brandubh](browser-analysis-jarl-native-depth5-2026-11-19.json) ·
[browser Jarl depth 5 Brandubh](browser-analysis-jarl-browser-depth5-2026-11-19.json) ·
[ONNX export/parity](browser-neural-onnx-export-2026-11-19.json) ·
[browser neural WASM](browser-neural-wasm-2026-11-19.json) ·
[browser neural WebGPU capability](browser-neural-webgpu-2026-11-19.json).

## Environment

| Item | Value |
| --- | --- |
| Host | Linux 7.0.14-26-pve x86_64, glibc 2.41, 4 exposed logical CPUs |
| CPython | 3.11.18 (project venv) |
| PyTorch | 2.14.1+cpu (one thread for parity) |
| ONNX / onnxscript / onnxruntime | 1.23.2 / 0.7.2 / 1.32.0 |
| Node.js | 22.23.4 |
| Browser | Chromium 156.0.8078.4 (Playwright, headless) |
| Pyodide / CPython | 0.27.7 / 3.12.7 |
| ORT Web | 1.22.0 |
| WASM heap capacity | 20 MiB |

This is a shared, resource-constrained container. No physical phone, no WebGPU
adapter and no representative trained checkpoint were available. All single-run
numbers are observations, not calibrated percentiles, unless a median/p90 is
stated.

## Phase 1A — production Jarl configuration

The spike exposes the production `alpha_beta` ("Jarl") preset: depth 5, 2 s
cooperative deadline, Shield maiden evaluation and the optimized search defaults
(transposition table, move ordering, PV/table/killer/history ordering). The
shared `jarl_benchmark.py` runner is imported unchanged by native CPython and by
the Pyodide worker, so both compare identical serialized positions.

Benchmark workloads (benchmark targets, not production defaults):

| Configuration | Depth | Time budget |
| --- | ---: | ---: |
| quick | 3 | 250 ms |
| interactive | 5 | 1 s |
| standard (production Jarl) | 5 | 2 s |
| deep | 8 | 5 s |
| extended | 10 | 10 s |

The engine's internal alpha-beta depth cap is 8 and the public configuration
validator caps depth at 5. `extended` is therefore rejected by the engine with
`ValueError: Search depth must be between 1 and 8` and is reported per case
rather than silently reduced. The client hard watchdog is separate from the
cooperative deadline: 5 s for ordinary interactive jobs, up to 180 s for explicit
benchmark jobs.

### Deterministic fixed-work agreement (correctness)

Native CPython and browser Pyodide were run with the same positions, seed,
configuration and engine revision. Completed fixed-work searches must agree
exactly because they perform identical work.

| Comparison | Comparable cases | Best-move agreement | Value agreement |
| --- | ---: | ---: | ---: |
| quick (depth 3), all three rulesets, seven position kinds | 21 | 21/21 | 21/21 |
| standard (depth 5), Brandubh, seven position kinds | 7 (×2 runs) | 14/14 | 14/14 |

### Fixed-work latency at equal completed work (depth 3)

Browser is consistently ≈2.1–2.4× native CPython at identical completed depth.
Because both sides complete the same depth, this ratio is a fair computational
overhead measurement, not a speedup claim.

| Position | Native ms | Browser ms | Ratio |
| --- | ---: | ---: | ---: |
| brandubh-endgame | 30.8 | 67.5 | 2.2× |
| brandubh-king-escape | 7.6 | 17.6 | 2.3× |
| brandubh-midgame | 119.9 | 260.7 | 2.2× |
| brandubh-opening | 94.5 | 213.1 | 2.3× |
| brandubh-repetition | 93.6 | 221.4 | 2.4× |
| brandubh-tactical | 61.3 | 137.4 | 2.2× |
| copenhagen-endgame | 342.6 | 722.9 | 2.1× |
| copenhagen-king-escape | 55.2 | 121.6 | 2.2× |
| copenhagen-midgame | 1711.9 | 3540.7 | 2.1× |
| copenhagen-opening | 1823.3 | 3815.5 | 2.1× |
| copenhagen-repetition | 1360.1 | 2786.5 | 2.0× |
| copenhagen-tactical | 676.8 | 1403.4 | 2.1× |
| tablut-endgame | 15.1 | 34.9 | 2.3× |
| tablut-king-escape | 6.6 | 14.0 | 2.1× |
| tablut-midgame | 184.7 | 386.5 | 2.1× |
| tablut-opening | 326.2 | 735.6 | 2.3× |
| tablut-repetition | 402.3 | 880.1 | 2.2× |
| tablut-tactical | 22.7 | 52.4 | 2.3× |

Terminal positions complete in ≈0 ms on both sides and return
`source: terminal`, `best_move: null`, confirming terminal handling bypasses
search.

### Fixed-time search observations

`quick` fixed-time (250 ms) with all 21 positions:

| Ruleset | Browser median ms | Browser p90 ms | Median completed depth |
| --- | ---: | ---: | ---: |
| Brandubh | 141.2 | 238.7 | 3 |
| Tablut | 54.8 | 250.3 | 2.5 |
| Copenhagen | 250.3 | 250.5 | 2 |

Fixed-time searches complete different depths than native, so their best-move and
value agreement (81% and 76% on the 21 cases) is timing-dependent and is **not**
reported as a correctness failure. Deterministic fixed-work agreement is the
correctness signal.

### Lifecycle and responsiveness

| Metric | Browser quick run | Browser depth-5 run |
| --- | ---: | ---: |
| Startup to first response | 3185.7 ms | 2082.2 ms |
| Cancellation acknowledgement | 0.2 ms | 0.3 ms |
| Worker restart after termination | 2115.7 ms | 1845.5 ms |
| UI click during active worker search | 0.2 ms | 0.2 ms |
| WASM heap capacity | 20 MiB | 20 MiB |

The main thread stays responsive because all search runs in the worker. Startup
includes service-worker activation and is not a WAN model.

## Phase 1B — ONNX export and browser inference

### Export and native parity

An actual trained Brandubh smoke checkpoint (channels 4, blocks 0, two
self-play games — a real but non-competitive training artifact) was exported from
the validated tensor-only loader. This validates the pipeline; it is **not**
strength evidence.

| Metric | Value |
| --- | --- |
| ONNX graph size | 46,924 bytes |
| Graph validation | `onnx.checker` and shape inference passed |
| Input / outputs | `[batch,6,7,7]` / `[batch,2401]`, `[batch]` |
| Dynamic batch | yes (verified at batch 1, 2, 8) |
| Parity positions | 6 non-terminal Brandubh positions |
| Worst logit max-abs error | 1.43e-6 |
| Worst value abs error | 5.96e-8 |
| Worst legal-policy relative error | 2.76e-7 |
| Top-5 move ranking | identical on all cases |

The metadata sidecar records ruleset, architecture, shapes, `source*size**2+target`
action indexing, defender-relative value, legal-only masking, SHA-256 digests and
exporter/runtime versions. It contains no optimizer state, state dict or source
filesystem path.

### Browser ORT Web WASM

The exported model ran in Chromium through ORT Web 1.22.0 WASM
(`ort.min.js`, single-threaded, no cross-origin isolation). First inference
measured 19.5 ms; legal-only probabilities summed to 1.

| Batch | Median ms | p90 ms | Positions/s |
| ---: | ---: | ---: | ---: |
| 1 | 0.6 | 0.78 | 1,667 |
| 2 | 0.4 | 0.46 | 5,000 |
| 4 | 0.5 | 0.56 | 8,000 |
| 8 | 0.7 | 1.48 | 11,429 |
| 16 | 1.2 | 1.20 | 13,333 |
| 32 | 2.1 | 2.16 | 15,238 |

These are tiny-model numbers and must not be extrapolated to representative
32×2 or larger networks. They establish that the browser WASM path executes the
actual exported graph with correct shapes and legal masking.

### Browser ORT Web WebGPU

`navigator.gpu` is present but `requestAdapter()` returned no adapter in this
headless container. A WebGPU session request therefore failed **explicitly**
("WebGPU requested but no adapter is available") and reported
`provider_is_gpu: false`. No CPU fallback was presented as GPU success. WebGPU
execution remains unvalidated until run on physical hardware with a real adapter
(see the roadmap's Phase 1B blockers).

## Repository checks

- `tests/test_offline_spike.py`: 14 passed (extended Jarl bounds, engine depth
  constraint, shared-position benchmark, production-config equivalence, plus the
  original staging/parity/refusal cases).
- `tests/test_browser_neural_export.py`: 5 passed with the optional ONNX
  dependencies installed; they skip without them.
- `ruff check` and `ruff format --check` pass on the spike and tests.
- The full Pyodide browser gate `verify.mjs` was re-run against the modified
  bridge/worker/client and completed all original assertions.

## Remaining validation and blockers

- Tablut/Copenhagen fixed-work depth 5+ agreement.
- Physical desktop and mobile device performance and memory.
- Representative trained weights for all three rulesets.
- WebGPU on physical hardware with an adapter.
- Long-run memory growth and model-switch behavior.
- Deadline-overshoot percentiles over repeated measured runs.

No production browser analysis is enabled, no production limit or API changed and
no runtime dependency entered the application bundle.
