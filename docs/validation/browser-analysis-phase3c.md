# Browser analysis — Phase 3C-A validation platform

Phase 3C-A builds reusable competitive-validation, tournament, memory and
physical-device infrastructure on merged Phase 3A/3B. It does **not** claim
competitive strength or physical-hardware acceptance. Relay remains the default
transport; direct transport, inference caching and independent batching remain
opt-in. Single-tree batching and subtree reuse remain deferred.

## Status

- Phase 3A: merged, software validated.
- Phase 3B: merged; direct/cache/batching experimentally validated and opt-in.
- **Phase 3C-A: implementation and structural integration validated; external
  acceptance gates blocked.**
- Phase 3C as a whole is **not complete**.
- Competitive trained checkpoints, physical WebGPU/mobile/Safari/Firefox,
  sustained 10/30-minute process memory, verified GPU execution and statistically
  meaningful playing-strength tournaments are **blocked** in this environment.
- Verified-GPU acceptance is blocked by missing instrumentation, not by missing
  code: the pinned ORT Web runtime still exposes no supported per-operator
  placement diagnostic.

## 0. Third review correction (N1–N8)

| Finding | Correction | Regression proof |
| --- | --- | --- |
| N1 | `competitive_validation.py` now defines every function before its module entry point; the public CLI runs checkpoint/export validation end-to-end. | `test_competitive_validation_cli_integration_with_real_export` invokes the real CLI with a disposable smoke checkpoint, a validated ONNX export and genuine structural Level A evidence; the old module raised `NameError` before this correction. |
| N2 | Evidence orchestration enforces the expected Level A/B level per gate, native/browser runtime identities, distinct runtimes for non-structural evidence, model/weight/ruleset/architecture/encoder binding, direct-relay mode+ruleset+model coverage, native+browser fixed-work/fixed-time benchmarks, and explicit provenance/acceptance policy. `--validate` is report inspection only and never claims verified acceptance. | `test_genuine_browser_level_b_evidence_passes_orchestration_gate` (committed genuine browser Level B observations), `test_level_gate_rejects_a_different_validation_level`, `test_validate_cli_never_claims_verified_acceptance`, manifest provenance/CLI negative tests. |
| N3 | `JsonProcessAgent` uses per-generation response queues, unique request IDs, generation filtering, bounded stdout/stderr, one hard deadline covering write+read+recovery, terminate+reap, stream/thread cleanup, a lifecycle context manager, concurrency rejection, recorded-deadline binding, and in-process SIGALRM restoration. | `tests/test_tournament_agents.py` exercises one adapter instance through success, timeout, replacement and success; stale/duplicate/EOF/malformed responses; blocking writes; excessive diagnostics; cleanup; concurrency. |
| N4 | Tournament manifests bind game IDs/pair indexes to expected openings, require identical opening state+history for both legs, validate deserialized rulesets, nonterminal openings, roles, agent configurations/model identity/runtime/provider, per-game algorithm/seed/budget/deadline, starting ply, absolute vs new moves, move limits, failed-game prefixes and reasons, terminal/move-limit outcomes, canonical pair ordering, and structural-vs-strength acceptance. | `tests/test_tournament.py` includes a regression for every item; the illegal-prefix test recomputes otherwise valid summary fields so replay itself rejects the prefix. |
| N5 | Memory validation requires timestamp-derived coverage, sufficient usable RSS observations and coverage, explained missing metrics, sustained progress throughout the window, worker/cache/WASM budgets, zero-baseline growth accounting, profile-specific lifecycle events, and separate structural/partial/acceptance outcomes. Blocked/partial profiles exit nonzero. | `tests/test_memory_validation.py` covers one RSS sample, missing heap/cache series, gaps, idle and half-window activity, zero-baseline growth, worker/cache/WASM limits, lifecycle events, blocked CLI exit 2. |
| N6 | Device acceptance is derived per mandatory platform/provider combination with provider-specific conditions, unique matrix entries, contradictory-combination rejection, independent operator inventory coverage, session/model/runtime/workload identity, cancellation/recovery evidence, and separate measurement/WebGPU-session/verified-GPU acceptance. | `tests/test_device_evidence.py` includes a complete measured matrix where every WebGPU record is unverified or CPU-fallback: structural validation succeeds, verified-GPU acceptance remains false. |
| N7 | All four validators and CLIs validate nested container types, identifier types, numeric types (including booleans/non-finite/negative values), digests and file/JSON errors; diagnostics are structured; checkpoint paths are not echoed. | `tests/test_phase3c_malformed_inputs.py` plus the public-function tests in each module (including 30,000-case nested-mutation fuzzing during development). |
| N8 | This report, the device runbook and the spike README document executable collection and validation commands plus schemas for export/parity/benchmarks/manifest, native+browser process tournament adapters, sustained-memory collection and the physical-device matrix. | `tests/test_phase3c_workflow.py` runs the tournament and memory collector CLIs end-to-end; competitive CLI integration runs export→evidence→validation. |

