# League Arena validation

[Documentation index](../README.md) · [League policy](../game/leagues.md)

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
[Backend measurements](./browser-analysis/evidence/measurements/league-arena-backend.json) ·
[Browser measurements](./browser-analysis/evidence/measurements/league-arena-browser.json)

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

## Follow-up to the review at `e66c595`

The preceding measurements describe the original League implementation. The
follow-up compares the same main and reviewed commits with the revised runtime
and records the service and worker workloads independently.

### Reproduction

Run serially with the repository test lock and math thread limits. The shared
runner makes a SQLite backup of the main fixture once, including the same
accounts, ordinary games and pending tournament settlements. Each trial copies
that backup into fresh temporary storage. A League source tree upgrades the copy
to schema 23 through normal startup.

```sh
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
work=$(mktemp -d)
mkdir "$work/main" "$work/reviewed" "$work/revised"
git archive f2590b993cab24d7d3ab89249174ac052af5d2e6 | tar -x -C "$work/main"
git archive e66c5950a76edc10f3c9d55474f4e3648b14fc7e | tar -x -C "$work/reviewed"
git archive fbb987fe97a60165c3bd0d4d8c7a2d69f1707340 | tar -x -C "$work/revised"
PYTHONPATH="$work/main" .venv/bin/python scripts/benchmark_league_workers.py prepare \
  --baseline --template "$work/common"
PYTHONPATH="$work/main" .venv/bin/python scripts/benchmark_league_workers.py shared \
  --baseline --source-revision f2590b993cab24d7d3ab89249174ac052af5d2e6 \
  --template "$work/common" --output "$work/main-1.json"
PYTHONPATH="$work/revised" .venv/bin/python scripts/benchmark_league_workers.py shared \
  --source-revision fbb987fe97a60165c3bd0d4d8c7a2d69f1707340 \
  --template "$work/common" --output "$work/revised-1.json"
PYTHONPATH="$work/reviewed" .venv/bin/python scripts/benchmark_league_workers.py shared \
  --source-revision e66c5950a76edc10f3c9d55474f4e3648b14fc7e \
  --template "$work/common" --output "$work/reviewed-1.json"
```

Reverse the three-source order on even repetitions. Each shared trial measures
100 accepted moves, 100 rated concessions and 100 tournament settlements in
isolation. The common service arm interleaves moves and settlements with six
threads. The live common arm schedules move bursts around the real tournament
worker's one-second tick. Its League variant also starts the real League worker
with 240 compatible waiters and 40 unsettled official results. The League
service arm adds explicit joins, cancellations and League settlements.

Use the same `PYTHONPATH` source selection and `--output` convention with
`scripts/benchmark_league_workers.py matchmaking` for the six live queue
scenarios and `scripts/benchmark_league_rank.py` for authenticated rank reads.
The queue scenarios use real elapsed time, with a 10-second observation window
from the start of actionable work. The simultaneous expansion uses synthetic
Elo groups spaced far enough apart to isolate the intended pairs. The expiry
case leaves three seconds of the original lifetime when sampling starts.
Process CPU includes measurement overhead and any foreground moves. Queue
waiting time uses the authoritative pairing timestamp. A separate observer
checks post-commit visibility every 50 ms.

```sh
.venv/bin/python scripts/benchmark_league_rank.py --output "$work/rank.json"
.venv/bin/python scripts/benchmark_league_arena.py --output "$work/service.json"
npm --prefix frontend run build
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
  node frontend/benchmarks/league-network.mjs "$work/network.json"
```

### Writer and query behavior

The query plan for the ordinary terminal-result provenance check seeks
`sqlite_autoindex_league_games_1` by game ID. The reviewed outbox statement
performed the same seek through `INSERT … SELECT` and then tried the indexed
active-seat delete. The revised transaction executes the outbox upsert and
`league_active_game` delete only when it finds League provenance. Nonterminal
moves execute zero League result statements. They now skip the return-leg
lookup and reuse the validated move transition when constructing the response.
The official result, League outbox and seat release retain their atomic write.

