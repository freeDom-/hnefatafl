# Current-engine browser analysis feasibility

Standalone spike only, based on main `2579a86b82ed1eca91f39e7d8064196597e873ed`.
No production entry, API, engine, model, schema or dependency is changed. The existing
Python engine/search runs in a dedicated Pyodide 0.27.7 worker; there is no JavaScript
rules implementation. [Machine-readable run](browser-analysis-prototype.json).

## Finding and correction

The historical spike staged only `__init__`, engine and AI. Current AI imports
openings, symmetry, tournament statistics and Shield maiden, so the old staging
fails with `ModuleNotFoundError: hnefatafl.openings`. The explicit stdlib-only
closure now stages seven current modules, with native/browser differential checks.
Pinned runtime downloads and supplied runtime copies are verified against the
previously recorded SHA-256 digests. Runtime assets remain outside the repository
in disposable `work/` or `/tmp` staging; they are not application dependencies.

Adds classical analysis using the existing `analyze_position`, legal moves and
terminal adjudication. The result envelope identifies `backend=local-python`,
`authoritative=false`, settings and full-history/settings position hash, containing
the canonical engine analysis object with defender-perspective values. It is an
experimental executor contract, not a deployed local/server selector.

## Bounded worker prototype

One worker/job at a time, generation/request guards, eight-entry exact state/history/
settings cache, input limit 512 KiB, native search time 0.01–0.5s, bounded depth/
simulations/rollouts, and hard request timeout at most 5s. Initialization has a 30s
limit. Cursor/game replacement cancels by terminating/recreating the worker.
Unsupported Workers, missing runtime and neural/model requests fail explicitly.
No package/model download or server fallback is invented by an analysis request.

The isolated page exposes current-position analysis, restore-position navigation,
legal moves, baseline AI moves and cancellation for experiments. Cancel controls
handle synchronous actions without an unhandled promise error. No official game,
clock, account, rating, achievement, tournament or league mutation is possible;
the verifier rejects any `/api/` request. This does not authorize live online advice
in the production app.

### Review follow-up corrections

- Displayed analysis is associated with the exact position and effective settings.
  New game, restore, human move and AI completion clear the pane and invalidate any
  in-flight render through the existing generation guard, so a superseded analysis
  cannot update the board or pane. Identical position/history/settings still reuse
  the cache, and cancellation never clears valid cache entries.
- Foreground gameplay takes precedence over background analysis. Human/AI moves, new
  game and restore cancel the active analysis without a manual Cancel click and
  terminate/recreate the worker; ordinary cache hits avoid the restart. Concurrent
  analyses are last-wins and the obsolete promise is discarded. Repeated cancellation
  and worker restart remain safe, and stale worker responses are ignored.
- Expected cancellation (`AbortError`) is presented as a cancelled state, distinct
  from unexpected failures. Position-dependent controls stay disabled until a position
  exists, and every async event handler and the initialization chain handle rejection.
- The 512 KiB limit is checked against the exact serialized payload using UTF-8 bytes
  before the worker boundary; Python re-checks independently. A synchronous
  `postMessage` failure clears the pending timer, pending request and busy flag.

## Fresh real-browser evidence

Chromium 156.0.8078.4, Linux cloud host, desktop 1920×1080 and mobile-emulated
390×844. The mobile profile requests 4× **page** CPU throttling; worker throttling
has not been proven. It is not a physical low-end phone benchmark. One sample per
profile; no percentile, calibrated strength or deployment-capacity claim.

| Metric | Desktop | Mobile emulation, requested 4× page throttle |
| --- | ---: | ---: |
| Fresh worker startup | 2040.8 ms | 2150.4 ms |
| Cached/offline fresh-worker startup | 1608.5 ms | 1681.3 ms |
| UI click while actual 500ms alpha-beta search is busy | 32.0 ms | 83.0 ms |
| Cursor cancellation acknowledgement | 0.4 ms | 1.1 ms |
| Restart after hard termination | 1566.3 ms | 1703.2 ms |
| Aggregate browser-process CPU lower bound, entire harness | 15.97 s | 25.48 s |

Startup excludes service-worker installation and does not model a WAN. Offline
means cached code/runtime and explicitly supplied fixture/replay positions; the
last game and production replay library are **not persisted**. No external/API
requests occur during the staged browser gate.

