# Disposable backend read workload

[Documentation index](../README.md) · [Architecture](../../ARCHITECTURE.md)

## Scope and safety

This optional Linux benchmark seeds a fresh temporary SQLite database with real
HTTP-authorized games, eight verified player accounts and an administrator. It
never accepts an operator database, checkpoint or model path. Eight genuine
completed repetition draws have nine replay states each; current games,
waiting lobbies, single-player/local records and a human tournament bracket
exercise populated reads. No AI search or training is requested.

A clean Git root and the imported server path are verified before seeding.
The revision is checked again after measurement. Evidence includes script SHA-256,
fixture counts, representative query plans/indexes, per-endpoint samples and API
process CPU/RSS. Query-plan fragments are explicitly separate from actual HTTP
service queries. Output must be a new directory; existing evidence is preserved.
The server is loopback-only, one process, with an unpredictable fixture identity
checked before login. Account/online background sweeps are disabled. Generated
credentials, cookies, CSRF tokens and response bodies are never reported. Clients
and the API subprocess close on completion or graceful cancellation; only owned
temporary fixture data is removed.

## Reproduction

From a clean committed checkout, using installed locked Python dependencies:

```sh
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  flock -n /run/lock/hnefatafl-tests.lock \
  timeout --kill-after=10s 180s nice -n 10 \
  .venv/bin/python scripts/benchmark_backend_reads.py \
  --output /tmp/hnefatafl-read-evidence-new
```

Coordinate the shared lock across worktrees. Do not run alongside builds, browser
suites, training or other benchmarks. The default is three rounds of thirteen
bounded GET endpoints for each of eight distinct accounts, serially at concurrency
1, 4 and 8 (936 measured requests). Each account has at most one in-flight
request; report observed distinct-account overlap separately. Bounds cap users
at 16, rounds at 10 and distinct concurrency levels at five. Fixture writes and
warm-up reads are excluded from measured phases. Seed writes are correctness
setup, not a concurrent-write benchmark.

## Interpretation and measured evidence

Small warm loopback reads with the driver and API sharing a host cannot establish
production capacity, WAN/TLS performance, tail behavior at large history sizes,
background-sweep cost, mixed authoritative writes, league queue scalability or
concurrent analysis admission. Do not extrapolate player capacity from requests
per second. Query plans must motivate any proposed indexes; this branch changes
no production query, index, engine, permission or schema.

Clean revision `b755fdb4251804817b310e2885ff029399e64e2b` passed all 936
measured requests (HTTP 200); actual simultaneous account counts matched 1/4/8.
See the [summary](../benchmarks/backend-read-baseline.json) and
[all samples](../benchmarks/backend-read-requests.json). Fixture: nine accounts,
40 local/single-player games, 32 online games, 64 accepted moves, eight results
and rating events, two tournaments, eight entrants, fourteen matches and eight
legs. A thin fixture-identity HTTP middleware is included in these observations.

| Distinct-account concurrency | Requests | Elapsed seconds | Median ms | p95 ms | p99 ms | API CPU seconds | API RSS KiB after phase |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 312 | 1.928 | 5.84 | 9.74 | 10.81 | 1.71 | 62,012 |
| 4 | 312 | 1.780 | 22.59 | 33.17 | 39.18 | 3.37 | 65,744 |
| 8 | 312 | 1.680 | 43.96 | 57.61 | 91.66 | 3.74 | 70,164 |

This is one short run, not a capacity target or improvement claim. There are only
24 samples per endpoint/concurrency, so tail percentiles are coarse. Lobby p95
reached 118.83 ms at concurrency eight. Owner/category, replay and tournament
fragments used existing indexes. The representative status/updated query scanned
`online_updated`; a 32-row fixture cannot justify a production index change or
establish large-table cost. No recurring scan was added.

Sixteen focused guard tests passed (one existing FastAPI/TestClient deprecation
warning); Ruff formatting/lint and docs passed. The earlier startup attempt failed
because an unclassified identity route was correctly denied by authorization.
Import-only diagnosis completed in 0.70 seconds; readiness now uses the existing
public health endpoint with a fixture-only response header. A two-account check
then passed 52/52 reads. Before the recorded full run, import-time default app
storage was explicitly isolated and environment restoration tested. Earlier
prototype imports could initialize the default app, although all seeded fixture
records used explicit temporary storage; no data deletion was performed.

Full checks run in GitHub Actions. Local checks used the shared resource lock,
serial execution, two-thread numeric limits and hard timeouts. API children and
HTTP clients closed; temporary fixture databases were removed. Useful failed-run
logs were retained. No benchmark server or browser remains running. Full branch
CI is pending.