## 1. Strict detach acknowledgments (preserved)

`NeuralClient.detachDirectPort()` accepts only a matching request id on the
captured worker/generation, requires a `direct_port_detached` object, requires
boolean `pending` and (when present) boolean `stale`, rejects contradictory
`stale=true,pending=true`, treats worker errors as failed cleanup, never
synthesizes success, clears the reservation only after confirmed detach,
terminates/invalidates the worker otherwise, and settles exactly once. There is
one implementation; deterministic and actual-browser lifecycle tests cover
successful, stale, pending, malformed, rejected, timed-out and generation-replaced
cases.

## 2. Competitive checkpoint validation

`spikes/browser_offline/competitive_validation.py` is fail-closed. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | The requested validation/acceptance actually succeeded (validated or structural-validated). |
| 1 | Invalid or failed evidence. |
| 2 | Blocked or unavailable required evidence. |

Two layers are deliberately separate:

- `--validate REPORT` performs **structural report inspection** only. The result
  carries `verification: report-inspection`, `independent_verification: false`,
  `acceptance: false` and `competitive_acceptance: false`, even when the inspected
  document claims acceptance. It cannot turn a synthetic summary into verified
  acceptance.
- `--checkpoint ... --export-dir ... --report MANIFEST` recomputes checkpoint
  digest/weight fingerprint and validates the ONNX export, then re-runs every
  supplied observation through `mcts_compare.py`, `path_parity.py` and
  `mcts_benchmark.py`. Only this path reports
  `verification: independently-verified-evidence` and can set acceptance.

Acceptance policy and provenance are explicit, not inferred from a label:

```json
{
  "kind": "browser-competitive-evidence",
  "schema": 2,
  "validation_level": "competitive-trained",
  "acceptance_policy": "competitive",
  "provenance": {
    "training_status": "competitive-trained",
    "checkpoint_sha256": "<64 hex>",
    "onnx_sha256": "<64 hex>",
    "source_weights_sha256": "<64 hex>",
    "ruleset": "brandubh",
    "architecture": {"channels": 32, "blocks": 2},
    "encoder_version": "six-plane-v1"
  },
  "evidence": {
    "level_a": {"native": "native-a.json", "browser": "browser-a.json"},
    "level_b": {"native": "native-b.json", "browser": "browser-b.json"},
    "direct_relay": {
      "fixed": "direct-relay-fixed.json",
      "fixed_time": "direct-relay-fixed-time.json",
      "fixed_brandubh": "direct-relay-fixed-brandubh.json"
    },
    "benchmarks": ["native-bench.json", "browser-bench.json"]
  }
}
```

`structural-smoke` evidence must use `acceptance_policy: structural`; its result
is `structural-validated` with `competitive_acceptance: false`. A structural
manifest whose label/policy is edited to `competitive-trained` is rejected because
Level B, direct/relay and benchmark evidence are missing. Additional
`direct_relay` documents are supported; keys `fixed`, `fixed_*` imply fixed work
and `fixed_time`, `fixed_time_*` imply fixed time. Every document must cover the
validated ruleset/model and both modes must be present.

Required workload/budget/repetition/coverage checks are delegated to the existing
comparators, which independently reconstruct the workload, coverage, budgets and
bootstrap-independent identities. The orchestrator additionally requires:

- the report level equals the gate (`level_a` → Level A, `level_b` → Level B);
- native evidence declares a native runtime and browser evidence a browser
  runtime; non-structural evidence cannot reuse one runtime;
- Level B model metadata matches the export ONNX digest, source-weight
  fingerprint, ruleset and size;
- benchmark records come from `mcts-benchmark` reports whose root model matches
  the export; both native and browser reports contain fixed-work and fixed-time
  modes and cover the model ruleset;
- direct/relay evidence uses the `direct-relay-compact` gate, correct expected
  ruleset/model/size coverage and per-case model digest.

Owner workflow:

