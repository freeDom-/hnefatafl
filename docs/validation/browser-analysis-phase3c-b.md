# Browser analysis — Phase 3C-B competitive measurements and readiness

Phase 3C-B applies the merged Phase 3C-A validation platform to representative
browser-analysis runs. It records actual structural measurements, resolves the
remaining V1 provider-evidence finding, and states clearly which competitive,
sustained-memory, physical-device and GPU gates remain blocked.

**No competitive trained checkpoint, physical device or verified GPU runtime was
available in this environment.** The measurements below use fresh, disposable
32-channel / 2-block smoke checkpoints created by `smoke_checkpoint.py`. They are
synthetic structural-representative models, not competitively trained models, and
are never presented as playing-strength evidence.

## A. Acceptance matrix

| Gate | Coverage | Measurement status | Acceptance | Evidence | Blocker / next action |
| --- | --- | --- | --- | --- | --- |
| V1 provider-evidence consistency | Brandubh browser WASM | Validated | Met | `tests/test_tournament_execution.py::test_offline_wasm_evidence_cannot_claim_verified_gpu_execution` | Owner: none |
| Competitive checkpoint provenance | None (no owner checkpoints) | Blocked | Not met | [blocked report](phase3cb-competitive-blocked.json) | Owner supplies private trained checkpoints per ruleset |
| ONNX export/numerical parity | 32×2 synthetic, all rulesets | Measured | Structural/partial only (not competitive) | `phase3cb-*` parity evidence | Competitive weights required |
| Level A search parity | All three rulesets, 21/21 exact | Measured | Structural pass | [browser A](phase3cb-browser-a.json), [native A](phase3cb-native-a.json) | Competitive weights required for 3C-B acceptance |
| Level B model-backed parity | 7/7 best-move parity each ruleset | Measured | Structural pass with synthetic model | [Brandubh](phase3cb-browser-b-brandubh.json), [Tablut](phase3cb-browser-b-tablut.json), [Copenhagen](phase3cb-browser-b-copenhagen.json) | Competitive weights required |
| Fixed-work benchmark matrix | 128/256/512/1024, opening+tactical, 1 repetition | Measured | Partial: competitive policy requires all five kinds and ≥2 repetitions | [Brandubh](phase3cb-browser-bench-brandubh.json) and other ruleset reports | Owner full policy workload |
| Fixed-time benchmark matrix | 0.5/1/2/5 s | Measured | Partial as above | comparison reports | Owner full policy workload |
| 1,024 simulations < 2 s | Brandubh 2009 ms; Tablut 2712 ms; Copenhagen 3667 ms | Measured | Not met as a general claim (Brandubh approximately at target, others miss) | benchmark reports | Smaller/faster models or runtime optimization; not a merge gate |
| Browser vs native neural MCTS tournament | 2 pairs / 4 games, synthetic model | Measured, structural only | Not met (0 complete pairs; 3 truncated) | [report](phase3cb-tournament-browser-vs-native-neural.json) | ≥50 meaningful pairs with competitive model |
| Browser vs native Jarl tournament | 2 pairs / 4 games | Measured, structural only | Not met (1 complete pair, 2 truncated) | [report](phase3cb-tournament-browser-vs-native-jarl.json) | Competitive model + full sample |
| 10-minute sustained profile | Brandubh 32×2, real 600 s post-warmup | Measured | Partial/blocked (WASM capacity unobservable) | [profile](phase3cb-memory-10m-profile.json), [validation](phase3cb-memory-10m-validation.json) | Runtime metric exposure or documented partial acceptance |
| 30-minute sustained profile | Brandubh 32×2, real 1800 s post-warmup | Measured | Not met (JS heap growth threshold; compatibility-file consistency) | [profile](phase3cb-memory-30m-profile.json), [validation](phase3cb-memory-30m-validation.json) | Investigate JS heap step; rerun with telemetry-only progress |
| Lifecycle profiles | Cancel/restart, model reload, worker replacement | Measured | Partial/blocked (WASM unobservable; worker replacement RSS growth) | `phase3cb-memory-*-profile.json` | Owner lifecycle acceptance with observable metrics |
| Physical device matrix | None | Blocked | Not met | [blocked matrix](phase3cb-device-evidence-blocked.json), [runbook](device-matrix-phase3c.md) | Physical Windows/Linux/macOS/Android/Safari runs |
| Verified GPU execution | No WebGPU adapter | Blocked | Not met | [capability report](phase3cb-webgpu-capability-blocked.json) | Runtime with supported placement diagnostics + physical GPU |
| Copenhagen depth-5 | Native, 2/5/10/30 s | Measured | Not met | [reassessment](phase3cb-copenhagen-depth5.json) | Owner decision on adaptive target |

