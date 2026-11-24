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

## 0.1 Fourth review correction (Q1–Q6)

| Finding | Correction | Regression proof |
| --- | --- | --- |
| Q1 | The collector now completes `--warmup-duration` before collecting the full `--duration` acceptance window (schema 3, timestamp-excluded warmup). It consumes live JSONL telemetry incrementally, associates values by Unix timestamp and session/model/workload identity, detects stale/missing/malformed/identity-mismatched telemetry, verifies the root process start time/cmdline (PID reuse), distinguishes browser workers from OS processes, and reports only telemetry-observed lifecycle events. | `tests/test_phase3c_workflow.py`: warmup/acceptance separation, live telemetry appended after start, identity change, stale/missing telemetry, process disappearance, PID-replacement, browser-worker vs OS-process distinction and observed events. The reviewed collector measured only the total duration and read telemetry files once. |
| Q2 | Process shutdown now terminates the owned process group/tree *before* stream cleanup, tracks writer threads as well as readers, closes stdin only when no writer is stuck, bounds cleanup (deadline + 2 s allowance + 1 s kill grace), reports cleanup/reaping failures, and uses POSIX sessions/Windows process groups. | `tests/bounded_adapter_probe.py` (external subprocess watchdog) starts a child that never reads stdin, spawns a descendant and writes a request larger than the pipe capacity: timeout, bounded cleanup, child reaped, descendant terminated, no thread leaks, same-instance recovery. The reviewed `_stop_generation()` closed buffered stdin before terminating the child. |
| Q3 | Competitive provenance now binds checkpoint metadata digest and distinguishes owner attestation from independently verified training facts. Competitive-trained requires ≥32 channels/≥2 residual blocks, rejects known structural-smoke checkpoints, and enforces an independent workload policy (all five position kinds, fixed-work budgets 128/256/512/1024, fixed-time 0.5/1/2/5 s, ≥2 repetitions, ≥1 warmup) using `compare_reports()` for native/browser workload/settings compatibility. | `tests/test_competitive_validation.py`: smoke relabelled competitive, insufficient architecture, missing/inconsistent checkpoint metadata and attestation, reduced quick benchmark workload, incompatible native/browser configurations and altered workload identity. |
| Q4 | Agents now use a version-2 initialization handshake and per-move evidence. Handshake reports kind/runtime/engine identity/algorithm/model/provider/depth; every move reports effective settings, seed, completed simulations/depth, completion reason and elapsed time. Mismatched command/config identities abort the tournament; fixed-work/fixed-time modes are explicit; unknown budgets cannot be equal-work evidence. | `tests/test_tournament_execution.py`: real native Jarl handshake/evidence integration, scripted-configured-as-Jarl rejection, incorrect adapter model identity, seed/provider/algorithm/depth/evidence mutations, and no playing-strength acceptance for invalid reports. Actual browser adapter run also validates. |
| Q5 | Device evidence now requires explicit `physical`/`emulated` provenance and a session identity, binds GPU session model/browser/runtime/workload/session identity, requires ordered observed cancellation timestamps, and keeps verified-GPU evidence through a code-controlled runtime registry that is empty for the pinned ORT Web runtime. Manually supplied matching operator lists cannot become acceptance. | `tests/test_device_evidence.py`: emulated matrix cannot satisfy physical acceptance, matching manual lists rejected as unsupported runtime claims, session/model/browser identity mismatches, missing/out-of-order cancellation timestamps. |
| Q6 | Memory validation enforces per-metric coverage (RSS ≥80 %, ≥5 usable; JS heap/cache/WASM/workers ≥80 %, ≥5 usable), longest usable-observation gaps, complete acceptance-window coverage, explicit inapplicability rules and telemetry-observed lifecycle events. Missing mandatory metrics make the result partial/blocked, never accepted. | `tests/test_memory_validation.py`: warmup-only RSS, shortened window, sparse JS heap, longest-gap, worker/cache/WASM coverage, manual lifecycle events, schema-3 window boundaries. |

## 0.2 Final targeted correction (T1–T5)

