# Browser analysis Phase 1 validation (revised)

Validation record for Phase 1A (full-strength Jarl) and Phase 1B (trained-model
ONNX export and browser inference) after the review corrections to PR #104.

This record supersedes the **measurements** in the earlier revision-specific
files while preserving them as history:

- Revision 1 (`07f63bb`) evidence remains committed unchanged:
  `browser-analysis-jarl-*-2026-11-19.json`, `browser-neural-*-2026-11-19.json`.
  That revision compared only the benchmark *plan*, did not fail on fixed-work
  divergence, selected positions by name from the staged bundle, duplicated one
  input for batched inference and recorded the requested provider as the actual
  provider. Its numbers must not be read as evidence for the corrected contract.
- Revision 2 (this record) evidence is the `*-r2.json` files below.

The project owner's earlier Phase 0 WSL run is separate historical evidence in
[browser-analysis-prototype.md](browser-analysis-prototype.md) and is not recast.

Machine-readable evidence (revision 2):

- [native fixed-work quick](jarl-native-fixed-work-quick-r2.json) ·
  [browser fixed-work quick](jarl-browser-fixed-work-quick-r2.json)
- [native depth-5 Brandubh](jarl-native-depth5-brandubh-r2.json) ·
  [browser depth-5 Brandubh](jarl-browser-depth5-brandubh-r2.json)
- [native depth-5 Tablut+Copenhagen](jarl-native-depth5-tablut-copenhagen-r2.json) ·
  [browser depth-5 Tablut+Copenhagen](jarl-browser-depth5-tablut-copenhagen-r2.json)
- [ONNX export manifest](neural-export-manifest-wasm-parity.json) ·
  [native ONNX parity](neural-native-onnx-parity.json)
- [browser WASM numerical parity](neural-browser-parity-wasm-r2.json) ·
  [deliberate corruption rejected](neural-browser-parity-corruption-r2.json) ·
  [WebGPU blocked](neural-browser-parity-webgpu-blocked-r2.json)
- [native fixed-time standard](jarl-native-fixed-time-standard-r2.json) ·
  [browser fixed-time standard](jarl-browser-fixed-time-standard-r2.json)
- [browser WASM benchmark](neural-browser-wasm-benchmark-r2.json)

## Environment

| Item | Value |
| --- | --- |
| Host | Linux x86_64 container (Proxmox LXC), glibc 2.41, 4 exposed logical CPUs |
| CPython | 3.11.18 (project venv) |
| PyTorch | 2.14.1+cpu (single thread for parity) |
| ONNX / onnxscript / onnxruntime | 1.23.2 / 0.7.2 / 1.32.0 |
| Node.js | 22.23.4 |
| Browser | Chromium 156.0.8078.4 (Playwright, headless) |
| Pyodide / CPython | 0.27.7 / 3.12.7 |
| ORT Web | 1.22.0 (digest-verified staging) |

No physical phone, no WebGPU adapter and no representative competitive trained
checkpoint were available. Numbers are single observations unless a median/p90 is
stated. Mobile emulation is never presented as physical-mobile evidence.

## Phase 1A — Jarl benchmark contract

One shared `jarl_benchmark.py` defines the positions, effective configuration and
workload identity for both runtimes. `jarl_compare.py` is the single comparison
implementation; `compare_jarl_benchmarks.py` is its CLI and `verify_jarl.mjs`
invokes it, so there is no duplicated comparison logic.

Corrected contract:

- The verifier analyses the **exact serialized state objects** supplied by
  `--positions`; it never re-selects by name. Custom positions, and positions
  that reuse a fixture name with a different board or repetition history, are
  honoured.
- Each report records a workload digest over the ordered serialized states
  (including repetition history), the names/kinds, the effective configuration
  for every row, the mode and the seed, plus a hash of the staged engine sources.
  Different digests are refused rather than compared.
- Each case runs as its own bounded request, so a slow or failing case cannot
  discard earlier records.
- `fixed_work` has a mandatory correctness gate: status, completed depth, best
  move and defender value must agree in addition to identity and case coverage.
- Terminal positions are a distinct status (null move is expected, not a failed
  search). Unsupported configurations (depth 10) are reported explicitly.
- Cases that cannot finish inside the fixed-work safety limit are reported as
  `deadline_limited` and are excluded from completed-work agreement instead of
  being silently passed or failed.