## B. Software delivered in Phase 3C-B

1. **V1 provider-evidence consistency.** Live process-agent validation and saved
   tournament-report validation now share `_provider_evidence_failures()`.
   WASM/CPU/mixed/unverified observations cannot claim verified GPU execution;
   GPU flags are strict booleans; contradictory observation fields are rejected;
   no-inference shortcuts must report no placement; native CPU providers remain
   valid. A mutation of genuine committed WASM evidence claiming
   `gpu_execution_verified: true` is rejected offline and by the shared live check.
2. **Executable native neural tournament adapter.** `native_tournament_agent.py`
   gained `--engine neural --checkpoint ... --model-export ...`, using the
   production neural MCTS and checkpoint loader. It reports the ONNX/weight/model
   identity, `torch-cpu` provider, MCTS simulations/inference counts, effective
   settings, seeds and completion reasons through the version-2 handshake and
   per-move evidence contracts. Browser vs native neural MCTS is now executable.
3. **Sustained workload driver.** `sustained_workload.mjs` runs repeated real
   browser neural-MCTS searches and optional cancellation, model reload or worker
   replacement, emitting live session/model/workload telemetry and progress. It is
   measurement tooling only.
4. **Narrow memory-validator correction.** `cache_bytes` growth is bounded by the
   declared cache budget rather than a general 25 %/1 MiB growth threshold; cache
   filling is expected, while exceeding `max_cache_bytes` still fails.
5. **Owner runbook** below and refreshed evidence links; no production UI, model
   delivery, rules, search algorithm or security-boundary changes.

## C. Competitive model results

No competitive trained checkpoints were available. For every ruleset a fresh
32×2 smoke checkpoint was created and exported through the validated ONNX pipeline.
These models have no competitive strength and are labelled `structural
representative` (synthetic training, 12 steps) throughout.

| Ruleset | Encoder | Architecture | Native/browser Level B | Placement |
| --- | --- | --- | --- | --- |
| Brandubh 7×7 | six-plane-v1 | 32 ch / 2 blocks | 7/7 best-move parity | WASM |
| Tablut 9×9 | six-plane-v1 | 32 ch / 2 blocks | 7/7 best-move parity | WASM |
| Copenhagen 11×11 | six-plane-v1 | 32 ch / 2 blocks | 7/7 best-move parity | WASM |

Level A shared-driver parity passed 21/21 exactly across all rulesets. Level B
comparisons accepted execution validity, workload completion and best-move parity;
the comparison reports are committed with the browser reports. These establish
software correctness for a representative architecture, **not** competitive-model
parity or strength.

## D. Native/browser performance

Measurements are native ONNX CPU versus browser Pyodide/ORT Web WASM, same 32×2
model, same positions (opening/tactical quick workload), one measured repetition
(warmup 1), relay transport, compact legal-only inference, cache disabled. Medians
over the two quick positions:

| Ruleset | Cold model load | Equal-work browser/native ratio | 128 sims | 256 sims | 512 sims | 1024 sims |
| --- | --- | --- | --- | --- | --- | --- |
| Brandubh | 3,756 ms | 0.291 | 393 ms | 541 ms | 1,138 ms | 2,009 ms |
| Tablut | 3,525 ms | 0.503 | 500 ms | 661 ms | 1,259 ms | 2,712 ms |
| Copenhagen | 4,998 ms | 0.449 | 672 ms | 1,342 ms | 1,862 ms | 3,667 ms |

Fixed-time completed work (median simulations):

| Ruleset | 0.5 s | 1 s | 2 s | 5 s |
| --- | --- | --- | --- | --- |
| Brandubh | 252 (native 678) | 404 (native 1,910) | 1,009 (native 3,719) | 2,558 (native 4,096 ceiling) |
| Tablut | 218 (native 558) | 434 (native 1,187) | 750 (native 2,166) | 1,568 (native 4,096 ceiling) |
| Copenhagen | 152 (native 287) | 310 (native 646) | 492 (native 867) | 1,256 (native 3,014) |

