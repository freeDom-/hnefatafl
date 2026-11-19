# Disposable backend read workload

[Documentation index](../README.md) · [Architecture](../../ARCHITECTURE.md)

## Scope

This optional Linux benchmark seeds a fresh temporary SQLite database with real
HTTP-authorized games, eight verified player accounts and an administrator. It
never accepts an operator database, checkpoint or model path. It measures warm
loopback GETs only; it performs no AI search, training, inference, mixed
authoritative writes or production query/index/schema changes. Results are
observations, not capacity claims.

## Supported invocation and parameter bounds

From a clean committed checkout, using installed locked Python dependencies:

```sh
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  flock -n /run/lock/hnefatafl-tests.lock \
  timeout --kill-after=15s 600s nice -n 10 \
  .venv/bin/python scripts/benchmark_backend_reads.py \
  --output /tmp/hnefatafl-read-evidence-new
```

Bounds are enforced before any fixture work: `--users` 2–16, `--rounds` 1–10,
`--port` 1024–65535, and 1–5 distinct `--concurrency` levels each no larger than
the user count. `--source-root` must be the runner's own checkout; a separate
backend revision is not supported. `--data-dir` is only valid together with the
internal `--serve` worker flag. The output directory must not already exist, so
prior evidence is never overwritten. Coordinate the shared lock across
worktrees and do not run alongside builds, browser suites, training or other
benchmarks.

The default is three rounds of thirteen bounded GET endpoints for each of eight
distinct accounts, at concurrency 1, 4 and 8 (936 measured requests). Each
account keeps at most one in-flight request. Fixture writes, semantic
verification and warm-up reads are excluded from measured phases.

## Environment and storage isolation

Both the driver and the internal worker independently establish a deterministic
benchmark environment before importing any application module, and both verify
the imported backend location. Every `HNEFATAFL_*`, proxy and numeric-thread
variable is snapshotted, removed, replaced with benchmark-local values and
restored afterwards, even when the body raises. This covers the data directory,
development environment, loopback public URL and allowed hosts, trusted
proxies, session lifetime, account request budgets, online feature settings,
registration/SMTP (disabled, so no real mail or external network), and
two-thread numeric limits. Operator settings such as a production
`HNEFATAFL_PUBLIC_URL` cannot redirect or invalidate the local workload.

The fixture directory is created with mode `0700`, must be an absolute,
non-symlink, user-owned directory whose name starts with `hnefatafl-load-`, and
must contain a regular `.benchmark-only` marker with a 32-character hex
identity. The worker rejects a `--data-dir` that does not match
`HNEFATAFL_DATA_DIR`, so the internal server mode cannot initialize arbitrary
operator storage. Only the current process's owned fixture directory is removed;
a changed marker or identity makes cleanup refuse. The benchmark never opens or
modifies existing operator databases, models, checkpoints or training jobs.

## Server identity

The API process binds `127.0.0.1` on an exclusively owned listening socket. It
does not use `SO_REUSEPORT`, so a live unrelated listener makes the bind fail
rather than being reused; `SO_REUSEADDR` only reclaims `TIME_WAIT` addresses and
`TCP_NODELAY` is inherited by accepted sockets to avoid a Nagle/delayed-ACK
artifact. The parent probes the existing public `/api/health` route and only
proceeds when a fixture-only response header matches the unpredictable fixture
identity; that check happens before any credentials are sent. The parent never
signals a process it did not start.

## Cancellation and cleanup

A cooperative cancellation event is checked before every request and during
startup, authentication, fixture creation and warm-up. Measurement runs in
synchronized waves of at most the configured concurrency; on cancellation the
current barrier is aborted, queued futures are cancelled, running workers stop
before their next request, and the driver waits a bounded time for outstanding
requests. HTTP clients are registered for cleanup immediately after creation,
before login. In a `finally` path the driver terminates the API child (escalating
from `SIGTERM` to `SIGKILL` after a bounded grace), reaps it, closes every HTTP
client and the log handle, closes SQLite connections explicitly, and removes only
the owned fixture. Subprocess termination is attempted even if client cleanup
fails. Partial evidence is still written, and an incomplete run is never reported
as a successful baseline. Cleanup after an uncatchable `SIGKILL` or abnormal host
termination cannot be guaranteed.

## Source and dependency provenance

The runner records the clean repository revision, the imported backend location
verified in both processes, the runner script SHA-256, the `uv.lock` SHA-256, and
the installed Python, SQLite, FastAPI, Uvicorn, HTTPX and Pydantic versions. The
revision is re-checked after measurement, so a source change invalidates the
evidence. A clean Git checkout does not prove the installed dependencies match
the lockfile; the report records the lock fingerprint and states explicitly that
this is not a lock check. Unverifiable or misleading provenance is rejected.

## Fixture coverage and semantics

The fixture uses supported API operations and legal game transitions only. Each
player account owns three local pass-and-play games (one completed by a genuine
eight-ply repetition draw) and two in-progress single-player games. Each player
also has one active, one completed (eight-ply repetition draw) and one waiting
online game, so every player has two completed rated draws. Two human
tournaments are seeded: a started eight-entrant bracket and an empty upcoming
registration.