| Ruleset / algorithm | Desktop elapsed | Mobile-emulated elapsed | Completed work |
| --- | ---: | ---: | --- |
| Brandubh baseline | 2.2 ms | 2.1 ms | One-ply baseline |
| Brandubh alpha-beta | 74.4 ms | 73.8 ms | Completed depth 2 |
| Brandubh MCTS | 93.9 ms | 94.4 ms | 32 simulations |
| Tablut baseline | 5.7 ms | 5.2 ms | One-ply baseline |
| Tablut alpha-beta | 66.5 ms | 70.3 ms | Completed depth 2 |
| Tablut MCTS | 174.5 ms | 187.2 ms | 32 simulations |
| Copenhagen baseline | 11.6 ms | 12.7 ms | One-ply baseline |
| Copenhagen alpha-beta | 124.8 ms | 132.3 ms | Completed depth 2 |
| Copenhagen MCTS | 212.3 ms | 204.7 ms | Deadline-limited: 10 / 8 simulations, not the 32 target |

Each profile checks 34 current CPython rule/position/legal-move vectors before and
after offline reload (68 comparisons), repetition/terminal values and history-aware
cache separation. Three completed one-ply king-escape references match native best
moves and win immediately across all rulesets. This is a limited tactical accuracy
check, **not a broad playing-strength benchmark** or a claim that timed searches
complete equal work. MCTS shortfalls are reported explicitly.

Observed WASM heap capacity is approximately 20 MiB. This is neither actual RSS
nor a hard memory ceiling. Aggregate CPU excludes exited processes and includes
startup/all harness work; it cannot be interpreted as per-analysis CPU. The staged
20-file payload totals 14,158,507 raw bytes; summed fixed-gzip estimates 5,776,830
bytes are not measured compressed HTTP transfers. No neural weights are included.

## Reproduce

```sh
.venv/bin/python spikes/browser_offline/prepare.py --output /tmp/hnefatafl-analysis
node spikes/browser_offline/verify.mjs /tmp/hnefatafl-analysis
.venv/bin/python -m pytest tests/test_offline_spike.py
```

The first command explicitly downloads the pinned runtime; `--runtime-dir` reuses
a verified local copy. The verifier serves only its disposable directory on an
available loopback port and closes its own browser/server. `--shell-only` checks
the missing-runtime/error path without a runtime download. Native pytest alone
does **not** rerun the Wasm/browser proof. Original Phase 0 evidence remains
historical and unmodified.

### Continuous verification choice

The default PR CI keeps running `tests/test_offline_spike.py` (staging closure,
native bridge parity, UTF-8 sizing, bounded/neural refusal) with no runtime
download. The full Wasm gate stays a documented manual/opt-in command rather than
an ordinary-PR CI job: it needs a ~14 MB pinned Pyodide download plus Chromium and
several worker restarts, which is disproportionate for every pull request and
would make browser-runtime regressions hide behind slow, flaky infrastructure. The
staged runtime is digest-verified, and the manual command above is the reproducible
gate to run before merging changes to the worker, client or bridge.

## Review follow-up validation

Re-ran the staged browser gate after the lifecycle, scheduling and UTF-8 sizing
corrections. Both the desktop 1920×1080 and mobile-emulated 390×844 profiles passed
every original assertion (three rulesets, 68 native/browser fixture comparisons,
tactical king-escape parity, repetition/terminal outcomes, history-aware cache
identity, busy-search UI responsiveness, offline reload, no external/API requests)
and the added lifecycle cases:

| Follow-up check | Desktop | Mobile-emulated 4× |
| --- | --- | --- |
| Completed analysis cleared after position change | Passed | Passed |
| Restore while analysis running clears superseded result | Passed | Passed |
| Human move supersedes background analysis | Passed | Passed |
| New game supersedes background analysis | Passed | Passed |
| Repeated cancellation then reliable restart | Passed | Passed |
| Concurrent analyses: newest wins, obsolete discarded | Passed | Passed |
| Late/obsolete worker responses ignored | Passed | Passed |
| ASCII/multibyte/exact/over 512 KiB UTF-8 boundary | Passed | Passed |
| Synchronous `postMessage` failure clears pending state | Passed | Passed |

Cancellation still acknowledged with `AbortError`; hard-deadline termination now
raises a distinct `TimeoutError` so it is not reported as successful user
cancellation. Worker restart after termination measured ~2.0 s desktop and ~2.7 s
mobile-emulated. No external or `/api/` request was observed. These are single
observations on the same cloud host, not percentiles. The machine-readable
`browser-analysis-prototype.json` remains the original run and is not recast.

### Second review follow-up

The same desktop and mobile-emulated profiles passed the additional corrections:

| Second follow-up check | Desktop | Mobile-emulated 4× |
| --- | --- | --- |
| Early Cancel still registers the service worker and startup resolves | Passed | Passed |
| Rapid New Game recovery (newest wins) | Passed | Passed |
| Offline reload after cancelled/recovered startup | Passed | Passed |
| Cache-hit preemption cancels the slower search | Passed | Passed |
| Cached result, not the superseded search, stays displayed | Passed | Passed |
| Manual Cancel clears "Analyzing…" immediately and is idempotent | Passed | Passed |
| `AbortError` / `TimeoutError` / failure have distinct status | Passed | Passed |
| Obsolete analysis error cannot overwrite newer status | Passed | Passed |
| `postMessage` failure resets transport; replacement worker recovers | Passed | Passed |
| Worker startup failure followed by recovery | Passed | Passed |
| Idempotent, spike-scoped service-worker registration | Passed | Passed |
| Terminal Move/AI disabled across all three rulesets | Passed | Passed |
| Invalid/empty moves rejected; playable position restores controls | Passed | Passed |

Worker startup-failure recovery was exercised at the client level with a fake
transport; the app-level early-Cancel and rapid-New-Game recovery used the real
worker and an offline reload. No external or `/api/` request was observed.

### Third review follow-up

Both profiles also passed the final asynchronous-lifecycle corrections:

| Third follow-up check | Desktop | Mobile-emulated 4× |
| --- | --- | --- |
| Background cache miss does not preempt a foreground job | Passed | Passed |
| Background cache hit does not terminate a foreground job | Passed | Passed |
| App-level background analysis yields to a foreground job | Passed | Passed |
| Delayed fixture load cannot preempt or overwrite a newer game | Passed | Passed |
| Invalid analysis settings clear the pending indicator | Passed | Passed |
| Simulated worker transport failure clears the indicator and recovers | Passed | Passed |
| Repeated idle cancellation preserves the worker instance | Passed | Passed |
| Active cancellation still terminates and recovers | Passed | Passed |

Foreground priority was exercised both at the client level with a delayed fake
worker (deterministic message ordering) and at the app level with the real worker
probe. No external or `/api/` request was observed.

### Final review follow-up

The branch was merged with current `main` (`7d57c29`) and re-ran both profiles.
The staged Python engine closure is byte-identical to `main` (only unrelated
`online_core.py`/`online_ratings.py` changed upstream), no Pyodide asset enters
the production frontend, and the two frontend Playwright files the branch touched
now match `main`'s settled-geometry/retry versions.

| Final follow-up check | Desktop | Mobile-emulated 4× |
| --- | --- | --- |
| Obsolete error from a replaced worker is ignored | Passed | Passed |
| Replacement worker stays active and completes | Passed | Passed |
| Active worker error still rejects and resets the transport | Passed | Passed |
| Obsolete startup error from a replaced worker is ignored | Passed | Passed |
| Active startup error still fails fast and resets | Passed | Passed |

All previous lifecycle, caching, cancellation, priority, terminal-state and
offline-reload checks still passed on the merged branch. No external or `/api/`
request was observed.

## Production and neural decision

**Classical engine reuse is feasible experimentally; production integration is not
accepted yet.** Still required: normalized app executor/auto-local-server-disabled
UX, owner/account isolation, permission-context admission, real replay integration,
repeated search-strength/low-end/device tests, strict memory budget and process/RSS
measurement, storage eviction/recovery, production CSP/Wasm policy, license/notices,
explicit download/fallback policy and browser support. Do not add unsafe-eval or
ship this service worker to bypass these gates. No runtime belongs in initial app JS.

No actual trained model was exported to ONNX or run through ORT Web. Numerical,
legal-policy masking, architecture/hash compatibility, WASM/WebGPU memory/download
and search-quality parity are therefore unverified. Arbitrary PyTorch checkpoints
cannot be claimed browser-compatible. Neural execution remains optional research
and is not shipped. Existing server analysis and online permission denials remain
authoritative and unchanged.

## Repository checks

Fresh native spike tests: **9 passed** (the original 8 plus the UTF-8 size-boundary
case). Ruff check/format covers spike Python and
its tests; repository Ruff, frontend formatting, mypy (69 files), TypeScript and
production build passed. The unchanged production entry remains 1,013.70 kB
minified; its existing large-chunk warning is recorded rather than suppressed.
Full backend/frontend/browser CI runs separately on this focused PR; its actual
result belongs to that run, not to the standalone browser benchmark above.

The first full PR CI passed backend verification and all 1,312 frontend unit tests;
its browser run had 354 passed, 19 skipped and one existing resize-timing failure.
The same settled-geometry and transient-board retry corrections already green in
Portrait Hall CI are retained here without changing any layout thresholds. The
[neural/CSP report](browser-neural-feasibility.md) adds actual trained-native probes
and concrete production policy/runtime gaps; it does not claim browser inference.
