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
A bounded exponential backoff never retries a failed read sooner than its own
cadence: 5s/10s callers keep the historical 30-second ceiling, while a failing
60-second read backs off to 120 seconds and resets after success. Server hero
versions invalidate the owning queue only when changed. Server result aggregate
changes refresh progress/standing/recent/achievement projections, including
external results and corrections; per-side wins are part of the change signature
so a correction that keeps total wins/losses but moves the winning seat is still
detected. The active hero reaching or correcting a terminal result refreshes the
same projections once, covering local completion and single-player AI victories
without reading on ordinary moves. The unlocked-portrait count follows the
server-authoritative award list. A permissions-changed event that lands while a
hidden-tab abort is unwinding is revalidated instead of discarded, a
permission-initiated read aborted by hiding is reissued even while hidden, only a
security read clears the pending invalidation, and repeated events coalesce into
one follow-up read. No client-derived winners/rewards/counts or shared cache are
introduced. English/German authoritative singular labels are corrected.

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

Focused polling/auth/dashboard suite on the final integrated revision: **77 passed
across four files** (`useOnlinePoll`, `Auth`, `PersonalDashboard`, `Leaderboard`).
TypeScript, formatting and the production build passed. The twelve
recorded-build/source/fingerprint tests also passed. The full parallel frontend
run reported **1,364 passed** with no failures. No assertion or timeout was
relaxed. Added tests cover 5s/10s/60s failure backoff and recovery, hidden-tab
pausing and cleanup, a permission event during a hidden abort, a
permission-initiated read aborted by hiding, repeated permission events during an
active refresh, redundant visibility/focus suppression while a security read is
pending, local completion, single-player AI victory, active-hero terminal
transitions, side-specific rated corrections and stable-state idle behavior. Full
locked-dependency repository/browser CI is required on the PR.

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

## Integrated main plus fixes measurement

Clean source/build `6172baad8566904795c308813f5c59fda230c3bb`, matching complete
bundle fingerprint (`4cbf5bc2fe5fac24e4055b9a26715307b625a476fd44498418d0374977600ac6`),
no dirty override: [full record](../benchmarks/arena-polling-integrated-five-contexts.json).
This revision is the Dashboard-polling branch after merging current `main` and the
backoff, achievement-refresh and permission-invalidation fixes.

| Phase | Actual elapsed | Total requests | Per context/minute | Failures | p95 completion |
| --- | ---: | ---: | ---: | ---: | ---: |
| Empty visible | 60,000.86 ms | 55 | 11.00 | 0 | 121.84 ms |
| Active visible | 60,001.07 ms | 112 | 22.40 | 0 | 75.73 ms |
| Simulated hidden | 60,000.14 ms | 0 | 0 | 0 | No requests |

All steady HTTP statuses were200; five expected reload aborts during active
startup remain separate. The loopback p95 rose on this loaded four-CPU host
relative to the earlier run, so the request-count comparison is the meaningful
signal, not absolute latency. Five independent browser contexts still share one
disposable account/session: this is not a distinct-player population, capacity or
write/AI test.

```sh
npm --prefix frontend run build
node frontend/benchmarks/arena-polling.mjs --clients=5 \
  --output=docs/benchmarks/arena-polling-integrated-five-contexts.json
```

## Final integrated measurement

Clean source/build `00e5917d21167e75b566d61a836350ce7c1aacd4`, matching complete
bundle fingerprint (`7471f5dbe43c965786358a7949cc1910e043f6022ae317bc1391fabdfdcf0e9a`),
no dirty override: [full record](../benchmarks/arena-polling-final-five-contexts.json).
This revision merges the latest `main` (test-toolchain security) and the
permission-invalidation and achievement-invalidation fixes.

| Phase | Actual elapsed | Total requests | Per context/minute | Failures | p95 completion |
| --- | ---: | ---: | ---: | ---: | ---: |
| Empty visible | 60,000.03 ms | 55 | 11.00 | 0 | 18.57 ms |
| Active visible | 59,999.63 ms | 115 | 23.00 | 0 | 25.99 ms |
| Simulated hidden | 59,999.79 ms | 0 | 0 | 0 | No requests |

The steady request counts are unchanged from the earlier measurements, which is
expected: the new invalidations only fire on a terminal or corrected result, and
the benchmark's active game never completes. All steady HTTP statuses were200;
five expected reload aborts during active startup remain separate. Five
independent browser contexts still share one disposable account/session: this is
not a distinct-player population, capacity or write/AI test.

```sh
npm --prefix frontend run build
node frontend/benchmarks/arena-polling.mjs --clients=5 \
  --output=docs/benchmarks/arena-polling-final-five-contexts.json
```