Before measurement, untimed verification checks that logins correspond to the
expected seeded accounts, that player accounts are distinct and
non-administrator, that local and single-player records belong to the expected
owner, that current online games have the expected status and participants, that
completed games and replays carry the expected authoritative result, moves and
states, that online statistics, leaderboard entries and rating events reflect the
completed results, that tournament discovery, entrants, matches and legs are
present, and that recorded history contains the completed games. Authoritative
table counts must match expectations for the configured number of users, or the
benchmark fails instead of reporting a baseline. Single-player statistics remain
an unfinished-game workload because no AI search is run; the report says so
explicitly and does not imply completed single-player outcome coverage.

## Concurrency and success reporting

The report distinguishes configured client concurrency, the maximum observed
outstanding requests from distinct authenticated accounts, the number of
successfully authenticated accounts, and total/success/failed request counts.
Actor indexes are only equated with identities because each login was verified
against the seeded account. Synchronized waves make the configured concurrency
observable, but client-side overlap is not presented as proof of simultaneous
backend handler execution. Every actor, endpoint and concurrency level is
validated against its expected request count. Underachieved concurrency, missing
requests or any non-200 response marks the run incomplete and fails the
benchmark after evidence is written.

## Environment metadata

The report records the CPU model, logical and affinity CPU counts, effective CPU
quota when available, effective memory limit with its source, Python and SQLite
versions, FastAPI/Uvicorn/HTTPX/Pydantic versions, numeric thread settings, the
lock fingerprint, benchmark parameters and fixture configuration. Unavailable
Linux resource values are `null`, distinct from a measured zero. No credentials,
cookies, tokens, private configuration, full environment dump or response body
is recorded.

## Hardened measured baseline

Revision `4504605dbfd7d02072dd17a4ee9497af781f706c` on
`perf/backend-read-baseline`, observed `2026-11-19T09:39:36Z`, passed all 936
measured requests (HTTP 200); the observed simultaneous account counts matched
1/4/8 and every expected request count was validated. Fixture: nine accounts, 40
local/single-player games, 32 online games, 64 accepted moves, eight results and
rating events, two tournaments, eight entrants, fourteen matches and eight legs.
A thin fixture-identity HTTP middleware is included in these observations.

See the [hardened summary](../benchmarks/backend-read-baseline-hardened.json),
[all raw samples](../benchmarks/backend-read-requests-hardened.json) and
[non-default parameter checks](../benchmarks/backend-read-parameter-checks.json).

| Distinct-account concurrency | Requests | Elapsed seconds | Median ms | p95 ms | p99 ms | API CPU seconds | API RSS KiB after phase |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 312 | 2.027 | 6.25 | 10.16 | 11.58 | 1.81 | 66,128 |
| 4 | 312 | 1.768 | 21.86 | 32.64 | 41.13 | 3.19 | 69,872 |
| 8 | 312 | 1.750 | 45.36 | 60.01 | 65.34 | 3.75 | 73,704 |

The summary was independently recalculated from its raw samples: request counts,
nearest-rank p95/p99, medians, response-byte totals and status counts all match.
The two-account (26 requests per level) and four-account (104 requests per level
at concurrency 1/2/4) checks also completed with all requests accounted for.

## Historical evidence (pre-hardening)

The original PR #97 baseline was collected before these isolation, lifecycle,
provenance and verification changes, so it is retained only as historical
evidence and must not be read as a hardened-benchmark result. It used revision
`b755fdb4251804817b310e2885ff029399e64e2b` and the same nominal workload:
936 measured requests (HTTP 200), observed overlap 1/4/8.

| Distinct-account concurrency | Requests | Elapsed seconds | Median ms | p95 ms | p99 ms | API CPU seconds | API RSS KiB after phase |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 312 | 1.928 | 5.84 | 9.74 | 10.81 | 1.71 | 62,012 |
| 4 | 312 | 1.780 | 22.59 | 33.17 | 39.18 | 3.37 | 65,744 |
| 8 | 312 | 1.680 | 43.96 | 57.61 | 91.66 | 3.74 | 70,164 |

Its [summary](../benchmarks/backend-read-baseline.json) and
[all samples](../benchmarks/backend-read-requests.json) are unchanged.

## Interpretation and limitations

Small warm loopback reads with the driver and API sharing a host cannot establish
production capacity, WAN/TLS performance, tail behavior at large history sizes,
background-sweep cost, mixed authoritative writes, league queue scalability or
concurrent analysis admission. Do not extrapolate player capacity from requests
per second. Query plans must motivate any proposed indexes; this branch changes
no production query, index, engine, permission or schema. There are only 24
samples per endpoint/concurrency, so tail percentiles are coarse. Completed
games are short repetition draws, single-player statistics are an unfinished-game
workload, and cleanup after `SIGKILL` or abnormal host termination cannot be
guaranteed. Full CI runs in GitHub Actions; local runs used the shared resource
lock, serial execution, two-thread numeric limits and hard timeouts.