The pool for guarded online and tournament writers retains at most four idle
SQLite connections, with an independent commit or rollback for each lease.
It reduces recurring SQLite schema and statement preparation. Session,
authority, expiry and optimistic versions still load after writer acquisition.
The deployment gate applies to every lease, and each lease sets its intended
lock timeout before beginning the transaction.

### Repeated shared-writer results

Five repetitions per source used the same two-CPU-quota Linux host, SQLite
build, temporary storage and common fixture. Table cells are the median of
the per-run statistic with its observed minimum and maximum in brackets.
Timings are in milliseconds unless the row names another unit.

| Workload statistic | Main | Reviewed League | Revised League |
| --- | ---: | ---: | ---: |
| Common service operations/s | 379.74 [349.57–406.12] | 341.95 [313.97–369.97] | 621.06 [523.42–642.65] |
| Common move p95 | 21.74 [8.89–27.61] | 22.66 [13.12–23.22] | 4.11 [2.20–22.88] |
| Common move p99 | 81.70 [36.63–85.23] | 52.86 [37.32–107.59] | 36.15 [22.22–58.83] |
| Common move writer wait p99 | 79.06 [34.45–80.60] | 34.52 [19.92–104.28] | 33.54 [18.68–53.86] |
| Common move writer hold p50 | 2.31 [2.28–2.83] | 2.76 [2.52–2.78] | 1.13 [1.11–1.36] |
| Common move SQL in writer p50 | 1.86 [1.85–2.30] | 2.26 [2.07–2.28] | 0.86 [0.84–0.99] |
| Common tournament service p95 | 35.20 [28.75–40.83] | 34.35 [28.27–36.61] | 35.16 [24.82–44.70] |
| Common tournament before writer p95 | 33.05 [26.38–38.08] | 30.49 [25.30–33.03] | 19.57 [17.92–22.45] |
| Isolated nonterminal p95 | 1.84 [1.56–3.37] | 2.03 [1.75–2.41] | 0.69 [0.66–0.90] |
| Isolated rated result p95 | 2.52 [2.40–2.75] | 2.87 [2.58–2.99] | 1.51 [1.41–1.71] |
| Isolated tourney p95 | 2.80 [2.47–3.15] | 2.95 [2.88–3.30] | 2.01 [1.94–2.08] |
| Live common move p95 | 84.11 [80.27–88.80] | 83.02 [82.13–84.87] | 56.82 [56.25–58.70] |
| Live common move p99 | 132.25 [113.55–133.47] | 108.46 [87.19–110.18] | 83.59 [82.31–107.80] |
| Live common tournament writer p95 | 2.38 [1.93–3.90] | 2.89 [2.53–7.08] | 1.26 [1.06–2.76] |
| Live mixed move p95 | — | 106.41 [58.45–111.92] | 56.21 [36.91–57.29] |
| Live mixed move p99 | — | 182.54 [83.75–185.77] | 84.74 [81.32–109.01] |
| Live mixed tournament writer p95 | — | 2.75 [2.24–3.95] | 1.45 [1.08–2.35] |
| Live mixed League pairs | — | 5.00 [5.00–5.00] | 52.00 [47.00–57.00] |
| Live mixed League settlements | — | 40.00 [40.00–40.00] | 40.00 [40.00–40.00] |
| Mixed service operations/s | — | 276.56 [262.59–280.86] | 466.52 [459.05–492.63] |
| Mixed service tournament p95 | — | 65.54 [55.83–79.11] | 38.05 [29.16–54.43] |

The repeatable change on the reviewed branch was lower common service
throughput and a longer median move writer hold. The review's 58.83 ms move
p95 exceeded all five reviewed p95 trials here. SQLite lock acquisition
accounted for much of the variable tail. The direct tournament samples also
include dispatch and serialization before the writer. The live tournament
samples measure `BEGIN IMMEDIATE` through transaction completion.

