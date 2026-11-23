# Browser analysis — Phase 3C-A validation platform

Phase 3C-A builds reusable competitive-validation, tournament, memory and
physical-device infrastructure on merged Phase 3A/3B. It does **not** claim
competitive strength or physical-hardware acceptance. Relay remains the default
transport; direct transport, inference caching and independent batching remain
opt-in. Single-tree batching and subtree reuse remain deferred.

## Status

- Phase 3A: merged, software validated.
- Phase 3B: merged; direct/cache/batching experimentally validated and opt-in.
- **Phase 3C-A: implemented; external acceptance gates blocked.**
- Phase 3C as a whole is **not complete**.
- Competitive trained checkpoints, physical WebGPU/mobile/browser, sustained
  process memory and statistically meaningful playing-strength tournaments are
  **blocked** in this environment.

## 1. Remaining Phase 3B finding — strict detach acknowledgments

`NeuralClient.detachDirectPort()` now:

- accepts only a matching request id on the captured worker/generation;
- requires a `direct_port_detached` object;
- requires boolean `pending` and, when present, boolean `stale`;
- rejects contradictory `stale=true,pending=true`;
- treats worker `error` responses as failed cleanup;
- never synthesizes success from a missing value/error;
- clears the reservation only on confirmed safe detach;
- terminates/invalidates the affected worker if cleanup cannot be confirmed;
- never clears/terminates a newer reservation or generation;
- clears timers/listeners and settles exactly once.

There is exactly one `detachDirectPort()` implementation. Deterministic lifecycle
tests cover successful, stale, pending, malformed, rejected, timed-out,
generation-replaced, repeated and partial-attachment cases. Actual-browser tests
cover a genuinely dropped detach acknowledgment: the client terminates the
worker, reports no model, and a later load recovers.

## 2. Competitive checkpoint validation

`spikes/browser_offline/competitive_validation.py` is fail-closed:

- no checkpoint/export ⇒ `status: blocked`, no acceptance;
- validates sidecar/source fingerprint, ONNX digest, architecture, ruleset,
  encoder and validated-export marker through the existing pipeline;
- requires Level A, Level B and direct/relay parity plus benchmark records with
  matching model digest before `status: validated`;
- never invents weights or claims playing strength from inference throughput.

Owner workflow:

```sh
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt
.venv/bin/python spikes/browser_offline/onnx_export.py /private/last.pt /tmp/export
.venv/bin/python spikes/browser_offline/competitive_validation.py \
  --checkpoint /private/last.pt --export-dir /tmp/export \
  --output work/competitive-validation.json
# Run native/browser Level A/B and direct/relay parity/benchmarks using the
# established Phase 2/3 commands, then validate the collected report.
```

The committed blocked report contains no private paths or weights:
[competitive-validation-phase3c-blocked.json](competitive-validation-phase3c-blocked.json).

**Competitive-model gate: blocked.** No owner-trained Brandubh/Tablut/Copenhagen
checkpoints were available. Structural smoke fixtures are not competitive models.

## 3. Playing-strength tournament harness

`spikes/browser_offline/tournament.py` provides:

- role-balanced paired openings (A attacker/B defender, then swapped);
- one game record per opening/role leg with unique IDs;
- authoritative Python engine moves, full repetition history and terminal
  detection;
- per-agent and per-role wins/losses/draws, completed/truncated/error counts,
  game lengths, fixed settings/seeds;
- paired-opening bootstrap 95% CI (complete pairs only);
- strict validator replaying every move and checking role/model/summary
  consistency, missing/duplicate games, illegal moves, malformed winners and
  incomplete pairs; nonzero CLI on mandatory failures.

The harness is agent-agnostic so native Jarl/neural, browser adapter and
deterministic structural agents use the same scheduler and validator. No
competitive tournament was run in this environment; the playing-strength gate is
**blocked**, not inferred from inference performance.

Tests: `tests/test_tournament.py` covers structural paired games, role coverage,
statistics and all required negative validator cases. The committed
[structural harness evidence](tournament-harness-structural-phase3c.json) uses
scripted agents only and is explicitly **not** playing-strength evidence.

## 4. Sustained memory validation

`spikes/browser_offline/memory_validation.py` enforces:

- explicit profile and predeclared budgets;
- timestamp/nonnegative/counter validation;
- unavailable RSS must be `null` with an explanatory reason—never zero or
  substituted with JS heap/WASM capacity;
- post-warmup growth limits and budget checks;
- blocked profiles must state reasons and cannot claim acceptance.

A 10/30-minute owner profile was **not executed** in this environment. The
committed [blocked profile](memory-profile-phase3c-blocked.json) records that
gap. CI tests validate the monitoring/report contract; they do not claim sustained
memory stability.

## 5. Physical device and WebGPU evidence

`spikes/browser_offline/device_evidence.py` validates the device matrix and
provider evidence:

- physical platform, OS, browser, CPU/RAM, provider request/placement;
- WebGPU-requested→WASM fallback is rejected;
- `webgpu-session-verified` requires explicit GPU verification evidence;
- unexecuted platforms must be blocked with reasons.

Runbook and matrix are in [device-matrix-phase3c.md](device-matrix-phase3c.md).
No physical Windows/Android/Safari/WebGPU run was available. The committed
[blocked evidence](device-evidence-phase3c-blocked.json) marks those gates
outstanding. `--require-webgpu` and `--require-gpu-execution` were not weakened.

## 6. Copenhagen depth-5 acceptance

The three historical Copenhagen positions were re-measured natively with the
unchanged depth-5 Jarl configuration at larger bounded budgets:

| Position | 2 s | 5 s | 10 s | 30 s |
| --- | --- | --- | --- | --- |
| copenhagen-opening | depth 2 | depth 3 | depth 3 | depth 4 |
| copenhagen-midgame | depth 2 | depth 3 | depth 3 | depth 4 |
| copenhagen-repetition | depth 3 | depth 3 | depth 4 | depth 4 |

None completed depth 5 even at 30 s. Slow native execution is the limiting factor;
no browser-specific regression was observed because browser execution was not
rerun. The requirement remains **outstanding**, not silently reduced. Evidence:
[native 2/5/10 s](copenhagen-depth5-native-phase3c.json),
[native 30 s](copenhagen-depth5-native-30s-phase3c.json).

## 7. Test and compatibility summary

- Phase 3B detach strictness is covered by neural lifecycle tests (66/66); one
  `detachDirectPort()` implementation remains.
- Tournament harness/validator: 10 tests.
- Competitive validation: 4 tests.
- Memory validation: 5 tests.
- Device evidence: 3 tests.
- Existing Phase 3B browser/UI/parity/cache/batching tests remain the mandatory
  regression suite; no production API/auth/DB/CSP/frontend changes.
- Relay default; direct/cache/batching opt-in; sequential Python MCTS reference
  unchanged.

## 8. Remaining Phase 3C gates

Blocked: competitive trained checkpoints, role-balanced browser/native
tournaments, physical WebGPU execution verification, physical desktop/mobile/
Safari/Firefox, sustained 10/30-minute process-memory measurement, playing-strength
statistical claims, and the Copenhagen depth-5 completion decision. Phase 3C-B
must collect owner-supplied model/hardware evidence through this harness.