| Finding | Correction | Regression proof |
| --- | --- | --- |
| T1 | The adapter captures the owned process-group id and root start time at launch and terminates verified group members through pidfds (or verified PIDs), so cleanup no longer depends on the immediate child being alive. SIGTERM is escalated to SIGKILL after the grace period even if the parent exited; readers are joined before streams are closed, and a still-blocked reader is never synchronously closed. | `tests/bounded_tree_probe.py` (external watchdog) covers a parent that exits immediately, a parent exiting on SIGTERM with a SIGTERM-ignoring descendant, a descendant retaining stdout/stderr, bounded cleanup, no surviving owned group members/threads and same-instance recovery. |
| T2 | Execution evidence uses one explicit convention: absolute engine ply. The validator derives `starting_ply + relative replay index`, rejects out-of-range/duplicate/missing entries and recomputes `game_seed + absolute_ply`. | `test_noninitial_opening_execution_evidence_uses_absolute_ply` runs the real Jarl adapter on a midgame opening (ply > 0) and rejects ply/seed mutations. |
| T3 | The tournament mode is passed in request settings and supported-mode maps are enforced before launch. Fixed-work MCTS requires completed simulations; fixed-work alpha-beta requires completed configured depth. Fixed-time accepts deadline-limited alpha-beta depth and deadline/ceiling MCTS completion, reports actual depth/completed work and effective settings, and binds browser placement (`wasm`, WebGPU session placement, rejecting CPU fallback). | `tests/test_tournament_execution.py`: Jarl fixed-work, Jarl deadline-limited fixed-time, MCTS fixed-work/fixed-time, immediate-win, bad completion reasons, incomplete fixed-work, provider/placement mismatch and unsupported scripted fixed-time. |
| T4 | `_coverage()` now includes the gap from acceptance-window start to the first usable observation and from the last usable observation to window end, in addition to internal gaps, for every applicable mandatory metric. | `tests/test_memory_validation.py`: sufficient coverage with excessive leading/trailing gaps is partial, while a boundary gap exactly at the 12 s policy maximum is accepted. |
| T5 | Telemetry requires all three identity fields in every accepted record once identity is established; workload progress prefers the telemetry counter, records provenance, verifies file/telemetry consistency, and blocks acceptance on counter resets. Lifecycle events are derived only inside the acceptance window. History is bounded (4096 records) and no longer repeatedly sorted. | `tests/test_phase3c_workflow.py`: missing identity fields, mismatched-session progress, warmup-only events, acceptance-window events, counter reset, and a 5000-record bounded-history stream. |

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
    "encoder_version": "six-plane-v1",
    "training": {
      "classification": "competitive-trained",
      "checkpoint_metadata_sha256": "<digest of the checkpoint metadata>",
      "attestation": {
        "source": "owner",
        "statement": "Owner attests the training provenance.",
        "recorded_at": "2026-11-24T00:00:00Z"
      }
    }
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
is `structural-validated` with `competitive_acceptance: false`.

Training classification is never inferred from the manifest label. The verified
checkpoint metadata digest is bound to `provenance.training`, and known structural
smoke checkpoints (metadata name/note containing "smoke"/"structural") are rejected
for competitive classification. An owner attestation is explicitly reported as
`classification_source: owner-attestation` with
`training_facts_independently_verified: false`; it is not an independently verified
training fact. Competitive-trained evidence also requires at least 32 channels and
two residual blocks.

The competitive benchmark workload policy is independent of the workload's own
declared repetitions: all five position kinds (`opening`, `midgame`, `endgame`,
`tactical`, `repetition`), fixed-work budgets 128/256/512/1024, fixed-time budgets
0.5/1/2/5 s, at least two repetitions and one warmup run. Native and browser
reports are compared with `mcts_benchmark.compare_reports()`, so their workload
identity, effective settings and completed-work semantics must agree. A reduced
quick workload cannot satisfy competitive acceptance even when its own repetitions
are complete. A structural
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

Execution-evidence plies use one explicit convention: the absolute engine ply. For
a noninitial opening the validator derives each expected evidence ply as
`starting_ply + relative_replay_index` and the expected move seed as
`game_seed + absolute_ply`; evidence outside that range, duplicated or missing is
rejected.

Process protocol (persistent, newline-delimited JSON, version 2):

```text
handshake: {"kind": "handshake", "protocol": 2, "agent": {
             "kind": "native-jarl", "runtime": "native-cpython",
             "provider": "cpu", "algorithm": "alpha_beta", "depth": 5,
             "engine_sha256": "<64 hex>", "settings_support": {...}}}
request:   {"id": "<unique>", "state": {...}, "settings": {...}, "seed": 7}
response:  {"id": "<same>", "move": {"source": 0, "target": 1},
            "evidence": {"settings": {...}, "seed": 7, "algorithm": "alpha_beta",
                         "depth": 5, "completed_simulations": null,
                         "completion_reason": "completed", "elapsed_ms": 12.5,
                         "provider": "cpu", "model_sha256": null}}
```