Equal-work ratios are reported only where both runtimes completed the same work.
The 1,024-simulation aspirational target is approximately met for the smallest
board (2.0 s on Brandubh) but missed for Tablut (2.7 s) and Copenhagen (3.7 s)
with this synthetic representative model. Browser throughput is dominated by
inference/transport rather than Python search; the measured ratio range is
0.29–0.50. These are partial-policy measurements: competitive acceptance requires
all five position kinds and at least two repetitions, which were not run here.

### Optimization comparisons (Brandubh, 32×2, 256/1024 simulations)

| Variant | 256 opening | 1024 opening | 256 tactical | 1024 tactical |
| --- | --- | --- | --- | --- |
| Relay compact (baseline) | 604 ms | 2,027 ms | 458 ms | 1,868 ms |
| Direct transport | +4.1 % | +28.9 % | +7.4 % | +8.1 % |
| Exact cache enabled | -19.4 % | -41.5 % | +43.4 % | +30.0 % |
| Full-logit response | +155.5 % | +130.8 % | +74.2 % | +45.6 % |

- **Relay remains the default.** Direct transport was slower in this run and had
  no reproducible gain across Phase 3B/3C-B.
- **Cache is workload-dependent.** Repeated opening workloads benefited strongly
  at 1024 simulations (cache hit rate 80.3 %, 4.1 MB of 64 MB); the tactical case
  was slower, so caching stays opt-in.
- **Full-logit responses are consistently slower**; compact legal-only inference
  remains the search path.
- **Independent batching** completed both contexts, but the 64-simulation test was
  slower when batched (765 ms) than sequential (477 ms) on CPU/WASM. Keep it
  opt-in; batch utilization is reported in `phase3cb-batching-*.json`.

## E. Playing-strength tournaments

Both required primary comparisons are executable with the new native neural
adapter. The runs below are structural smoke runs with the synthetic 32×2 model
and must not be interpreted as strength results:

| Comparison | Mode | Games | Completed | Truncated | Errors | Complete pairs |
| --- | --- | --- | --- | --- | --- | --- |
| Browser neural vs native neural (same model) | fixed-work 8 simulations, ≤12 plies | 4 | 1 | 3 | 0 | 0 |
| Browser neural vs native Jarl | fixed-time 2 s, ≤12 plies | 4 | 2 | 2 | 0 | 1 |

Truncated games are not rule draws. With 0–1 complete pairs and a non-competitive
model, no score, Elo or superiority claim is made. The validator's paired bootstrap
requires at least ten complete pairs for playing-strength eligibility; the target
for owner runs is 50 pairs (100 games) per primary comparison.

## F. Sustained memory and runtime stability

Real browser runs, 32×2 Brandubh, continuous search (64 simulations per request),
cache enabled, telemetry emitted by the live browser workload. The acceptance
window excludes the 60 s warmup.

| Profile | Acceptance window | Requests | RSS median/peak | JS heap median/peak | Cache peak | Workers | Validation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sustained-10m | 600 s | 9,086 | 930.9 / 971.2 MB | 9.5 / 9.5 MB | 12.6 MB | 2 | partial: WASM capacity unavailable |
| sustained-30m | 1,800 s | 25,445 | 947.4 / 979.1 MB | 9.5 / 19.6 MB | 12.6 MB | 2 | invalid: JS-heap growth threshold; compatibility progress lag; WASM unavailable |
| cancel-restart | 90 s | 2,143 | 907.3 / 945.1 MB | 9.5 / 9.5 MB | 12.5 MB | 2 | partial: WASM unavailable |
| model-reload | 90 s | 1,760 | 877.7 / 957.4 MB | 9.5 / 9.5 MB | 0.3 MB | 2 | partial: WASM unavailable |
| worker-replacement | 90 s | 391 | 863.7 / 981.8 MB | 9.5 / 9.5 MB | 0.5 MB | 1–2 | invalid: RSS growth threshold; WASM unavailable |

Observations:

- Process RSS stayed roughly 930–979 MB for the 10/30-minute runs; no monotonic
  unbounded process growth was observed. The 30-minute validation still fails its
  declared growth threshold because the JS heap stepped from 9.5 MB to 19.6 MB at
  about 17 minutes and then plateaued. That step is a real finding; acceptance is
  not claimed.
- Browser worker count stayed at 2 for sustained/reload/cancel profiles and
  fluctuated 1–2 during worker replacement; OS process count stayed at 7, and is
  recorded separately.
- Cache stayed within the 64 MB budget (12.6 MB peak); cache hits and bytes are
  reported by the actual inference cache.