- `fixed_time` differences are informational warnings; they never fail a run.
- Latency ratios are only computed for equal completed work.

### Deterministic fixed-work agreement

| Comparison | Comparable cases | Limited | Best move | Value | Depth | Equal-work browser ÷ native median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| quick (depth 3), all three rulesets, seven kinds | 21 | 0 | 21/21 | 21/21 | 21/21 | 2.21× |
| standard (depth 5), Brandubh | 7 | 0 | 7/7 | 7/7 | 7/7 | 2.12× |
| standard (depth 5), Tablut + Copenhagen | 11 | 3 | 11/11 | 11/11 | 11/11 | 2.14× |

The three limited Copenhagen cases are `opening`, `midgame` and `repetition` at
depth 5: they reach depth 4 inside the 60 s fixed-work safety limit on **native
CPython** as well, so the workload is reported as limited on both sides rather
than being presented as a completed match. Tablut completes depth 5 on all
tested positions.

### Fixed-time workload comparison (informational)

`standard` (depth 5, 2 s) fixed-time runs across all three rulesets, two measured
repetitions per position. Fixed-time searches complete different work on different
runtimes, so these are reported as informational and are excluded from the
correctness gate; latency is never presented as an equal-work speedup.

| Ruleset | Native median | Native completed depths | Browser median | Browser completed depths | Limited cases |
| --- | ---: | --- | ---: | --- | ---: |
| Brandubh | 414 ms | 4–5 | 992 ms | 4–5 | 6/14 each |
| Tablut | 325 ms | 4–5 | 667 ms | 3–5 | 6/14 native, 6/14 browser |
| Copenhagen | 2000 ms | 3–5 | 2000 ms | 2–4 | 8/14 native, 12/14 browser |

The Copenhagen budget cap (2 s) dominates both sides, and the browser completes
shallower depths inside the same budget. Evidence:
[jarl-native-fixed-time-standard-r2.json](jarl-native-fixed-time-standard-r2.json),
[jarl-browser-fixed-time-standard-r2.json](jarl-browser-fixed-time-standard-r2.json).

### Lifecycle and responsiveness

| Metric | Quick run | Depth-5 Brandubh | Depth-5 Tablut+Copenhagen |
| --- | ---: | ---: | ---: |
| Startup to first response | 2688.6 ms | 2290.0 ms | 2097.0 ms |
| Cancellation acknowledgement | 0.2 ms | 0.2 ms | 0.2 ms |
| Worker restart after termination | 2086.8 ms | 2262.4 ms | 2169.8 ms |
| Batch wall clock (measured cases) | 16.0 s (21) | 31.9 s (7) | 272.5 s (14) |
| Click-to-handle during active search | 77.7 ms | 76.9 ms | 65.2 ms |
| Max event-loop lag during search | 25.0 ms | 20.4 ms | 18.9 ms |
| Median event-loop lag during search | 16.7 ms | 16.7 ms | 16.7 ms |

Responsiveness is measured by driving an actual Playwright click while the worker
is computing, plus a `requestAnimationFrame` event-loop lag probe — not a
synchronous DOM call. `batch_wall_clock_ms` is reported once per run and is never
attached to an individual case's latency.

### Resource metric terminology

The Pyodide worker reports `heap_capacity_bytes` (20 MiB) from the Emscripten
`HEAPU8` buffer. That is the observed WASM heap capacity, **not** process RSS and
not a hard ceiling. The neural worker separately reports JavaScript heap hints
when the browser exposes them and explicitly reports
`wasm_heap_capacity_bytes: null` with a note, because the pinned ORT Web build
does not expose its WASM heap size. Headless Chromium did not expose
`performance.memory`, so those fields are `null` rather than fabricated.

## Phase 1B — ONNX export and browser neural parity

### Artifact integrity and transactional export

`onnx_export.py` now exports into a temporary staging directory and publishes
only after the graph validates and (unless `--no-parity`) native parity passes.
A passing export writes `VALIDATED.json`; a failed or `--no-parity` export leaves
no validated marker, and `prepare_neural.py` refuses to stage it unless
`--allow-unvalidated` is passed for structural work.