A separate first-statement probe measured the median session lookup at
0.589 ms on main and 0.711 ms on the League build with fresh connections.
Preparing the schema with a zero-row read before acquiring the lock moved the
lookup to about 0.03 ms on both sources. Preparing on every write still
incurred the cold work. Reusing the guarded connections reduced both
preparation and time in the lock. The revised median direct tournament lock
wait p95 was 1.12 ms, down from 3.20 ms on main. Its median writer hold was
0.77 ms, down from 1.58 ms. These intervals include SQLite operations and
local scheduling. The timed SQL subset excludes cursor fetch and commit.

For an ordinary rated concession, the two reviewed League result statements
cost 33.0 microseconds in total per result at the median across runs. The
single indexed provenance lookup in the revision cost 6.3 microseconds. The
shared service and worker trials recorded zero operation errors. In the live
mixed arm all 40 League settlements completed in each run alongside the real
tournament worker. The result supports the throughput and latency comparison
on these disposable workloads. Busy-lock tails still vary with scheduling.

### Live matchmaking

The worker ran at its normal one-second cadence. These are medians of three
repetitions per source with minimum and maximum in brackets. Post-commit p95
measures seconds from the start of actionable work among committed pairings.
Queue p95 includes any time the fixture spent paused or waiting for a window.
The fixed sample denominator is ten seconds, including the time between
cycles. Entries are counted as expired once their authoritative lifetime
elapses, even when cleanup still has them pending.

| Scenario | Source | Pairs/s | Post-commit p95 s | Queue p95 s | Expired eligible entries | Worker lock wait p99 ms | Process CPU s | Worker writes | Ordinary move p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| compatible | reviewed | 0.90 [0.90–0.90] | 9.10 [9.08–9.14] | 9.07 [9.06–9.09] | 0.00 [0.00–0.00] | 0.08 [0.07–0.36] | 0.42 [0.37–0.47] | 9.00 [9.00–9.00] | — |
| compatible | revised | 12.00 [12.00–12.00] | 8.57 [8.56–9.55] | 8.53 [8.53–9.54] | 0.00 [0.00–0.00] | 0.05 [0.02–0.07] | 0.48 [0.47–0.48] | 121.00 [121.00–121.00] | — |
| resume | reviewed | 0.90 [0.90–0.90] | 9.05 [8.98–9.05] | 11.12 [11.07–11.15] | 0.00 [0.00–0.00] | 0.23 [0.07–0.27] | 0.49 [0.38–0.60] | 12.00 [11.00–12.00] | — |
| resume | revised | 12.00 [12.00–12.00] | 8.49 [8.43–9.45] | 10.54 [10.53–11.54] | 0.00 [0.00–0.00] | 0.04 [0.03–0.05] | 0.47 [0.46–0.50] | 121.00 [121.00–121.00] | — |
| expanding_windows | reviewed | 1.00 [1.00–1.00] | 9.13 [9.10–9.13] | 69.08 [69.08–69.11] | 0.00 [0.00–0.00] | 0.14 [0.07–0.19] | 0.49 [0.46–0.62] | 11.00 [11.00–11.00] | — |
| expanding_windows | revised | 12.00 [12.00–12.00] | 8.58 [8.56–8.58] | 68.55 [68.55–68.56] | 0.00 [0.00–0.00] | 0.04 [0.03–0.05] | 0.55 [0.54–0.57] | 125.00 [125.00–125.00] | — |
| capped_and_incompatible | reviewed | 0.00 [0.00–0.00] | — | — | 0.00 [0.00–0.00] | 0.16 [0.05–0.16] | 0.40 [0.36–0.48] | 9.00 [9.00–9.00] | — |
| capped_and_incompatible | revised | 0.00 [0.00–0.00] | — | — | 0.00 [0.00–0.00] | 0.16 [0.05–0.26] | 0.32 [0.32–0.33] | 11.00 [11.00–11.00] | — |
| mixed_online | reviewed | 0.90 [0.90–0.90] | 9.09 [9.06–9.10] | 9.06 [9.06–9.07] | 0.00 [0.00–0.00] | 0.10 [0.07–0.11] | 0.67 [0.63–0.73] | 9.00 [9.00–9.00] | 38.20 [36.49–42.08] |
| mixed_online | revised | 12.00 [12.00–12.00] | 8.56 [8.53–8.57] | 8.52 [8.51–8.53] | 0.00 [0.00–0.00] | 0.03 [0.03–0.07] | 0.65 [0.65–0.67] | 121.00 [121.00–121.00] | 22.99 [22.87–23.00] |
| expiry | reviewed | 0.20 [0.20–0.20] | 2.02 [2.02–2.03] | 599.01 [599.01–599.02] | 236.00 [236.00–236.00] | 0.09 [0.09–0.75] | 0.41 [0.40–0.45] | 16.00 [16.00–16.00] | — |
| expiry | revised | 2.70 [2.60–2.80] | 2.17 [2.16–2.17] | 599.12 [599.12–599.12] | 186.00 [184.00–188.00] | 0.08 [0.04–0.27] | 0.38 [0.35–0.42] | 42.00 [41.00–43.00] | — |

