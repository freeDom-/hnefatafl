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

The documented `--kill-after=15s` is the external grace after a SIGTERM. The
internal shutdown budget (initial outstanding-request wait, server termination
and forced-kill allowances, worker drain, and final cleanup) is designed to fit
inside it, but that is a design bound rather than a guarantee: a worker blocked
in an uninterruptible operation can outlast every internal deadline and is
ultimately stopped by the external hard timeout. Setup, verification and
evidence writing are synchronous and also count against the overall command
timeout.

## Environment and storage isolation

Both the driver and the internal worker independently establish a deterministic
benchmark environment before importing any application module, and both verify
and record the imported backend location. Every `HNEFATAFL_*`, proxy and
numeric-thread variable is snapshotted, removed, replaced with benchmark-local
values and restored afterwards, even when the body raises. This covers the data
directory, development environment, loopback public URL and allowed hosts,
trusted proxies, session lifetime, account request budgets, online feature
settings, registration/SMTP (disabled, so no real mail or external network), and
two-thread numeric limits. Operator settings such as a production
`HNEFATAFL_PUBLIC_URL` cannot redirect or invalidate the local workload.

The fixture directory is created with mode `0700`, must be an absolute,
non-symlink, user-owned directory whose name starts with `hnefatafl-load-`, and
must contain a regular `.benchmark-only` marker with a 32-character hex
identity. Before any application import, the whole fixture tree is checked:
symlinks are rejected anywhere, every entry must resolve inside the fixture, and
the application-owned paths (`hnefatafl.sqlite3`, `jobs`, `models`,
`tournaments`, `.deployment-gate`, the marker and their journal/WAL siblings)
must have the expected file or directory type. A valid-looking directory whose
database or subdirectory links to external storage is therefore rejected before
the backend can open it. The worker also requires an explicit
`HNEFATAFL_DATA_DIR` that matches `--data-dir`, so the internal server mode
cannot initialize arbitrary operator storage. Only the current process's owned
fixture directory is removed; a changed marker or identity makes cleanup refuse.
Application-owned files must also have a single link (no hard link sharing an
inode with external storage) and every entry must stay on the fixture's own
filesystem. Mount points at or beneath the fixture root are detected from
`/proc/self/mountinfo` (decoding the kernel's octal path escapes for space, tab,
newline and backslash in a single pass), so a same-filesystem bind mount of
operator storage is rejected before any application import, not only a
different-device mount. The same check runs again before recursive cleanup: if a
nested mount is present, or if mount information cannot be read or resolved
reliably, the fixture is retained and a cleanup failure is reported rather than
deleting the mounted contents. Nothing is ever unmounted automatically, and
ordinary symbolic
links inside the fixture are still removed without following their targets. This
is a proportionate guard for an optional local Linux benchmark, not a defence
against arbitrary hostile concurrent same-user filesystem manipulation. The
benchmark never opens or modifies existing operator databases, models,
checkpoints or training jobs.

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

A cooperative cancellation event is checked before every fixture mutation,
verification request, warm-up request and measured request, and after each
synchronization barrier. Measurement runs in synchronized waves of at most the
configured concurrency; the wait tracks only still-pending futures, so a
completed actor never makes the driver spin until the slowest one finishes. On
cancellation the current barrier is aborted, queued futures are cancelled, the
server is stopped if that is needed to unblock an in-flight request, and the
driver waits a bounded time for the workers. Returned samples are a consistent
snapshot taken after the workers settle, and a phase that could not stop its
workers is marked incomplete. Worker exceptions are inspected only on futures
that already completed, so a pending worker is never awaited without a bound.
When a worker remains pending after the bounded waits, remaining phases and the
post-measurement verification are skipped, clients are left open, and the run
cannot report success. Python's ThreadPoolExecutor may still join a stuck
non-daemon worker at interpreter shutdown; that case is left to the external
hard timeout after evidence has been persisted.

HTTP clients are registered for cleanup immediately after creation, before
login. Clients are only closed when every measurement phase confirmed its
workers stopped, so a client is never closed underneath a running worker. In a
`finally` path the driver stops and reaps the API child (escalating from
`SIGTERM` to `SIGKILL` after a bounded grace), then closes clients and the log
handle, and removes the fixture only after the child is confirmed stopped. A
child that cannot be reaped keeps its fixture so a database still in use is not
deleted; the retained path is recorded. Partial evidence is still written, and
an incomplete run is never reported as a successful baseline. Cleanup after an
uncatchable `SIGKILL` or abnormal host termination cannot be guaranteed.

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
tournaments are seeded: a started bracket and an empty upcoming registration.

For the bounded fixture, verification uses exact identifier sets rather than
subset checks, so a missing record, an extra foreign record or an unexpectedly
empty listing fails the benchmark. It checks that logins correspond to the
expected seeded accounts, that player accounts are distinct and
non-administrator, that local and single-player history contain exactly the
expected owned records, that the in-progress listings contain exactly the
expected unfinished games, that the completed local game is the seeded
repetition draw with the expected validated replay, that current online games
contain exactly the expected active, waiting and tournament games and exclude
completed games, that online history and rating history contain exactly the two
completed draws per player, that replays carry the expected authoritative result,
moves, states and participants, that statistics and leaderboard entries reflect
the completed results, that tournament discovery, entrants, matches and legs
match expectations derived independently from the entrant count, that tournament
responses agree across accounts, and that the timed endpoint paths and
parameters return semantically appropriate results. The timed leaderboard page
must contain exactly `min(users, 10)` unique rated accounts with the expected
total and rating metadata; the timed lobby page must contain exactly
`min(expected active/waiting games, 25)` unique non-terminal games in production
order, with the full set checked when it fits; and the timed single-player
statistics projection must show the expected unfinished-game totals, ruleset
groups and no completed outcomes. Authoritative table counts must match
expectations for the configured number of users.