```sh
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt
.venv/bin/python spikes/browser_offline/onnx_export.py /private/last.pt /tmp/export

# State/benchmark/parity collectors (see README for the full command set)
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level a \
  --iterations 128 --output /tmp/native-a.json
node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
  --native /tmp/native-a.json --level a --output /tmp/browser-a.json
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level b \
  --evaluator onnx --model-export /tmp/export --iterations 256 \
  --output /tmp/native-b.json
node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
  --native /tmp/native-b.json --level b --provider wasm --output /tmp/browser-b.json
.venv/bin/python spikes/browser_offline/native_mcts_benchmark.py \
  --model-export /tmp/export --evaluator onnx --quick --output /tmp/native-bench.json
node spikes/browser_offline/mcts_benchmark.mjs /tmp/neural-spike \
  --provider wasm --quick --native /tmp/native-bench.json --output /tmp/browser-bench.json
node spikes/browser_offline/test_search_path_parity.mjs /tmp/neural-spike \
  --gate direct-relay-compact --output /tmp/direct-relay-fixed.json
node spikes/browser_offline/test_search_path_parity.mjs /tmp/neural-spike \
  --gate direct-relay-compact --fixed-time --output /tmp/direct-relay-fixed-time.json

# Write the manifest above, then:
.venv/bin/python spikes/browser_offline/competitive_validation.py \
  --checkpoint /private/last.pt --export-dir /tmp/export \
  --report /tmp/evidence-manifest.json --output /tmp/competitive-validation.json
.venv/bin/python spikes/browser_offline/competitive_validation.py \
  --validate /tmp/competitive-validation.json
```

The committed [blocked report](competitive-validation-phase3c-blocked.json)
contains no private paths or weights.

Fresh structural integration evidence (genuine native and browser collection,
disposable smoke checkpoint/export kept outside the repository):
[manifest](phase3c-evidence-manifest.json),
[native Level A](phase3c-native-a.json), [browser Level A](phase3c-browser-a.json),
[native Level B](phase3c-native-b.json), [browser Level B](phase3c-browser-b.json),
and the independently verified [structural validation](phase3c-competitive-validation.json)
(`status: structural-validated`, `acceptance: false`,
`competitive_acceptance: false`, both gates accepted with independent runtimes).
This is structural smoke evidence, not competitive-model acceptance.

**Competitive-model gate: blocked.** No owner-trained Brandubh/Tablut/Copenhagen
checkpoints were available. Structural smoke fixtures are not competitive models.

## 3. Playing-strength tournament harness

`spikes/browser_offline/tournament.py` provides role-balanced paired openings
(A attacker/B defender, then swapped), one game per opening/role leg, authoritative
Python-engine replay, per-game settings/seed/budget/deadline, absolute ply and new
moves, move limits, paired bootstrap 95% CI, and a strict validator.

The canonical manifest records: ruleset, agent labels and complete agent
configuration, settings, and ordered openings with pair index, state and starting
ply. Each game ID `pair-<index>-<ab|ba>` must bind to that pair's opening and the
scheduled seed `seed + pair*100 + swap`. Both legs must use the same exact opening
state and repetition history. Failed games are replayed over their valid move
prefix, and terminal reasons/move-limit outcomes are recomputed. Summary totals,
role/agent statistics and bootstrap values are recomputed from the games after
canonical pair ordering.

Process protocol (persistent, newline-delimited JSON):

```text
request:  {"id": "<unique>", "state": {...}, "settings": {...}, "seed": 7}
response: {"id": "<same>", "move": {"source": 0, "target": 1}}
```

The adapter creates a new response queue per subprocess generation, rejects
stale/duplicate/unexpected/malformed responses, bounds responses and stderr, and
enforces one hard deadline over writes, reads and recovery. Timed-out or
protocol-breaking children are terminated and reaped. The same adapter instance
recovers with a replacement process; use it as a context manager (or call
`close()`) to guarantee cleanup.

Agent configuration schema:

```json
{
  "label": "native-neural",
  "kind": "native-neural",
  "runtime": "native-cpython",
  "provider": "onnxruntime-cpu",
  "model": {
    "onnx_sha256": "<64 hex>",
    "weights_sha256": "<64 hex>",
    "ruleset": "brandubh",
    "size": 7
  }
}
```

Kinds: `scripted`, `native-jarl`, `native-neural`, `browser-jarl`,
`browser-neural`. Neural kinds require model identity; browser kinds require
`wasm` or `webgpu`; scripted agents must not declare a model. Playing-strength
evidence requires non-scripted agents, no failed/truncated games and at least ten
complete pairs; the validator reports eligibility, never a strength claim.