- Cancellation/restart completed 1,017 observed cancellations with recovery;
  model reload completed 113 observed reloads; worker replacement completed 16
  observed replacements with recovery.
- The 30-minute run recorded eight transient progress-file/telemetry mismatches.
  Progress acceptance uses the live telemetry counter exclusively; the compatibility
  progress file was retained for diagnostics and the run remains non-accepted.
- WASM heap capacity is not exposed by the pinned ORT Web runtime, so these
  profiles remain partial/blocked rather than accepted. No substitute metric was
  used.

## G. Physical devices, WebGPU and Windows

- **Physical devices: blocked.** No Windows, Linux Firefox, Android or Safari
  hardware was available. The mandatory matrix remains blocked with reasons in
  [phase3cb-device-evidence-blocked.json](phase3cb-device-evidence-blocked.json);
  emulation is not physical evidence.
- **WebGPU: blocked.** The environment reports `null adapter`; no WebGPU session
  was created. See [webgpu-capability-blocked.json](phase3cb-webgpu-capability-blocked.json).
  No verified-GPU claim is made, and the fail-closed evidence registry is
  unchanged.
- **Windows subprocess cleanup:** not physically verified. Windows descendant
  cleanup after the immediate parent exits remains best-effort; stale `taskkill`
  is never used. Linux process-group/token ownership remains the verified path.
  A Windows Job Object subsystem is deferred to a separate focused PR.

## H. Copenhagen depth-5 reassessment

| Position | 2 s | 5 s | 10 s | 30 s |
| --- | --- | --- | --- | --- |
| copenhagen-opening | depth 3 | depth 3 | depth 3 | depth 4 |
| copenhagen-midgame | depth 3 | depth 3 | depth 3 | depth 4 |
| copenhagen-repetition | depth 3 | depth 3 | depth 4 | depth 4 |

At 2/5/10/30 s all three positions remain deadline-limited and do not complete
depth 5; 30 s reaches depth 4 at best. Elapsed times track the requested budgets
(deadline overshoot is small relative to the budget).

**Recommendation:** keep Copenhagen depth 5 explicitly blocked; do not silently
lower production Jarl strength. If a practical target is required, the next
approved step should be adaptive iterative deepening with a bounded practical
deadline (or an owner-approved revised depth/time requirement), evaluated as a
separate change.

## I. Owner-side Phase 3C-B runbook

Private checkpoints, ONNX files and training data must remain outside the
repository. The commands below are copyable; substitute private paths locally.