The essential semantics are verified again after measurement and before the run
is declared successful. Single-player statistics remain an unfinished-game
workload because no AI search is run; the report says so explicitly and does not
imply completed single-player outcome coverage.

## Concurrency and success reporting

The report distinguishes configured client concurrency, the maximum observed
outstanding requests from distinct authenticated accounts, the number of
successfully authenticated accounts, and total/success/failed request counts.
Actor indexes are only equated with identities because each login was verified
against the seeded account. Synchronized waves make the configured concurrency
observable, but client-side overlap is not presented as proof of simultaneous
backend handler execution. Every actor, endpoint and concurrency level is
validated against its expected request count, every requested level must appear,
and worker failures are recorded explicitly. Underachieved concurrency, missing
requests or any non-200 response marks the run incomplete.

Measurement completion and overall success are separate. `measurement_complete`
requires every requested level to be present and complete; `complete` also
requires cleanup to succeed and the evidence to be written. A cleanup failure,
an un-reaped subprocess or a retained fixture therefore cannot produce a
successful exit status.

## Partial evidence and telemetry

Collected samples are appended to the evidence before optional process telemetry
is read. A server that exits during measurement, or a missing `/proc` entry for
CPU or RSS, leaves the completed samples intact and records the telemetry as
`null`; `api_process_telemetry_available` distinguishes a missing reading from a
measured zero. Raw samples are written before the summary, so a summary-writing
failure still preserves the measurements. Diagnostic messages record an
exception type and a short message, and redact anything that may carry a
credential, token or response payload.

## Environment metadata

The report records the CPU model, logical and affinity CPU counts, the effective
cgroup CPU quota and memory limit resolved from the process cgroup slice
(honouring the mount root and point from `/proc/self/mountinfo`, so delegated
subtree mounts and nested systemd slices resolve correctly, through
`/proc/self/cgroup`), system memory capacity, Python and SQLite versions,
FastAPI/Uvicorn/HTTPX/Pydantic
versions, the caller's numeric-thread settings, the benchmark-configured
numeric-thread settings (read after entering the deterministic environment), the
imported backend location, the lock fingerprint, benchmark parameters and fixture
configuration. System memory capacity is reported separately from an enforced
cgroup memory limit. The cgroup v1 fallback reads the fixed controller root and
does not resolve nested slices, so it may over-report the available quota; it
returns `null` when the controller files are absent. Unavailable Linux resource
values are `null`, distinct from a measured zero. No credentials, cookies,
tokens, private configuration, full environment dump or response body is
recorded.

## Corrected measured baseline

Revision `c5e706683439ec7fa9e9ad7b75a4ffae322e96e1` on
`perf/backend-read-baseline`, observed `2026-11-19T11:48:35Z`, passed all 936
measured requests (HTTP 200); the observed simultaneous account counts matched
1/4/8, every expected request count was validated, and every phase reported its
workers stopped. Fixture: nine accounts, 40 local/single-player games, 32 online
games, 64 accepted moves, eight results and rating events, two tournaments,
eight entrants, fourteen matches and eight legs. A thin fixture-identity HTTP
middleware is included in these observations.

See the [corrected summary](../benchmarks/backend-read-baseline-corrected.json),
[all raw samples](../benchmarks/backend-read-requests-corrected.json) and
[non-default parameter checks](../benchmarks/backend-read-parameter-checks-corrected.json).

| Distinct-account concurrency | Requests | Elapsed seconds | Median ms | p95 ms | p99 ms | API CPU seconds | API RSS KiB after phase |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 312 | 1.884 | 5.79 | 9.74 | 10.76 | 1.68 | 67,412 |
| 4 | 312 | 1.703 | 21.25 | 31.69 | 41.38 | 3.24 | 71,156 |
| 8 | 312 | 1.748 | 45.64 | 59.36 | 80.25 | 3.81 | 75,400 |

The summary was independently recalculated from its raw samples: aggregate and
per-endpoint request counts, nearest-rank p95/p99, medians, response-byte totals
and status counts all match. The two-account (26 requests per level), four-account
(104 requests per level at concurrency 1/2/4) and three-account non-divisible wave
(78 requests per level at concurrency 1/2) checks also completed with every
request accounted for.

## Historical evidence

All three recorded baselines are retained unchanged. The corrected baseline
above was produced before the final failure-path and verification corrections,
which do not change successful-path measurement semantics; the two earlier
baselines used earlier scheduling and verification and must not be read as
results from the corrected implementation.

### Hardened baseline (before the corrected scheduling and semantics)

Revision `4504605dbfd7d02072dd17a4ee9497af781f706c`, observed
`2026-11-19T09:39:36Z`: 936 measured requests (HTTP 200), observed overlap 1/4/8.
This run used the earlier measurement scheduling and subset-based fixture
verification.

| Distinct-account concurrency | Requests | Elapsed seconds | Median ms | p95 ms | p99 ms | API CPU seconds | API RSS KiB after phase |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 312 | 2.027 | 6.25 | 10.16 | 11.58 | 1.81 | 66,128 |
| 4 | 312 | 1.768 | 21.86 | 32.64 | 41.13 | 3.19 | 69,872 |
| 8 | 312 | 1.750 | 45.36 | 60.01 | 65.34 | 3.75 | 73,704 |

Its [summary](../benchmarks/backend-read-baseline-hardened.json),
[all samples](../benchmarks/backend-read-requests-hardened.json) and
[parameter checks](../benchmarks/backend-read-parameter-checks.json) are
unchanged.

### Pre-hardening baseline

The original PR #97 baseline was collected before the isolation, lifecycle,
provenance and verification changes. It used revision
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
