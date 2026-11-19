# Browser analysis Phase 1 validation (revision 3)

Validation record for Phase 1A (full-strength Jarl) and Phase 1B (trained-model
ONNX export and browser neural inference) after integrating current `main`
(`07d7438`, merged as `9daa668`) and correcting the follow-up review findings.

Historical evidence is preserved unchanged and must not be read as evidence for
the current contract:

- Revision 1 (`07f63bb`): `browser-analysis-jarl-*-2026-11-19.json`,
  `browser-neural-*-2026-11-19.json`. It compared only the benchmark plan,
  selected positions by name, did not fail on fixed-work divergence, duplicated
  one input for batching and recorded the requested provider as the observed one.
- Revision 2 (`c158ed8`): `*-r2.json`. It added a real fixed-work gate, custom
  workloads, bounded per-case requests, browser WASM parity and distinct-position
  batching, but retained first-repetition-only comparison, reconstructed browser
  probabilities from logits, could retain a stale `VALIDATED.json` after a forced
  unvalidated replacement, did not await session release, and ended download
  ownership before digest verification.
- Revision 3 (this record): `*-r3.json`. It validates every repetition, separates
  execution validity from parity and acceptance, validates the **actual returned
  browser policy**, enforces a coherent artifact lifecycle, validates graph and
  runtime tensors, awaits resource release, closes load/cancellation races and
  refines provider evidence.

The project owner's earlier Phase 0 WSL run remains separate historical evidence
in [browser-analysis-prototype.md](browser-analysis-prototype.md).

Machine-readable revision-3 evidence:

- [native fixed-work quick](jarl-native-fixed-work-quick-r3.json) ·
  [browser fixed-work quick](jarl-browser-fixed-work-quick-r3.json)
- [native depth-5 Brandubh](jarl-native-depth5-brandubh-r3.json) ·
  [browser depth-5 Brandubh](jarl-browser-depth5-brandubh-r3.json)
- [native depth-5 Tablut+Copenhagen](jarl-native-depth5-tablut-copenhagen-r3.json) ·
  [browser depth-5 Tablut+Copenhagen](jarl-browser-depth5-tablut-copenhagen-r3.json)
- [native fixed-time standard](jarl-native-fixed-time-standard-r3.json) ·
  [browser fixed-time standard](jarl-browser-fixed-time-standard-r3.json)
- [ONNX export manifest](neural-export-manifest-r3.json) ·
  [native ONNX parity](neural-native-onnx-parity-r3.json)
- [browser WASM policy parity](neural-browser-parity-wasm-r3.json) ·
  [all corruption kinds rejected](neural-browser-corruption-r3.json)
- [browser WASM benchmark](neural-browser-wasm-benchmark-r3.json)
- [WebGPU parity blocked](neural-browser-parity-webgpu-blocked-r3.json) ·
  [WebGPU benchmark blocked](neural-browser-benchmark-webgpu-blocked-r3.json)

## Environment

| Item | Value |
| --- | --- |
| Host | Linux x86_64 container (Proxmox LXC), glibc 2.41, 4 exposed logical CPUs |
| CPython | 3.11.18 |
| PyTorch / ONNX / onnxruntime | 2.14.1+cpu / 1.23.2 / 1.32.0 |
| Node.js / Chromium | 22.23.4 / 156.0.8078.4 headless |
| Pyodide / CPython | 0.27.7 / 3.12.7 |
| ORT Web | 1.22.0 (digest-verified staging) |

No physical phone, no WebGPU adapter and no competitive trained checkpoint were
available. Single observations unless a median/p90 is stated; mobile emulation is
not physical-mobile evidence.

## Phase 1A — Jarl contract in revision 3

`jarl_benchmark.py` owns positions, per-position legal-move/terminal contracts,
effective configuration and workload digest. `jarl_compare.py` is the only
comparison implementation. Revision 3 changes:

- **Every repetition is validated.** Record keys include the repetition index;
  duplicates are rejected; expected coverage is derived independently from the
  workload cases × configurations × declared repetitions. Both reports omitting
  the same position, empty reports and mismatched repetition counts all fail.