| Metric | Value |
| --- | --- |
| Artifact | trained Brandubh **smoke** checkpoint (channels 4, blocks 0) |
| Model SHA-256 | `4054099991db2d9732c43d49ea562b8bed86d68a2a806307fca63100b6eae040` |
| ONNX graph size / gzip | 46,924 B raw / 6,915 B gzip estimate |
| Graph validation | `onnx.checker` + shape inference passed |
| Dynamic batch | verified at batch 1, 2, 8, 16, 32 |
| Reference cases | 39 (38 non-terminal incl. 32 distinct generated positions, 1 terminal) |
| Reference kinds | opening, midgame, endgame, tactical, king-escape, repetition, generated, terminal |

Parity is fail-closed: every output and every error metric must be finite, legal
probabilities use a numerically stable legal-only softmax, output tensors are
requested by name, and an empty reference set is rejected. Logit parity is judged
absolutely (a relative error on a logit near zero is meaningless); relative error
is judged on normalised legal probabilities above a floor.

| Parity stage | Cases | Worst logit abs error | Worst value abs error | Worst legal-probability relative error | Material ranking changes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native PyTorch → ORT CPU | 39 | 1.43e-6 | 5.96e-8 | — | 0 |
| Native PyTorch → browser ORT WASM | 39 | 2.15e-6 | 5.96e-8 | 9.9e-7 | 0 |

Browser WASM numerical parity is therefore established for this smoke model, not
merely "execution succeeded". `neural_parity.mjs --simulate-corruption` perturbs
one observed logit by 0.05 and the gate fails with
`logit abs error 0.05 > 0.001`, proving the gate detects regressions.

### Browser inference performance (smoke model, WASM)

| Metric | Value |
| --- | ---: |
| Runtime initialization (ORT + session) | 474.0 ms |
| First inference (cold, incl. tensor prep) | 19.0 ms |
| Warm single-position run median | 0.6 ms |
| Model download size (gzip estimate) | 6,915 B |

Distinct-position batched inference (batch sizes 1–32) matches independent
single-position inference exactly (worst logit/value/probability error 0):

| Batch | Distinct positions | Median | p90 | Positions/s |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 0.60 ms | 0.70 ms | 1,667 |
| 2 | 2 | 0.80 ms | 0.90 ms | 2,500 |
| 4 | 4 | 0.90 ms | 1.10 ms | 4,444 |
| 8 | 8 | 1.30 ms | 1.36 ms | 6,154 |
| 16 | 16 | 3.20 ms | 3.62 ms | 5,000 |
| 32 | 32 | 4.50 ms | 4.60 ms | 7,111 |

These are tiny-model numbers and are **not** extrapolatable to a representative
32×2 or larger competitive network, nor to MCTS simulation throughput.

### Provider reporting

The report separates the provider that was *requested*, the session placement that
was *observed*, and whether GPU execution was *verified*. Only a session whose
observed provider evidence lists WebGPU providers is reported as
`webgpu-session-verified`; a session created for WebGPU without observable
placement is `webgpu-session-unverified` (never labelled GPU-accelerated); an
observed WASM provider is `cpu-fallback`.

On this host `navigator.gpu` exists but `requestAdapter()` returns no adapter, so
a WebGPU request fails explicitly with
`WebGPU requested but no adapter is available` and `--require-webgpu` exits
non-zero. **WebGPU execution is unvalidated and blocked on physical hardware.**

## Repository checks

- `tests/test_offline_spike.py`: 14 passed (Phase 0/staging/bridge behaviour).
- `tests/test_browser_jarl_benchmark.py`: 24 passed (comparator, identity,
  custom positions, classification, per-case latency, duplicate names).
- `tests/test_browser_neural_parity.py`: 18 passed (fail-closed parity).
- `tests/test_browser_neural_export.py`: 18 passed with the optional ONNX
  dependencies installed (skip without them).
- `tests/test_browser_client_lifecycle.py`: 2 passed (16 Node lifecycle checks +
  provider-placement semantics).
- Real-browser gates run manually: `verify.mjs`, `verify_jarl.mjs`,
  `neural_parity.mjs`, `neural_benchmark.mjs` (WASM), all green; WebGPU blocked.

## Remaining validation and blockers

- Representative competitive trained weights for all three rulesets.
- Physical WebGPU execution on Windows/Linux/Android/Safari hardware.
- Physical desktop/mobile performance and real process memory.
- Long-run memory growth with a representative network.
- Playing strength (Phase 3).

No production browser analysis is enabled, no production limit or API changed and
no runtime dependency entered the application bundle.