Collect and validate:

```sh
cat > /tmp/agent-a.json <<'JSON'
{"label":"native-jarl","kind":"native-jarl","runtime":"native-cpython","provider":"cpu","model":null}
JSON
cat > /tmp/agent-b.json <<'JSON'
{"label":"browser-neural","kind":"browser-neural","runtime":"browser-pyodide","provider":"wasm",
 "model":{"onnx_sha256":"<64 hex>","weights_sha256":"<64 hex>","ruleset":"brandubh","size":7}}
JSON

.venv/bin/python spikes/browser_offline/tournament.py --run \
  --ruleset brandubh --simulations 128 --time-limit 2 --seed 7 \
  --max-plies 200 --agent-timeout 30 \
  --agent-a-command ".venv/bin/python spikes/browser_offline/native_tournament_agent.py --engine jarl --depth 5" \
  --agent-b-command "node spikes/browser_offline/browser_tournament_agent.mjs /tmp/neural-spike --provider wasm" \
  --agent-a-config /tmp/agent-a.json --agent-b-config /tmp/agent-b.json \
  --evidence-class playing-strength --output /tmp/tournament.json

.venv/bin/python spikes/browser_offline/tournament.py /tmp/tournament.json \
  --output /tmp/tournament-validation.json
```

The README contains the shorter structural command that uses the same adapters
with `--engine scripted` and `--evidence-class structural`. The committed
[structural harness evidence](tournament-harness-structural-phase3c.json) uses
scripted agents only and is explicitly **not** playing-strength evidence.

A genuine browser-adapter collection run (native scripted vs. the persistent
browser neural adapter on the staged smoke model, four-ply move limit,
`simulations=4`) is preserved as
[browser adapter structural evidence](tournament-browser-structural-phase3c.json).
It exercises the real adapter lifecycle and the same validator, remains
`structural_only`, contains truncated (move-limit) games and is **not**
playing-strength evidence.

No competitive tournament was run in this environment; the playing-strength gate
is **blocked**, not inferred from inference throughput.

## 4. Sustained memory validation and collection

`spikes/browser_offline/memory_validation.py` reports three outcomes separately:
`structural_ok`, `measurement_ok` and `acceptance_ok`/`acceptance`. Its CLI exits
0 only for acceptance, 2 for blocked/partial and 1 for invalid reports.

Schema (`schema: 2`):

```json
{
  "kind": "browser-analysis-memory-profile",
  "schema": 2,
  "profile": "sustained-10m",
  "status": "measured",
  "duration_ms": 600000,
  "budgets": {
    "max_process_rss_bytes": 1073741824,
    "max_js_heap_used_bytes": 536870912,
    "max_cache_bytes": 67108864,
    "max_wasm_heap_capacity_bytes": 134217728,
    "max_workers": 32
  },
  "features": {"inference_cache": true},
  "warmup_samples": 1,
  "events": {},
  "unavailable_metrics": {"js_heap_used_bytes": "not exposed by harness"},
  "samples": [
    {
      "timestamp_ms": 0,
      "process_rss_bytes": 123456789,
      "js_heap_used_bytes": null,
      "wasm_heap_capacity_bytes": null,
      "cache_entries": 12,
      "cache_bytes": 4096,
      "workers": 4,
      "completed_requests": 0
    }
  ]
}
```

Rules enforced:

- at least ten samples, positive warmup, strict timestamps, sampling gaps no
  larger than one tenth of the sustained window;
- RSS is the only process-memory metric; JS heap/WASM capacity are never
  substituted; unavailable values stay `null` with a reason;
- acceptance requires usable RSS in at least 80 % of post-warmup samples and at
  least five usable observations; one RSS observation is never sufficient;
- every budgeted metric needs sufficient usable observations or an explicit
  unavailable reason (which makes the measurement partial, not accepted);
- `completed_requests` is mandatory on every post-warmup sample, monotonic and
  must advance across both halves of the window (idle/brief-activity reports
  fail);
- budgets cover RSS, JS heap, cache bytes, WASM capacity (when observed) and
  active worker count; growth is evaluated from zero baselines too;
- `cancel-restart`, `model-reload`, `worker-replacement` and
  `independent-batching` profiles require their corresponding lifecycle events.

Collection command (Linux `/proc`, Windows/macOS with `psutil`; process-tree RSS):