- **Record identity is validated.** Position hash, ruleset, mode, seed and the
  normalised effective-settings digest must match the workload. Numeric
  normalisation keeps digests stable across the Python/JavaScript JSON boundary.
- **Execution, parity and acceptance are separate.** A result is structurally
  valid only with a finite value, a legal workload move, correct
  defender-relative terminal value and a coherent status/depth. Parity is
  compared at equal completed work (same depth, interruption state and, when
  available, node/evaluation counters). Acceptance means every expected case
  completed the configured depth on both sides.
- **Honest incompleteness.** Asymmetric or depth-limited results never fail a run
  merely because one runtime completed more work; they are listed as
  `unresolved` acceptance targets. A comparison can legitimately finish
  collecting evidence while `acceptance_complete` is false.
- **Known unsupported depth 10 stays explicit.** It is accepted only for the
  configuration declared unsupported by the plan and remains unresolved.
- **Equal-work performance.** Terminal, unsupported, failed, zero-depth and
  non-equivalent cases are excluded from latency ratios; node-rate metrics are
  reported when counters permit and are `null` otherwise (never zero).

### Fixed-work correctness gate

| Comparison | Expected | Compared | Valid | Parity (equal work) | Acceptance | Browser ÷ native |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| quick (depth 3), all rulesets, reps=2 | 42 | 42 | 42 | 36/36 | complete | 2.14× |
| standard (depth 5), Brandubh, reps=2 | 14 | 14 | 14 | 12/12 | complete | 2.25× |
| standard (depth 5), Tablut+Copenhagen, reps=1 | 14 | 14 | 14 | 12/12 | **incomplete (3)** | 2.08× |

Brandubh `warm-up=1`; the Tablut+Copenhagen run uses `warm-up=0` to bound the
58–60 s Copenhagen searches. The three unresolved cases are Copenhagen `opening`,
`midgame` and `repetition`: native CPython and browser Pyodide both reach depth 4
inside the 60 s fixed-work safety limit, parity matches at that equal depth, and
the runs are reported as limited rather than passed or failed. Tablut completes
depth 5 on all tested positions.

### Fixed-time workload comparison (informational)

`standard` (depth 5, 2 s), two measured repetitions, warm-up 1. 42/42 cases
executed validly; 16 cases reached identical completed work (all parity matched);
24 could not be compared because the runtimes completed different work; 20
informational warnings. `acceptance_complete` is true for the fixed-time
evidence target, which does not claim depth completion.

## Phase 1B — neural export, parity and runtime

### Artifact lifecycle

The export is transactional and fail-closed. A validated export contains the ONNX
model, sidecar, reference vectors, a manifest and `VALIDATED.json`; the marker and
manifest agree on status, ruleset, model digest, model size and **reference-vector
digest**. Staging verifies the actual received bytes, the graph (opset, tensor
ranks, element types, spatial dims, dynamic batch, policy width) and the full
metadata contract. A forced replacement invalidates the old marker **before**
attempting publication, so a failed or unvalidated replacement can never inherit
an earlier marker. Stale markers, mismatched reference digests and inconsistent
manifests are rejected.

| Metric | Value |
| --- | --- |
| Checkpoint | trained Brandubh **smoke** model (channels 4, blocks 0) |
| Model SHA-256 | `959578236ead3fa9…` |
| ONNX graph | 46,924 B raw / 6,926 B gzip estimate |
| Reference cases | 39 (38 non-terminal incl. 32 distinct generated, 1 terminal) |
| Coverage kinds | opening, midgame, endgame, tactical, king-escape, repetition, generated, terminal |

### Numerical parity (actual returned policy)

The browser gate validates the policy the runtime actually returned: model and
position identity, logits, defender value, **legal indices and their order**,
legal-only normalised probabilities, shapes and dtypes, finiteness, probability
sum, and postprocessing consistency. It does not reconstruct browser
probabilities from logits.