The adapter requires the handshake before admitting the agent and compares kind,
runtime, provider, algorithm, depth, supported modes and
model/weight/ruleset/size identities with the declared configuration. A scripted
adapter configured as Jarl, an unsupported agent/mode combination, or any other
command/config mismatch aborts the tournament before play instead of being
recorded as a game failure.

`--mode fixed-work|fixed-time` is passed in the request settings, recorded in the
manifest and per game, and supported modes are declared per kind (`scripted`
supports structural fixed-work only; Jarl, Seer and neural/browser agents support
both). Fixed-work semantics are algorithm-specific:

- MCTS must report `completion_reason: simulations` with completed simulations
  equal to the requested budget (immediate-win/terminal shortcuts excepted).
- Alpha-beta must report `completion_reason: completed` at the configured maximum
  depth (immediate-win/terminal excepted).

Fixed-time semantics explicitly allow early legitimate completion: alpha-beta may
report a lower completed depth with `completion_reason: deadline`; MCTS may report
`deadline` below its simulation ceiling or `simulations` when it reaches the
ceiling before the deadline. Evidence reports actual completed depth/simulations,
effective time/exploration/iterations/max-depth settings and observed provider
placement. Browser evidence reports the observed placement; a WebGPU request with
WASM or `cpu-fallback` placement cannot satisfy WebGPU evidence. Unknown or
echo-only budgets are not equal-work evidence.

The adapter creates a new response queue per subprocess generation, rejects
stale/duplicate/unexpected/malformed responses, bounds responses and stderr, and
enforces one hard deadline over writes, reads and recovery. The owned process-group
id and root start time are captured at launch; shutdown signals verified group
members (pidfd where available), escalates SIGTERM to SIGKILL after the grace
period even when the immediate parent has already exited, joins writer/reader
threads within a bounded allowance and only then closes dead streams. A reader
still holding a stream lock is never synchronously closed. Reaping/thread failures
are reported in diagnostics. The same adapter instance recovers with a replacement
process; use it as a context manager (or call `close()`) to guarantee cleanup.

Platform limitation: on Windows the child is created in a new process group and
`taskkill /F /T` is used while the root process is alive. Windows has no equivalent
of POSIX process-group enumeration once the root exits, and no Job Object
integration is claimed here; descendant cleanup after parent exit is therefore
best-effort on Windows and not verified on physical Windows in this environment.
POSIX (Linux) cleanup is the verified path.

Agent configuration schema:

```json
{
  "label": "native-neural",
  "kind": "native-neural",
  "runtime": "native-cpython",
  "provider": "onnxruntime-cpu",
  "algorithm": "mcts",
  "model": {
    "onnx_sha256": "<64 hex>",
    "weights_sha256": "<64 hex>",
    "ruleset": "brandubh",
    "size": 7
  }
}
```

Kinds: `scripted`, `native-jarl`, `native-seer`, `native-neural`, `browser-jarl`,
`browser-neural`. Non-scripted kinds declare `algorithm` (`mcts` or `alpha_beta`);
alpha-beta also declares `depth`. Neural kinds require model identity; browser
kinds require `wasm` or `webgpu`; scripted agents must not declare a model. Playing-strength
evidence requires non-scripted agents, no failed/truncated games and at least ten
complete pairs; the validator reports eligibility, never a strength claim.

Collect and validate:

```sh
cat > /tmp/agent-a.json <<'JSON'
{"label":"native-jarl","kind":"native-jarl","runtime":"native-cpython","provider":"cpu","algorithm":"alpha_beta","depth":5,"model":null}
JSON
cat > /tmp/agent-b.json <<'JSON'
{"label":"browser-neural","kind":"browser-neural","runtime":"browser-pyodide","provider":"wasm",
 "algorithm":"mcts",
 "model":{"onnx_sha256":"<64 hex>","weights_sha256":"<64 hex>","ruleset":"brandubh","size":7}}
JSON

.venv/bin/python spikes/browser_offline/tournament.py --run \
  --ruleset brandubh --simulations 128 --time-limit 2 --seed 7 \
  --max-plies 200 --agent-timeout 30 --mode fixed-work \
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
  "schema": 3,
  "profile": "sustained-10m",
  "status": "measured",
  "warmup_ms": 60000,
  "duration_ms": 600000,
  "total_duration_ms": 660500,
  "budgets": {
    "max_process_rss_bytes": 1073741824,
    "max_js_heap_used_bytes": 536870912,
    "max_cache_bytes": 67108864,
    "max_wasm_heap_capacity_bytes": 134217728,
    "max_workers": 32
  },
  "features": {"inference_cache": true},
  "events": {"cancellations": 0, "worker_replacements": 0},
  "events_source": "telemetry",
  "telemetry": {"records": 600, "malformed": 0, "identity_mismatch": 0},
  "unavailable_metrics": {"js_heap_used_bytes": "not exposed by harness"},
  "samples": [
    {
      "timestamp_ms": 0,
      "wall_time_ms": 1700000000000,
      "process_rss_bytes": 123456789,
      "os_processes": 9,
      "js_heap_used_bytes": null,
      "wasm_heap_capacity_bytes": 67108864,
      "cache_entries": 12,
      "cache_bytes": 4096,
      "workers": 4,
      "completed_requests": 0,
      "telemetry": {"timestamp_ms": 1700000000000, "session_id": "s",
                    "model_sha256": "<64 hex>", "workload": "mcts-benchmark"},
      "telemetry_lag_ms": 2.5
    }
  ]
}
```

Rules enforced:

- `warmup_ms` is completed first and excluded by timestamp; `duration_ms` is the
  required post-warmup acceptance window, and the collector runs until that full
  window has been observed (schema 2 reports use index-based `warmup_samples`);
- at least ten samples, positive warmup, strict timestamps, sampling gaps no
  larger than one tenth of the sustained window, a complete acceptance window and
  no acceptance window that starts late or ends early;
- RSS is the only process-memory metric; JS heap/WASM capacity are never
  substituted; unavailable values stay `null` with a reason;
- every applicable mandatory metric (RSS, JS heap, cache entries/bytes when the
  cache is enabled, WASM capacity when budgeted/observed, browser workers) needs
  at least 80 % observation coverage, five usable observations and no usable-value
  gap longer than the sampling-gap policy. That gap check includes the window
  start→first usable observation and last usable observation→window end, so
  sufficient sample percentages cannot hide missing boundary telemetry. A declared
  budget is not proof that the metric was observed;
- acceptance requires usable RSS in at least 80 % of post-warmup samples and at
  least five usable observations; one RSS observation is never sufficient;
- missing mandatory metrics leave the result partial (structural but not accepted)
  or blocked; genuinely inapplicable metrics require an explicit feature/limitation
  declaration;
- `completed_requests` is mandatory on every post-warmup sample, monotonic and
  must advance across both halves of the window (idle/brief-activity reports
  fail);
- budgets cover RSS, JS heap, cache bytes, WASM capacity (when observed) and
  active worker count; growth is evaluated from zero baselines too;
- `cancel-restart`, `model-reload`, `worker-replacement` and
  `independent-batching` profiles require their corresponding lifecycle events,
  observed from live telemetry (`events_source: telemetry`) and derived only from
  records inside the acceptance window; warmup-only events do not count.
- every accepted telemetry record requires complete `session_id`,
  `model_sha256` and `workload` identity; a record missing an established field,
  changing session/model/workload, arriving malformed/out of order or resetting
  the completed-request counter is rejected. Acceptance requires progress from the
  live telemetry session (a compatibility progress file is recorded with its
  provenance and must match telemetry where claimed).
- `workers` is the actual browser-worker count from runtime telemetry;
  `os_processes` is reported separately and never used as the worker metric.
- telemetry history is bounded to 4096 records for timestamp alignment and
  diagnostics; live records are appended without re-sorting the whole stream.

Collection command (Linux `/proc`, Windows/macOS with `psutil`; process-tree RSS):