```sh
# Environment
uv sync --frozen --extra train --extra dev
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt
npm --prefix frontend ci && npx --prefix frontend playwright install chromium

# 1. Competitive checkpoint provenance and validated ONNX export
.venv/bin/python spikes/browser_offline/onnx_export.py /private/brandubh.pt /private/export-brandubh
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /private/export-brandubh --output /private/spike-brandubh

# 2. Numerical and search parity (repeat for tablut/copenhagen)
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level a \
  --iterations 128 --output /tmp/native-a.json
node spikes/browser_offline/mcts_parity.mjs /private/spike-brandubh \
  --native /tmp/native-a.json --level a --output /tmp/browser-a.json
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level b \
  --evaluator onnx --model-export /private/export-brandubh \
  --checkpoint /private/brandubh.pt --rulesets brandubh --iterations 256 \
  --output /tmp/native-b.json
node spikes/browser_offline/mcts_parity.mjs /private/spike-brandubh \
  --native /tmp/native-b.json --level b --provider wasm --output /tmp/browser-b.json
node spikes/browser_offline/test_search_path_parity.mjs /private/spike-brandubh \
  --gate direct-relay-compact --output /tmp/direct-relay-fixed.json
node spikes/browser_offline/test_search_path_parity.mjs /private/spike-brandubh \
  --gate direct-relay-compact --fixed-time --output /tmp/direct-relay-fixed-time.json

# 3. Full competitive benchmarking (native and browser; all kinds/repetitions)
.venv/bin/python spikes/browser_offline/native_mcts_benchmark.py \
  --model-export /private/export-brandubh --checkpoint /private/brandubh.pt \
  --evaluator onnx --extended --simulations 128,256,512,1024 \
  --time-budgets 0.5,1,2,5 --output /tmp/native-bench.json
node spikes/browser_offline/mcts_benchmark.mjs /private/spike-brandubh \
  --provider wasm --simulations 128,256,512,1024 --time-budgets 0.5,1,2,5 \
  --repetitions 2 --warmup 1 --native /tmp/native-bench.json \
  --output /tmp/browser-bench.json

# 4. Competitive manifest and validation (see Phase 3C-A schema)
.venv/bin/python spikes/browser_offline/competitive_validation.py \
  --checkpoint /private/brandubh.pt --export-dir /private/export-brandubh \
  --report /tmp/evidence-manifest.json --output /tmp/competitive-validation.json

# 5. Tournaments (browser vs native neural, and browser vs native Jarl)
cat > /tmp/native-neural.json <<'JSON'
{"label":"native-neural","kind":"native-neural","runtime":"native-cpython","provider":"torch-cpu","algorithm":"mcts","model":{"onnx_sha256":"<64 hex>","weights_sha256":"<64 hex>","ruleset":"brandubh","size":7}}
JSON
cat > /tmp/browser-neural.json <<'JSON'
{"label":"browser-neural","kind":"browser-neural","runtime":"browser-pyodide","provider":"wasm","algorithm":"mcts","model":{"onnx_sha256":"<64 hex>","weights_sha256":"<64 hex>","ruleset":"brandubh","size":7}}
JSON
.venv/bin/python spikes/browser_offline/tournament.py --run --ruleset brandubh \
  --limit 50 --simulations 256 --time-limit 2 --seed 7 --max-plies 200 \
  --mode fixed-work --agent-timeout 60 \
  --agent-a-command ".venv/bin/python spikes/browser_offline/native_tournament_agent.py --engine neural --checkpoint /private/brandubh.pt --model-export /private/export-brandubh" \
  --agent-b-command "node spikes/browser_offline/browser_tournament_agent.mjs /private/spike-brandubh --provider wasm" \
  --agent-a-config /tmp/native-neural.json --agent-b-config /tmp/browser-neural.json \
  --evidence-class playing-strength --output /tmp/tournament.json
.venv/bin/python spikes/browser_offline/tournament.py /tmp/tournament.json

# 6. Sustained memory (10/30 minutes; warmup is additional)
node spikes/browser_offline/sustained_workload.mjs /private/spike-brandubh \
  --duration 690 --mode repeated --session owner-10m --provider wasm \
  --telemetry /tmp/telemetry-10m.jsonl --progress /tmp/progress-10m.txt \
  --iterations 128 --time-limit 2 --cache --output /tmp/workload-10m.json &
BROWSER_PID=$!
sleep 10
.venv/bin/python spikes/browser_offline/memory_profile_collect.py --pid "$BROWSER_PID" \
  --profile sustained-10m --warmup-duration 60 --duration 600 --interval 1 \
  --telemetry-file /tmp/telemetry-10m.jsonl --telemetry-session-id owner-10m \
  --telemetry-model-sha256 "<64 hex>" --telemetry-workload sustained-analysis \
  --inference-cache --progress-file /tmp/progress-10m.txt \
  --output /tmp/memory-10m.json
.venv/bin/python spikes/browser_offline/memory_validation.py /tmp/memory-10m.json
# Repeat with --duration 1890, --profile sustained-30m, --duration 1800 for 30 minutes.

# 7. Physical device matrix (see device-matrix-phase3c.md for every platform)
.venv/bin/python spikes/browser_offline/device_evidence.py /private/device-evidence.json \
  --output /private/device-validation.json
```

Structural smoke mode: the existing quick benchmark/parity commands and short
2-pair tournaments remain appropriate for CI and software checks; they are not
full acceptance. Full owner runs need private checkpoints, physical hardware and
the complete 10/30-minute windows.

## J. Readiness recommendation

| Area | Status |
| --- | --- |
| Phase 3C-B software (V1, native neural adapter, sustained driver, runbook) | Implemented and structurally validated |
| Competitive model acceptance | Blocked: no competitive checkpoints |
| Playing-strength acceptance | Blocked: synthetic model, insufficient pairs |
| Sustained-memory acceptance | Blocked/partial: WASM metric unavailable; 30-minute JS-heap threshold not met |
| Physical-device acceptance | Blocked: no physical hardware |
| Verified GPU execution | Blocked: no adapter and no supported placement diagnostic |
| Copenhagen depth-5 | Blocked for Copenhagen; recommendation documented |
| Phase 4 production integration | Not started; no model delivery or production UI work in this phase |

Phase 3 remains **not fully validated** while these external gates are open. This
PR is the evidence-driven conclusion of the feasible Phase 3C-B work in this
environment, not a claim of competitive or hardware acceptance.