| Stage | Cases | Worst logit abs | Worst value abs | Worst postprocess | Material ranking changes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native PyTorch → ORT CPU | 39 | 7.15e-7 | 2.98e-8 | 0 | 0 |
| Native PyTorch → browser ORT WASM | 39 | 1.67e-6 | 2.98e-8 | 0 | 0 |

Distinct-position batched inference at batch sizes 1, 2, 4, 8, 16 and 32 all
passed parity against their own single-position reference, in order.

Every deliberate corruption kind fails the mandatory gate: a policy logit, the
defender value, legal indices, legal ordering, probabilities, policy shape, value
shape, model identity, and batched count/ordering. See
[neural-browser-corruption-r3.json](neural-browser-corruption-r3.json).

### Browser inference performance (smoke model, WASM)

| Metric | Value |
| --- | ---: |
| ORT + session initialization | 528.9 ms |
| First inference (cold, incl. tensor prep) | 24.7 ms |
| Warm run median / tensor prep median | 0.8 ms / 0.1 ms |
| Sustained in-worker throughput (200 × batch 8) | 4,523.6 positions/s |

| Batch | Distinct positions | Median | p90 | Positions/s |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 0.9 ms | 2.14 ms | 1,111 |
| 2 | 2 | 1.3 ms | 1.42 ms | 1,538 |
| 4 | 4 | 1.8 ms | 2.00 ms | 2,222 |
| 8 | 8 | 2.8 ms | 6.20 ms | 2,857 |
| 16 | 16 | 4.0 ms | 8.64 ms | 4,000 |
| 32 | 32 | 7.1 ms | 7.96 ms | 4,507 |

Repeated model replacement (3 cycles) succeeded with explicit release. Memory
hints were unavailable in headless Chromium and the pinned ORT Web build does not
expose its WASM heap size, so heap fields are `null`, not zero and not a stability
claim.

### Provider reporting and WebGPU

Requested provider, adapter availability, session creation, inference success and
observed placement are reported separately. Placement is `wasm`,
`webgpu-session-verified` (only when the session reports WebGPU providers alone),
`webgpu-session-unverified` (GPU session accepted but per-node placement is not
observable), `webgpu-mixed` (WebGPU plus another provider), `cpu-fallback` or
`placement-unknown`. `gpu_execution_verified` is true only for
`webgpu-session-verified`.

On this host `navigator.gpu` exists but `requestAdapter()` returns no adapter.
Both WebGPU gates fail explicitly with
`WebGPU requested but no adapter is available` and `--require-webgpu` exits
non-zero. WebGPU execution remains **blocked and unvalidated**.

## Repository checks

- `tests/test_offline_spike.py`: 17 passed.
- `tests/test_browser_jarl_benchmark.py`: 24 passed (all-repetition coverage,
  duplicates, independent expected coverage, both-empty, identity/record identity,
  terminal value, illegal moves, both-side errors, malformed limited results,
  asymmetric completion, work counters, custom positions).
- `tests/test_browser_neural_parity.py`: 30 passed (all reported-policy
  corruptions, batch count/order, duplicates, empty reference, tolerances).
- `tests/test_browser_neural_export.py`: 25 passed with the optional ONNX
  dependencies (skip without them), including forced unvalidated replacement,
  stale marker, reference digest, inconsistent manifest and interrupted export.
- `tests/test_browser_client_lifecycle.py`: 2 passed (25 deterministic Node
  lifecycle checks incl. digest cancellation, competing loads, failed-WASM→WebGPU
  recreation and oversized body).
- Real-browser gates run manually: `verify.mjs` (Phase 0, green),
  `verify_jarl.mjs` (all tables above), `neural_parity.mjs` (WASM + 9 corruptions +
  WebGPU blocked), `neural_benchmark.mjs` (WASM + WebGPU blocked).

## Remaining validation and blockers

- Competitive trained weights (32×2+) for all three rulesets.
- Physical WebGPU execution on Windows/Linux/Android/Safari hardware.
- Physical desktop/mobile performance and real process memory.
- Long-run memory behaviour with a representative network.
- Playing strength (Phase 3).

No production browser analysis is enabled, no production limit/API/rules/training
behaviour/CSP changed and no runtime dependency entered the application bundle.