```sh
# Telemetry is appended live by the workload; the collector reads it incrementally.
# --duration is the acceptance window AFTER the 60 s warmup.
.venv/bin/python spikes/browser_offline/memory_profile_collect.py \
  --pid "$BROWSER_PID" --profile sustained-10m \
  --warmup-duration 60 --duration 600 --interval 1 \
  --progress-file /tmp/completed-requests.txt \
  --telemetry-file /tmp/browser-telemetry.jsonl \
  --telemetry-session-id "$SESSION_ID" \
  --telemetry-model-sha256 "$MODEL_SHA256" \
  --telemetry-workload mcts-benchmark --inference-cache \
  --output /tmp/memory-profile.json
.venv/bin/python spikes/browser_offline/memory_validation.py \
  /tmp/memory-profile.json --output /tmp/memory-validation.json

# The browser benchmark can produce this telemetry live:
node spikes/browser_offline/mcts_benchmark.mjs /tmp/neural-spike \
  --provider wasm --simulations 128,256 --time-budgets 0.5,1 \
  --telemetry /tmp/browser-telemetry.jsonl --telemetry-session "$SESSION_ID" \
  --telemetry-workload mcts-benchmark --output /tmp/browser-bench.json
```

`workers` requires the staged search client that exposes `workerStats()`; if it is
unavailable the value stays `null` with a reason and worker coverage prevents
acceptance. Headless Chromium does not expose `performance.memory`, so JS heap may
remain unavailable and identical collection runs finish partial rather than
fabricating a value.

A 10/30-minute profile was **not executed** here. CI validates the
monitor/report contract with simulated timestamps; it does not claim sustained
memory stability. The committed [blocked profile](memory-profile-phase3c-blocked.json)
records the gap.

## 5. Physical device and WebGPU evidence

`spikes/browser_offline/device_evidence.py` validates the mandatory
platform/provider matrix and derives results per combination:

- `structural_ok`: every measured record is complete, self-consistent, declares
  explicit `device_origin` (`physical`/`emulated`) and a session identity;
- `measurement_acceptance`: every mandatory combination is measured and honours
  its requested provider (an explicit CPU fallback is a valid measurement but
  never satisfies a WebGPU entry);
- `physical_device_acceptance`: every mandatory combination is measured on a
  physical device; browser mobile emulation never satisfies physical acceptance;
- `webgpu_session_acceptance`: every mandatory WebGPU entry actually created a
  WebGPU session;
- `verified_gpu_acceptance`/`acceptance`: every mandatory WebGPU entry carries
  independently instrumented per-operator placement evidence covering the actual
  executed graph (including fusion/optimization accounting) and matching session
  identity (model, ONNX digest, browser, runtime, workload, architecture,
  requested provider).

Contradictory combinations are rejected: WASM request without WASM placement,
silent WebGPU→WASM fallback, GPU claims for WASM/CPU/mixed/unverified placement,
and verified placement without independent evidence. Duplicate matrix entries are
rejected; additional optional devices are allowed but validated. Blocked records
need a reason. `all_operators_covered: true` is never trusted on its own — the
declared inventory must be covered exactly **and** be produced by runtime
instrumentation registered by code. Manually supplied matching inventory/placement
lists do not establish verification.

Verified-GPU evidence is admitted only through
`SUPPORTED_GPU_EVIDENCE_SOURCES` (an extension point for a future runtime with
supported placement diagnostics). It is intentionally empty for the pinned ORT Web
runtime, so claims such as `ort-web-per-operator-v1` are rejected as unsupported
and verified-GPU acceptance remains blocked. No profiler or new runtime is
implemented in this PR.

Physical records also require model/ONNX digests, architecture, workload, MCTS
budget, positive finite init/inference/throughput measurements, and ordered
observed cancellation timestamps (`cancel_requested_at_ms`,
`cancel_acknowledged_at_ms`, `restart_completed_at_ms`,
`post_cancel_inference_at_ms`) in addition to the boolean acknowledgement flags.

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

- Competitive validation: 19 tests, including the real CLI integration with a
  disposable checkpoint/export, genuine committed browser Level B and benchmark
  evidence, and training/architecture/workload-policy negatives.
- Persistent process agents: 11 tests, including externally watched
  blocked-write and parent-exit/SIGTERM-ignoring descendant cleanup probes.
- Tournament harness/validator: 28 tests; manifest-to-execution binding: 14 tests
  (noninitial ply/seed, real Jarl handshake/evidence, fixed-work/fixed-time modes,
  command/config mismatch, evidence mutations).
- Memory validation: 24 tests; collection-to-validation workflow: 12 CLI
  integration tests (warmup separation, live telemetry, identity/staleness,
  disappearance, PID replacement, boundary gaps, progress/event binding).
- Device evidence: 16 tests, including the complete unverified/CPU-fallback
  matrix, emulated-device rejection and unsupported GPU-evidence claims.
- Cross-validator malformed input: 8 tests here plus the existing 23-case
  malformed suite (and development fuzzing).
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
