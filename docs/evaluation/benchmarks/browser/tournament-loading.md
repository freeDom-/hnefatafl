# Tournament collection loading

[Benchmarks index](../README.md) · [Performance guide](../../../training/performance.md)

## Collection loading measurement

A saved tournament retains every match with its moves, state and diagnostics, so
a collection grows much larger than the navigator needs. The collection endpoint
now projects the summary inside SQLite (`json_remove(data, '$.result')`) instead
of decoding the retained history in Python, and the browser fetches the selected
tournament's full record only when its status, completed game count or end time
changes.

`scripts/benchmark_tournament_summaries.py` builds a synthetic collection in a
temporary database — 24 completed tournaments with 200 games and 60 plies each
(10.6 MB of SQLite) — and reproduces the previous list and poll paths beside the
current ones:

| Measurement | Before | After |
| --- | --- | --- |
| Python JSON bytes decoded per collection read | 20.4 MiB | 14 KiB |
| SQLite reads per collection listing | 25 | 1 |
| Median collection time (manager plus projection) | 0.753 s | 0.178 s |
| Collection response body | 18,766 B | 18,766 B (unchanged) |
| 600 polls with one selected tournament | 266.4 MB | 11.7 MB |

The selected tournament's complete record is 425,301 bytes, so the polling
reduction comes from never re-downloading an unchanged match history. These
numbers made an incremental or paginated match-history endpoint unnecessary for
the current architecture.

Polling a *running* tournament is measured the same way, with a deterministic
worker handle so the record stays running. Liveness needs only the tournament id
and the worker pid, both present in the summary projection, and the failure
transition is a conditional `json_set` update that never overwrites a terminal
state:

| One summary poll of a running tournament | Before | After |
| --- | --- | --- |
| Python JSON bytes decoded | 850,574 B | 572 B |
| Database reads | 2 | 1 |
| Median duration | 0.023 s | 0.007 s |
| 600 polls | 510.3 MB | 343 KB |