The revised worker seated all 120 pairs in the compatible, resume, simultaneous
expansion and mixed-online trials. In the compatible workload the last pairing
became visible 9.59 seconds after the start at the median. The reviewed worker
paired nine in the same window. With three seconds left in the expiry workload,
the revised worker paired a median of 27 and 186 eligible entries expired.
Two pairs formed and 236 entries expired on the reviewed worker. The
incompatible and pair-capped fixture formed zero matches. The revised worker
spent 0.32 seconds of process CPU over that roughly 10-second observation.
The first sweeps can visit several small pages. After repeated misses, the
steady backoff allows up to three discovery transactions per eligible ruleset
every eight seconds.

These are observed waits for the specified fixtures. A finite compatibility
window, the remaining entry lifetime and concurrent writer contention all
affect the queue. Starter and opponent positions favor age within each visited
page, and an opponent that becomes compatible behind a saved cursor may wait
for a wrap. The deterministic tests cover multiple pages, independently
expanding windows, separate ruleset budgets, side assignment and lock deferral.

### Personal rank

Each cell below is p95 for 60 authenticated ASGI endpoint requests. The rank
work probe counts SQLite VM instructions in the authoritative read snapshot
in increments of 100. A quarter of each fixture forms one sporting tie, and
all its members share the displayed rank. The `/current` response was also
measured and is included in the raw results.

| Qualified players per season | Account | Rank | SQLite VM work floor | Current 1 reader ms | Current 6 readers ms | Archived 1 reader ms | Archived 6 readers ms |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,024 | high | 1 | 1,400 | 5.26 | 30.58 | 4.97 | 32.76 |
| 1,024 | tie | 129 | 1,900 | 4.60 | 34.93 | 5.05 | 28.43 |
| 1,024 | middle | 513 | 3,500 | 6.42 | 30.08 | 7.52 | 35.39 |
| 1,024 | low | 1,024 | 5,500 | 4.77 | 30.30 | 4.91 | 28.70 |
| 16,384 | high | 1 | 1,400 | 4.14 | 32.04 | 4.59 | 29.28 |
| 16,384 | tie | 2,049 | 9,600 | 4.76 | 30.27 | 5.06 | 41.48 |
| 16,384 | middle | 8,193 | 34,200 | 5.04 | 30.30 | 4.86 | 34.18 |
| 16,384 | low | 16,384 | 67,000 | 5.60 | 38.92 | 5.59 | 37.71 |
| 131,072 | high | 1 | 1,400 | 4.23 | 33.80 | 4.21 | 29.50 |
| 131,072 | tie | 16,385 | 67,000 | 10.50 | 31.56 | 6.28 | 33.58 |
| 131,072 | middle | 65,537 | 263,600 | 8.43 | 40.95 | 8.66 | 44.30 |
| 131,072 | low | 131,072 | 525,700 | 12.64 | 68.33 | 16.95 | 86.17 |

