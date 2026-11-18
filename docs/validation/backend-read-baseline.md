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

## Interpretation and pending evidence

Small warm loopback reads with the driver and API sharing a host cannot establish
production capacity, WAN/TLS performance, tail behavior at large history sizes,
background-sweep cost, mixed authoritative writes, league queue scalability or
concurrent analysis admission. Do not extrapolate player capacity from requests
per second. Query plans must motivate any proposed indexes; this branch changes
no production query, index, engine, permission or schema.

Fourteen focused guard tests passed before current-main integration. Current-main
fixture verification and reproducible measurements are pending. Full checks run
in GitHub Actions; local validation stays focused and uses the shared lock.
