# Dashboard polling and visibility policy

[Documentation index](../README.md) · [Dashboard](../DASHBOARD.md)

The existing polling hook now pauses recurring reads while hidden, aborts pending
loads, rejects late completions and refreshes on visibility return. Hold/accept,
account keys, one in-flight request, deadlines and failure backoff remain. An
initial background-opened route may still read once. Explicit security permission
invalidation remains active while hidden; no backend session/permission changes.

Dashboard historical single/local/recent reads use 60 seconds; its online overview
uses 10 seconds; the active hero stays at five seconds. Empty heroes stop ticking.
Compact standings use 60 seconds; actual participant online games keep five seconds.
A fixed backoff cap no longer accelerates a 60-second caller to 30 seconds.
Server hero versions invalidate the owning queue only when changed. Server result
aggregate changes refresh progress/standing/recent projections, including external
results and corrections. No client-derived winners/rewards/counts or shared cache
are introduced. English/German authoritative singular labels are corrected.

## Recorded production candidate measurement

One real isolated account/local game, three 60-second windows, 1920×1080 Chromium
156/Node24 on this four-CPU Linux host, Vite production preview:

| Requests per minute | Baseline | Candidate |
| --- | ---: | ---: |
| Empty Dashboard | 61 | 11 |
| Active local hero | 69 | 22 |
| Simulated hidden tab | 71 | 0 |

[Baseline](../benchmarks/arena-polling-before.json) and
[candidate record](../benchmarks/arena-polling-after.json). Every steady response
was HTTP200; no steady failures. Candidate startup had one expected reload abort.
The earlier candidate record uses a descriptive source label rather than a Git
build fingerprint; it is not proof of the final integrated production bundle.
The harness now preserves the audit's strict recorded-build/source/fingerprint
validation. Dirty or stale builds are rejected by default; `--allow-dirty` marks
an explicitly non-reproducible development run. Source-less archive builds remain
usable but cannot establish verified benchmark identity. No source override is
accepted. The optional bounded multi-context mode uses separate browser contexts
sharing one fixture account/session; it is not a distinct-player capacity test.

```sh
npm --prefix frontend run build
node frontend/benchmarks/arena-polling.mjs --output=/tmp/polling.json --clients=1
```

The runner uses disposable `HNEFATAFL_DATA_DIR` and fixture ports8001/8002 plus
preview4273, records no payloads/credentials and cleans its own services/data.
Hidden means a visibilityState override/event, not physical OS throttling. Counts
vary with latency/timer boundaries. This does not measure many-player SQLite
writes, production capacity or online deadline latency. The five-second participant
cadence is separately covered with fake timers.

## Validation

Candidate focused suite: **63 passed across four files**, TypeScript, formatting
and build passed. Integrated initial parallel run: **61 passed, two existing
20-second timeouts** under concurrent browser load; the unchanged serialized recheck passed all 63 tests in 7.28 seconds.
All eight recorded-build/source/fingerprint tests also passed.
No assertion or timeout was relaxed. Added tests cover hidden abort/late response,
wake/unmount/account switching, held mutations, slow cadence, unchanged live-game
cadence, request budgets, server version/result invalidation and singular labels.
Full locked-dependency repository/browser CI is required on the focused PR.

No backend/API/schema/game-rule/dependency changes. This optimizes polling, not
bundle size or database query complexity. Final merge/release acceptance is pending.

Verified clean build `d90f783`: five independent contexts produced 55 empty-window
requests and 115 active-window requests (11 and 23 per context), zero periodic
hidden requests. Steady failures: zero; p95 loopback completion 27.31/49.14 ms.
Five expected reload aborts occurred during startup. This first multi-context run
used nominal 60-second windows; the runner now also records actual elapsed window
time and normalized rates to reveal timer/scheduler delay. It is a shared-account
observation, not unique-player load or a capacity claim.

## Verified five-context result with actual window timing

Clean source/build `9bab822580e5afd784813ee2d957550394d41d46`, matching asset
fingerprint, no dirty override: [full record](../benchmarks/arena-polling-five-contexts.json).

| Phase | Actual elapsed | Total requests | Per context/minute | Failures | p95 completion |
| --- | ---: | ---: | ---: | ---: | ---: |
| Empty visible | 60,000.40 ms | 55 | 11.00 | 0 | 22.37 ms |
| Active visible | 59,999.92 ms | 115 | 23.00 | 0 | 28.51 ms |
| Simulated hidden | 60,000.64 ms | 0 | 0 | 0 | No requests |

All steady HTTP statuses were200. Five startup reload aborts remain excluded from
steady failure counts. Separate contexts still share one disposable account; this
is not eight distinct players, a population limit or a write/AI workload. The
normalized rates use actual monotonic elapsed time, not an assumed timer duration.