All sporting ranges used the qualified standings index. Work grows with the
population ahead of the player, including the cost of counting prior members
of a large tie for lower-ranked players. The lowest-ranked archived read in
the largest fixture measured 16.95 ms p95 alone and 86.17 ms p95 at six
concurrent reads. Given this interactive workload and the consistency cost
of another projection, the implementation keeps the exact indexed counts.

[Repeated shared-writer and live-worker measurements](./browser-analysis/evidence/measurements/league-arena-review-backend.json) ·
[Personal rank measurements](./browser-analysis/evidence/measurements/league-arena-review-rank.json)

### Network, interface and compatibility

The loopback Chromium 154 production probe at `d023e3d` observed four first-visit
League reads and no League requests in the first 17 idle seconds. Through
65 idle seconds it observed one queue read of 583 bytes. An explicit refresh
made five reads. Joining made one POST and one reconciliation GET. The next
17 queued seconds made four queue reads. The 4.5-second leave window had no
League requests and the complete probe recorded zero HTTP or network errors.

The measured initial JavaScript was 186,674 gzip bytes against its 200,000-byte
budget. The deferred League chunk was 10,210 gzip bytes and its stylesheet was
629 gzip bytes. Both loaded when the League navigation was used. The browser
probe preceded moving the existing UTC season countdown into the matchmaking
panel during archived inspection. The production build checks the final bundle.
[Production browser measurements](./browser-analysis/evidence/measurements/league-arena-review-browser.json)

The focused Playwright suite passed 14 cases across desktop and mobile Chromium.
It covers lost join replies before navigation and reload, late original joins,
account switches, reconnect, qualified and unqualified archived progress, empty
history, revised rankings, rebuilds and recovery from a remote pause without
reloading. The Arena checks cover four themes, English and German controls,
keyboard focus, reduced motion and touch target sizes. The existing navigation
suite covers interface scaling and the compact navigation breakpoint.

The focused backend run passed 211 tests, including the account authorization,
queue, result, restore, shared-writer and League recovery coverage.

```sh
.venv/bin/python -m pytest -q \
  tests/test_leagues.py tests/test_league_api.py tests/test_league_migrations.py \
  tests/test_league_restore.py tests/test_online_core.py tests/test_online_api.py \
  tests/test_online_matches.py tests/test_online_migrations.py tests/test_security.py
```

The repository and browser gates use the existing disposable fixtures.

```sh
make lint
make typecheck
make foundation
make check
make historical-refs
make deployment-check
make deployment-cross-version
npm --prefix frontend run test:scripts
npm --prefix frontend run audit:ci
uv audit --frozen --python-version 3.12
.venv/bin/python scripts/audit_api.py
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:online -- leagues.spec.ts
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:e2e -- workspace-navigation.spec.ts
```

The authorization inventory matched 133 grants. The full and production npm
audits reported zero affected packages. The lock update from OpenTelemetry API
1.45.1 to 1.45.2 cleared the Python advisory. The rerun found zero known
vulnerabilities across 52 packages. Schema 23 carries the additional
request lookup index. Baseline and League fixtures exercised the existing
schema 22 to 23 upgrade on copies of the same database.