```sh
# Point --pid at the browser (or workload) process tree root and --progress-file
# at the completed-request counter the workload updates.
.venv/bin/python spikes/browser_offline/memory_profile_collect.py \
  --pid "$BROWSER_PID" --profile sustained-10m --duration 600 --interval 1 \
  --progress-file /tmp/completed-requests.txt \
  --js-heap-file /tmp/js-heap.jsonl --cache-bytes-file /tmp/cache-bytes.jsonl \
  --events-json '{}' --output /tmp/memory-profile.json
.venv/bin/python spikes/browser_offline/memory_validation.py \
  /tmp/memory-profile.json --output /tmp/memory-validation.json
```

A 10/30-minute profile was **not executed** here. CI validates the
monitor/report contract with simulated timestamps; it does not claim sustained
memory stability. The committed [blocked profile](memory-profile-phase3c-blocked.json)
records the gap.

## 5. Physical device and WebGPU evidence

`spikes/browser_offline/device_evidence.py` validates the mandatory
platform/provider matrix and derives results per combination:

- `structural_ok`: every measured record is complete and self-consistent;
- `measurement_acceptance`: every mandatory combination is measured and honours
  its requested provider (an explicit CPU fallback is a valid measurement but
  never satisfies a WebGPU entry);
- `webgpu_session_acceptance`: every mandatory WebGPU entry actually created a
  WebGPU session;
- `verified_gpu_acceptance`/`acceptance`: every mandatory WebGPU entry carries
  independent per-operator placement evidence covering a declared operator
  inventory and a matching session identity (model, ONNX digest, runtime,
  workload, architecture, requested provider).

Contradictory combinations are rejected: WASM request without WASM placement,
silent WebGPU→WASM fallback, GPU claims for WASM/CPU/mixed/unverified placement,
and verified placement without independent evidence. Duplicate matrix entries are
rejected; additional optional devices are allowed but validated. Blocked records
need a reason. `all_operators_covered: true` is never trusted on its own — the
declared inventory must be covered exactly.

Physical records also require model/ONNX digests, architecture, workload, MCTS
budget, positive finite init/inference/throughput measurements, and explicit
cancellation evidence (`cancel_acknowledged`, `restart_completed`,
`post_cancel_inference`).

Runbook and matrix: [device-matrix-phase3c.md](device-matrix-phase3c.md). No
physical run was available. The committed
[blocked evidence](device-evidence-phase3c-blocked.json) marks those gates
outstanding, and the CLI exits 2 (not success) for a structurally valid but
unverified matrix. `--require-webgpu` and `--require-gpu-execution` were not
weakened.

## 6. Copenhagen depth-5 acceptance

The three historical Copenhagen positions were re-measured natively with the
unchanged depth-5 Jarl configuration at larger bounded budgets:

| Position | 2 s | 5 s | 10 s | 30 s |
| --- | --- | --- | --- | --- |
| copenhagen-opening | depth 2 | depth 3 | depth 3 | depth 4 |
| copenhagen-midgame | depth 2 | depth 3 | depth 3 | depth 4 |
| copenhagen-repetition | depth 3 | depth 3 | depth 4 | depth 4 |

None completed depth 5 even at 30 s. Native execution is the limiting factor; the
requirement remains **outstanding**, not silently reduced. Evidence:
[native 2/5/10 s](copenhagen-depth5-native-phase3c.json),
[native 30 s](copenhagen-depth5-native-30s-phase3c.json).

## 7. Test summary

- Competitive validation: 15 tests including the real CLI integration with a
  disposable checkpoint/export and genuine committed browser Level B and benchmark
  evidence.
- Persistent process agents: 7 same-instance lifecycle/protocol tests.
- Tournament harness/validator: 28 tests.
- Memory validation: 17 tests; collection workflow test.
- Device evidence: 14 tests including the complete unverified/CPU-fallback matrix.
- Cross-validator malformed input: 8 tests here plus the existing 23-case
  malformed suite (and development fuzzing).
- Collection-to-validation workflow: 3 CLI integration tests.
- Existing Phase 3B browser/UI/parity/cache/batching, neural lifecycle and detach
  regressions remain mandatory. Relay default; direct/cache/batching opt-in;
  sequential Python MCTS reference unchanged.

## 8. Remaining Phase 3C gates

Blocked: competitive trained checkpoints, role-balanced browser/native
tournaments, physical WebGPU execution verification, physical
desktop/mobile/Safari/Firefox, sustained 10/30-minute process-memory measurement,
playing-strength statistical claims, and the Copenhagen depth-5 completion
decision. Phase 3C-B must collect owner-supplied model/hardware evidence through
these commands; no further platform redesign is required.
