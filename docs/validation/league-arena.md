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

The runs used a Linux x86-64 cloud host with three visible AMD EPYC 9V74 vCPUs,
Python 3.12.14, SQLite 3.53.1, Node.js 24.19.0 and Chromium 154. The measured
backend runtime was commit `371bc31854d39900a841cfc2ce7e09bc569a4d19`, with
validation and documentation changes pending. The backend source hashes match
its Git blobs. The browser run used the clean compact-navigation fix at
`e6f7af5c20c53d7ba401cc3e192d301f29f1400b`. The main comparison used an archive
at `27a3193bb1abd1fd98d26428d48b14fe14cb5bdf`. The raw files include query plans,
per-operation errors and the production bundle fingerprint.
[Backend measurements](measurements/league-arena-backend.json) ·
[Browser measurements](measurements/league-arena-browser.json)

The baseline and the subsequently integrated main at `a4edb70` share the same
production Python and frontend sources and dependency locks.

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
| Match cycle, 8 compatible accounts | 4 | 3.77 | 4.37 | 4.37 | 41 |
| Match cycle, 4,096 compatible accounts | 30 | 4.41 | 5.37 | 7.75 | 41 |
| Match cycle, 130 accounts with a compatible pair at the tail | 17 | 4.06 | 4.70 | 4.70 | 45 |
| Standings, first page | 30 | 1.57 | 2.50 | 6.05 | 8 |
| Standings, after 3,275 rows | 30 | 1.61 | 2.49 | 2.62 | 8 |
| Personal history, first page | 30 | 1.86 | 2.79 | 3.31 | 8 |
| Personal history, after 950 rows | 30 | 1.90 | 2.62 | 5.49 | 8 |
| Settlement append | 30 | 3.84 | 4.63 | 5.08 | 23 |
| Same settlement with exact ledger replay | 30 | 5.66 | 7.32 | 11.87 | 24 |
| Official void and Elo recalculation | 10 | 83.90 | 111.02 | 111.02 | 15,106 |
| League withdrawal projection | 10 | 4.82 | 7.00 | 7.00 | 22 |
| Administrative rebuild batch | 155 writes plus one final read | 2.64 | 4.74 | 5.14 | 8 |

The small and large compatible queues committed 4 and 30 pairs, at 248.53 and
220.54 pairs per second of measured service time. The deep-tail pair committed
on cycle 17 after 87 ms of total service and status checks. Its discovery
writer held SQLite for 2.29 ms at p99. The standings plan seeks the qualified
ordering index through the complete cursor key. Personal history seeks both
participant indexes and sorts their two bounded result sets before the final
page limit.

Append settlement processed 251.82 results per second, with a median writer
hold of 1.59 ms. Forced replay processed 167.22 results per second and held the
writer for 3.21 ms at the median. A single subsequent batch settled 30 results
in 50.69 ms. The 1,260-game rebuild used 155 short writes before the final
empty check, with 0.443 seconds of work excluding the worker's scheduled waits.
The live worker executes one rebuild batch per cycle, so this fixture takes
about 155 seconds at the normal one-second cadence. The empty worker spent
41.89 ms of process CPU over 8.00 seconds, ran 42 SQL statements and acquired
zero writer transactions. Process RSS rose by 228 KiB in that sample.

### Shared SQLite writers

Each common run interleaves 30 accepted online moves and 30 pending tournament
settlements. The mixed League run adds 30 explicit joins followed by cancellation
and 30 League settlements. The six-thread runner serializes each settlement
service as it runs in the application. Join/cancel measures both mutations as one
operation. The reported SQL writer waits exclude the benchmark dispatch time.

| Source and operation | p50 ms | p95 ms | p99 ms | Median SQL statements | Writer wait p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Main online move | 3.25 | 83.85 | 133.51 | 20 | 130.17 |
| Main tournament settlement | 10.12 | 21.67 | 21.89 | 25 | 8.27 |
| League branch online move, common load | 3.00 | 21.25 | 107.60 | 20 | 104.24 |
| League branch tournament settlement, common load | 24.11 | 46.09 | 48.84 | 25 | 33.91 |
| League branch online move, mixed load | 2.95 | 56.12 | 57.53 | 20 | 53.92 |
| League branch tournament settlement, mixed load | 22.32 | 84.89 | 85.39 | 25 | 78.78 |
| League branch join and cancel, mixed load | 4.42 | 39.09 | 56.97 | 42 | 53.68 |
| League branch settlement, mixed load | 41.58 | 78.27 | 85.42 | 23 | 37.95 |

The common load completed 344.55 operations per second on main and 325.58 on
the League branch. The mixed load completed 276.13 operations per second, or
345.16 individual moves, mutations and settlements per second. All workloads
recorded zero errors. These are single local trials on disposable storage.

## Browser requests and assets

Opening League from the authenticated dashboard transferred 3,507 response
bytes over four League API reads. During the next 17 seconds idle it issued
zero League requests. An explicit join issued one POST, followed by four queue
status reads in 17 seconds, transferring 3,748 response bytes. Those four reads
measured 13.03 ms p50 and 14.28 ms p95/p99 on loopback. During the 4.5 seconds
after navigation away, it issued zero League requests. The browser recorded
zero HTTP or network errors.

The build stayed below every initial bundle limit. The browser made both League
asset requests after the navigation click. Those responses transferred 30,256
bytes combined over loopback, including response overhead. Emitted gzip sizes
use level 9, as in the bundle-budget check.

| Asset group | Raw bytes | Gzip bytes |
| --- | ---: | ---: |
| Main initial JavaScript | 573,702 | 183,136 |
| League branch initial JavaScript | 582,988 | 185,953 |
| Main initial CSS | 115,904 | 24,194 |
| League branch initial CSS | 115,832 | 24,170 |
| Deferred League JavaScript | 27,432 | 8,567 |
| Deferred League CSS | 1,782 | 629 |