The PR verification includes the full backend and frontend tests, deployment
lifecycle, pinned historical deployments, Node 22, production bundle budgets,
and the public, Arena, registration, online and personal Chromium suites.
[Final PR checks](https://github.com/freeDom-/hnefatafl/pull/99/checks)

## Final review at `7f84067`

The branch uses schema 23, the rated online authority, League policy and
Arena components.

- **P1.1 Matchmaking discovery** Starter and opponent continuations stay ready
  within the existing burst budget. The worker backs off after an unsuccessful
  completed sweep and schedules rating-window checks around the next possible
  opening. A busy writer leaves the cursor ready for the next tick. Tests cover
  cross-ruleset scheduling under churn, a committed ordinary move between
  pairing transactions, capped opponents, independently opening windows and
  expiry on either side of discovery.
- **P2.1 Unresolved queue requests** After a fresh read, the browser offers an
  explicit retry with the saved UUID, ruleset and generation. Discarding a
  current unresolved request records a terminal outcome in the ledger before
  the browser retires it. Once the generation advances, older sibling intents
  retire as well. API and browser tests exercise concurrent join and discard,
  lost replies, reload and navigation, stale generations, two tabs, logout
  and account switching.
- **P2.2 Administrative pagination** Up to 20 rows are displayed per page.
  Expiry pages seek on the existing `(expires_at, account_id)` index. Once
  expiry is exhausted, eligibility pages seek by queue rowid and read up to
  128 rows plus lookahead. The expiry boundary is fixed for the traversal and
  unread eligibility reflects current account state. Tests remove a page
  boundary, restore eligibility, expire another entry and race an exact
  cancellation with a replacement. [Cursor behavior](../game/leagues.md#api-and-recovery)
- **P2.3 Eligibility reconciliation** The lifespan-owned worker uses the
  shared eligibility policy and rechecks the original request UUID before
  cancelling. Tests exercise administrative pause, disabled admission and the
  timeout gate with expired, disabled and unverified entries. They also check
  deployment deferral and assert that existing games, results and ratings
  retain their prior state during queue reconciliation.
- **P2.4 Shared SQLite writers** Shutdown invalidates outstanding leases even
  when the same application starts again. Database replacement invalidates
  idle handles associated with its previous inode. Reused handles clear
  callbacks and restore the row and text factories, foreign keys and the busy
  timeout. Tests exercise simultaneous independent leases, commit and rollback,
  SQLite failure, the idle limit and shared online and tournament writer use
  across repeated startup and shutdown.

### Controlled worker and shared-writer trials

The comparison uses the runtime in `3341b6d` and reviewed source `7f84067`.
The ten shared-writer trials start from copies of one main database at
`f2590b9`. Even-numbered trials reverse source order.
The one-second worker cadence, lock and thread limits are the same in both
arms. The 10-second runs repeat the established 240-player, window-widening,
pair-cap and expiry fixtures three times per source. The 42-second runs repeat
the four extended scenarios twice per source. The 50 ms observation interval
measures post-commit visibility. Reported queue waits use authoritative
pairing timestamps and include any time already spent in the queue.

The extended workloads include 960 compatible players, a 2,304-entry sparse
pool with four compatible players at the end, and a sparse three-ruleset pool
with 1,024 entries per ruleset. The sparse single-pool fixture starts with
32 seconds of life left. The fourth scenario uses 360 players per ruleset
with provisional ratings, a seeded Elo distribution, skill tails and staggered
arrival ages. Both three-ruleset workloads add an arrival about every half
second, cancel it through the real queue cancellation path, and schedule 100
ordinary moves across six foreground writers. The expected pairs in the
sparse scenarios consist of the four tail entries in each ruleset. Ratings
spaced far outside the matching window isolate the other starters on purpose.

Trial figures are medians with minimum and maximum in brackets.

| Shared workload | Reviewed `7f84067` | Corrected `3341b6d` |
| --- | ---: | ---: |
| Common service operations/s | 584.29 [550.55–656.47] | 594.60 [563.48–643.33] |
| Online move service p95 ms | 3.97 [2.92–7.47] | 3.27 [1.97–22.59] |
| Online move service p99 ms | 38.17 [22.88–81.33] | 37.45 [24.14–109.14] |
| Online move writer hold p50 ms | 1.19 [1.05–1.26] | 1.17 [1.12–1.23] |
| Tournament settlement service p95 ms | 32.77 [19.80–55.20] | 19.59 [14.66–46.51] |
| Live mixed online move p95 ms | 56.60 [36.78–72.34] | 39.99 [37.05–60.64] |
| Live mixed online move p99 ms | 106.27 [58.53–144.47] | 105.98 [82.45–183.63] |

| Scenario | Source | Pairs | First pair s | Last pair s | Queue p95 s | Target players expired |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| compatible | 7f84067 | 120 | 1.03 [1.03–1.04] | 9.60 [9.60–9.63] | 9.55 [8.52–9.56] | 0 |
| compatible | 3341b6d | 120 | 1.03 [1.03–1.04] | 9.62 [9.61–9.63] | 9.56 [8.53–9.56] | 0 |
| expanding windows | 7f84067 | 120 | 0.07 [0.04–0.07] | 9.63 [8.62–9.67] | 69.59 [68.56–69.60] | 0 |
| expanding windows | 3341b6d | 120 | 0.11 [0.08–0.12] | 9.67 [8.64–9.73] | 69.62 [67.60–69.65] | 0 |
| capped and incompatible | 7f84067 | 0 | — | — | — | 0 |
| capped and incompatible | 3341b6d | 0 | — | — | — | 0 |
| expiry | 7f84067 | 27 [27–28] | 1.03 [1.03–1.04] | 2.17 | 599.12 | 186 [184–186] |
| expiry | 3341b6d | 28 [27–29] | 1.03 [1.03–1.04] | 2.18 [2.17–2.18] | 599.13 [599.12–599.13] | 184 [182–186] |

| Scenario | Source | Pairs | First pair s | Last pair s | Queue p95 s | Target players expired |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| large compatible | 7f84067 | 480 | 1.04 [1.03–1.05] | 36.26 [36.24–36.28] | 34.63 [34.12–35.15] | 0 |
| large compatible | 3341b6d | 480 | 1.03 | 38.41 [38.39–38.44] | 36.77 [36.25–37.29] | 0 |
| sparse tail | 7f84067 | 0 | — | — | — | 4 |
| sparse tail | 3341b6d | 2 | 22.39 [22.38–22.40] | 22.39 [22.38–22.40] | 590.37 | 0 |
| sparse mixed | 7f84067 | 0 | — | — | — | 0 |
| sparse mixed | 3341b6d | 6 | 29.38 [28.86–29.90] | 29.90 [29.89–29.90] | 569.89 [569.88–569.89] | 0 |
| realistic mixed | 7f84067 | 530 [523–537] | 1.03 | 41.61 [41.60–41.62] | 102.76 | 0 |
| realistic mixed | 3341b6d | 533 [531–535] | 1.03 | 41.61 [41.60–41.63] | 102.76 [102.75–102.77] | 0 |

The corrected worker seated all 120 pairs in each 10-second compatible
trial. Both sources seated all 480 pairs in the large compatible runs. The
corrected source took 38.39–38.44 seconds to the last visible match, about two
seconds longer than the reviewed source in these runs. The sparse single-pool
worker found both pairs with about 10 seconds of entry life left, and all four
target players expired in each reviewed run. All six sparse multi-ruleset pairs
were visible by 29.91 seconds. The realistic workload seated 531–535 of 540
possible pairs in the corrected runs and 523–537 in the reviewed runs.
The expansion fixture measures its first and last visibility times from two
seconds after worker start, when the seeded rating windows can first overlap.

Both mixed discovery workloads completed the 83 arrival/cancellation cycles
and 100 ordinary moves in each run. Ordinary move p95 during corrected sparse
discovery was 36.74–36.82 ms, compared with 22.71–23.96 ms in the reviewed
runs, which made less discovery progress. The corrected realistic workload
recorded 23.92–36.45 ms and the reviewed workload recorded 24.63–35.37 ms.
The corrected no-match fixture used 0.36–0.40 seconds of process CPU over its
10-second observation window, including the observer. The same fixture at the
reviewed revision used 0.36–0.39 seconds in this comparison.

The corrected common service throughput overlaps the earlier 523.42–642.65
operations/s range around the cited 621.06 median. The repeated online move
p95 was 3.27 ms, p99 was 37.45 ms and median writer hold was 1.17 ms. One
corrected common workload reached a 22.59 ms move p95 and 109.14 ms p99. The
trial data retains that variation. These measurements use Python 3.12.14
and SQLite 3.53.1 on disposable Linux storage.

Run `scripts/benchmark_league_workers.py` from this source tree with the
repository test lock and `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2` and
`OPENBLAS_NUM_THREADS=2`. Archive the three revisions into disposable directories
as in [the earlier reproduction](#reproduction). Use the main archive only to
build the shared database template. For each measured source, set `PYTHONPATH`
to its archive and supply the corresponding `--source-revision` and an `--output`
file for each trial.

```sh
.venv/bin/python scripts/benchmark_league_workers.py prepare --baseline --template "$work/common"
.venv/bin/python scripts/benchmark_league_workers.py shared --template "$work/common"
.venv/bin/python scripts/benchmark_league_workers.py matchmaking --worker-seconds 10 \
  --scenario compatible --scenario expanding_windows --scenario capped_and_incompatible --scenario expiry
.venv/bin/python scripts/benchmark_league_workers.py matchmaking --worker-seconds 42 \
  --scenario large_compatible --scenario sparse_tail --scenario sparse_mixed --scenario realistic_mixed
```

The shared and live mixed arms use 100 foreground samples and six foreground
writers. Callback tracing starts after leasing a writer, so the corrected
source's timeout and foreign-key setup run outside the trace. The online
service latency and writer timing boundaries are comparable across sources.
[Trial data and source hashes](./browser-analysis/evidence/measurements/league-arena-final-review-backend.json)

### Regression gates

The focused backend run passed 224 tests across League queue, API, migration
and restore, ordinary online core and API, return games, online migrations and
security. The Chromium online suite passed all 44 desktop and mobile cases,
including 18 League cases. Coverage includes explicit retries after reload and
navigation, lost replies, discard followed by a delayed original join, account
switches, exact cancellation, clocks, sequential and simultaneous games, and
player tournaments.

```sh
.venv/bin/python -m pytest -q \
  tests/test_leagues.py tests/test_league_api.py tests/test_league_migrations.py \
  tests/test_league_restore.py tests/test_online_core.py tests/test_online_api.py \
  tests/test_online_matches.py tests/test_online_migrations.py tests/test_security.py
npm --prefix frontend run test:online
```

`make deployment-check` passed 64 deployment and lifecycle cases and nine
selected tournament recovery cases. `make historical-refs` and
`make deployment-cross-version` passed, including seven pinned cross-version
cases. Ruff, mypy, frontend type checking, formatting, `make foundation` and
`make docs` passed. The authorization inventory accounted for 134 route grants.
The Python audit found zero known vulnerabilities across 52 packages. The full
and production npm dependency audits reported zero affected packages.

The frontend passed 1,590 unit tests across 109 files, 12 benchmark-harness
tests and 43 build and script tests. The production build passed the initial
bundle budgets with 186,715 gzip bytes of JavaScript and 24,114 gzip bytes of
CSS. The League route remains lazy-loaded.

The production Chromium 156.0.8078.4 probe at `d005199` made four
first-visit League reads, zero in the first 17 idle seconds and one 583-byte
queue read through 65 idle seconds. Manual refresh made five reads. Joining
made one POST and one reconciliation GET. The next 17 queued seconds made
four reads. The 4.5-second leave window had zero League reads. The probe
recorded zero HTTP and network errors. The deferred League JavaScript was
10,327 gzip bytes and its stylesheet was 629 gzip bytes.
[Production browser measurements](./browser-analysis/evidence/measurements/league-arena-final-review-browser.json)

The PR's `Verify` workflow covers the full backend, deployment compatibility,
Node 22, dependency audit and public, navigation, registration, online and
personal Chromium suites. [Final PR checks](https://github.com/freeDom-/hnefatafl/pull/99/checks)

### Operational limits

Newly compatible opponents behind the stored positions may wait for a wrap.
Live waits depend on eligible opponents, available queue time and writer
contention. Large expired or ineligible backlogs drain in bounded batches. An
admin refresh starts a new traversal for recently expired entries or entries
that changed behind the cursor. Recording a first discard consumes one slot
in the existing request budget. The browser preserves the pending request if
that write is rate-limited. Mobile browser checks use Chromium emulation.
