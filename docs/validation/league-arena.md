# League Arena validation

[Documentation index](../README.md) · [League policy](../LEAGUES.md)

## Browser and restore coverage

The Playwright League suite runs on desktop Chromium and the 390-pixel mobile
Chromium profile. Each run starts a real application lifespan with disposable
storage. The fixture controls the server's UTC clock and seeds an initial Elo
gap. Queueing, moves, concession, archived revisions and administrative actions
use the authenticated API and the existing online game interface. Leaving both
League pages before the windows widen tests matchmaking independently of the
page. Leaving the League pages during settlement tests recovery from browser
absence. The suite also exercises direct navigation, compact navigation, themes,
English/German, keyboard focus, reload, reconnect, expiry, delayed cancellation,
stale protected requests and permission-gated audit access. The compact
navigation checks cover 390- and 441-pixel phones at Large scale and verify the
space reserved by the fixed bar during normal play and text resizing.

The restore test copies the live League authority into a temporary candidate
taken before the games existed. It verifies that the paused timeout gate survives
restore, a committed result and a later void remain pending, and settlement
consumes both exactly once with the expected scoring and seat release.

```sh
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  .venv/bin/python -m pytest -q tests/test_league_restore.py
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
  npm --prefix frontend run test:online -- leagues.spec.ts
```

## Measurement method

The backend runner creates a new temporary SQLite database for each workload.
It isolates League timing from HTTP transport by calling the authenticated
services directly. The browser runner measures a compiled production bundle
over loopback with the real API, one logged-in account, and Chromium DevTools
Protocol request accounting. `Network.loadingFinished` reports transferred
response bytes. Raw and gzip asset sizes use the emitted build files. The
measurement record pairs data with the relevant Python source hashes or the
production build fingerprint and records the working tree state.

The backend runner uses 30 samples per repeated operation and six writer threads
for the mixed arm. SQLite trace counts include transactions and trigger callbacks.
Writer wait covers `BEGIN IMMEDIATE`. Writer hold covers acquisition through
commit and connection close. Latency percentiles use nearest rank. The synthetic
workloads exercise bounded application paths on this host. Concurrent tails
vary with local scheduling. The common writer arm compares the online move and
tournament settlement paths available on current main.

The runs used a Linux x86-64 cloud host with three visible AMD EPYC 9V74
vCPUs, Python 3.12.14, SQLite 3.53.1, Node.js 24.19.0 and Chromium 156.0.8078.4.
The measured League runtime and build used the clean commit
`9f4aafe5b7ddc28366415f4ec9668091f4a8318a`. The main comparison used an archive
at `f2590b993cab24d7d3ab89249174ac052af5d2e6`. The raw files include query plans,
per-operation errors and the production bundle fingerprint.
[Backend measurements](measurements/league-arena-backend.json) ·
[Browser measurements](measurements/league-arena-browser.json)

```sh
work=$(mktemp -d)
mkdir "$work/main"
git archive origin/main | tar -x -C "$work/main"
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 PYTHONPATH="$work/main" \
  .venv/bin/python scripts/benchmark_league_arena.py --baseline \
  --source-revision "$(git rev-parse origin/main)" --output "$work/main.json"
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  .venv/bin/python scripts/benchmark_league_arena.py --output "$work/league.json"
npm --prefix frontend run build
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
  node frontend/benchmarks/league-network.mjs "$work/browser.json"
```

Run these commands sequentially under the shared test lock in a shared
workspace. The browser runner checks that the bundle matches the checkout and
supports the explicit `--allow-dirty` switch for a recorded dirty measurement.
Use the bundled Playwright Chromium by omitting the executable override.

## Backend workloads

The 8- and 4,096-entry queues contain mutually compatible accounts in arrival
order. The separate 130-entry queue contains 128 earlier accounts with disjoint
synthetic ratings and one compatible pair at the tail. The SQL service drains
without a sleep in these trials. In the application, the worker waits between
cycles, so elapsed service time and end-to-end queue waiting time differ.

The standings fixture has 4,096 qualifying players and shared sporting ties.
The runner compares initial and deep seek pages of 25 rows. The personal-history
fixture has 1,260 real authoritative result events, with opponents on both sides
of the stored unordered account pair. Captured SQLite plans cover the actual
League queries issued by these pages.

The settlement arm measures the next 30 results after 1,200 results already
applied for one player. A copy with deliberately stale projection metadata
forces the exact replay path on the same events. Timing excludes the deliberate
metadata mutations and the runner checks identical standings. The correction
trial separates the existing Elo authority's whole-pool recalculation from the
League worker's withdrawal accounting. The administrative trial deletes one
projection and measures reconstruction from authoritative games and effective
rating events, in batches of 50. It checks that public projections remain
unavailable during recovery and that the repair reproduces the prior standing.

| Operation | Samples | p50 ms | p95 ms | p99 ms | Median SQL statements |
| --- | ---: | ---: | ---: | ---: | ---: |
| Match cycle, 8 compatible accounts | 4 | 3.95 | 4.36 | 4.36 | 41 |
| Match cycle, 4,096 compatible accounts | 30 | 4.10 | 4.85 | 5.50 | 41 |
| Match cycle, 130 accounts with a compatible pair at the tail | 17 | 3.97 | 5.56 | 5.56 | 45 |
| Standings, first page | 30 | 1.45 | 1.66 | 1.75 | 8 |
| Standings, after 3,275 rows | 30 | 1.46 | 1.68 | 1.70 | 8 |
| Personal history, first page | 30 | 1.78 | 2.08 | 2.10 | 8 |
| Personal history, after 950 rows | 30 | 1.79 | 2.27 | 2.47 | 8 |
| Settlement append | 30 | 3.30 | 3.90 | 4.54 | 23 |
| Same settlement with exact ledger replay | 30 | 4.96 | 5.52 | 5.91 | 24 |
| Official void and Elo recalculation | 10 | 68.69 | 73.83 | 73.83 | 15,106 |
| League withdrawal projection | 10 | 4.72 | 5.30 | 5.30 | 22 |
| Administrative rebuild batch | 155 writes plus one final read | 2.33 | 4.87 | 6.65 | 8 |

The small and large compatible queues committed 4 and 30 pairs, at
241.43 and 239.38 pairs per second of measured service time. The deep-tail
pair committed on cycle 17 after 92 ms of total service and status checks.
Its discovery writer held SQLite for 3.02 ms at p99. The standings plan
seeks the qualified ordering index through the complete cursor key. Personal
history seeks both participant indexes and sorts their two bounded result sets
before the final page limit.

Append settlement processed 293.83 results per second, with a median writer
hold of 1.30 ms. Forced replay processed 200.10 results per second and held the
writer for 2.94 ms at the median. A single subsequent batch settled 30 results
in 44.17 ms. The 1,260-game rebuild used 155 short writes before the final
empty check, with 0.436 seconds of work excluding the worker's scheduled waits.
The live worker executes one rebuild batch per cycle, so this fixture takes
about 155 seconds at the normal one-second cadence. The empty worker spent
93.31 ms of process CPU over 8.00 seconds, ran 42 SQL statements and acquired
zero writer transactions. Process RSS changed by +404 KiB in that sample.

### Shared SQLite writers

Each common run interleaves 30 accepted online moves and 30 pending tournament
settlements. The mixed League run adds 30 explicit joins followed by cancellation
and 30 League settlements. The six-thread runner serializes each settlement
service as it runs in the application. Join/cancel measures both mutations as one
operation. The reported SQL writer waits exclude the benchmark dispatch time.

| Source and operation | p50 ms | p95 ms | p99 ms | Median SQL statements | Writer wait p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Main online move | 2.84 | 11.61 | 21.73 | 20 | 20.19 |
| Main tournament settlement | 20.89 | 27.75 | 27.82 | 25 | 10.46 |
| League branch online move, common load | 2.98 | 58.83 | 82.43 | 20 | 78.71 |
| League branch tournament settlement, common load | 23.05 | 33.61 | 43.38 | 25 | 19.66 |
| League branch online move, mixed load | 2.25 | 35.42 | 83.17 | 20 | 79.40 |
| League branch tournament settlement, mixed load | 29.16 | 57.69 | 61.10 | 25 | 33.54 |
| League branch join and cancel, mixed load | 4.21 | 29.73 | 37.22 | 42 | 33.48 |
| League branch settlement, mixed load | 37.08 | 87.62 | 94.01 | 23 | 78.62 |

The common load completed 421.71 operations per second on main and 372.65 on
the League branch. The mixed load completed 277.66 operations per second, or
347.08 individual moves, mutations and settlements per second. All workloads
recorded zero errors. These are single local trials on disposable storage.

## Browser requests and assets

Opening League from the authenticated dashboard transferred 3,507 response
bytes over four League API reads. During the next 17 seconds idle it issued
zero League requests. An explicit join issued one POST, followed by four queue
status reads in 17 seconds, transferring 3,748 response bytes. Those four reads
measured 9.70 ms p50 and 13.03 ms p95/p99 on loopback. During the 4.5 seconds
after navigation away, it issued zero League requests. The browser recorded
zero HTTP or network errors.

The build stayed below every initial bundle limit. The browser made both League
asset requests after the navigation click. Those responses transferred 30,256
bytes combined over loopback, including response overhead. Emitted gzip sizes
use level 9, as in the bundle-budget check.

| Asset group | Raw bytes | Gzip bytes |
| --- | ---: | ---: |
| Main initial JavaScript | 573,936 | 183,181 |
| League branch initial JavaScript | 583,222 | 186,009 |
| Main initial CSS | 115,904 | 24,194 |
| League branch initial CSS | 115,551 | 24,114 |
| Deferred League JavaScript | 27,432 | 8,565 |
| Deferred League CSS | 1,782 | 629 |
