# Training monitoring internals

[Development index](README.md) · [Training interface](../training/interface.md) ·
[Monitoring benchmarks](../evaluation/benchmarks/training-monitoring.md)

This page records the internal publication, snapshot-identity and polling
behaviour behind the operator-facing training monitor. User guidance and the
heartbeat-state table remain in the [training interface](../training/interface.md).

## Three independent signals

The training process separates the worker heartbeat (the process is alive),
computational progress (training work advanced) and publication (a snapshot
reached the file the server or remote worker reads). A long self-play game,
optimization epoch or evaluation match can therefore keep the monitor live while
no counter moves.

Outside a trial, a tuning study publishes `initializing_study`, `preparing_trial`,
`between_trials` and `finalizing_study` with the study's timers and completed-trial
counter. The last trial's charts remain visible, but its finished phase and timers
are not presented as live, and its heartbeat cannot overwrite the study snapshot.

Ownership of the job files moves from a trial monitor to the study monitor and back
under the publication lock. The study publishes `between_trials` before Optuna
samples the next trial, stores the finished trial or reads study trials, so a slow
storage operation cannot leave the dashboard without a heartbeat. The
completed-trial counter follows Optuna's committed states and never counts pruned,
cancelled or failed trials.

## Publication freshness

Publication freshness is measured by the server, not the browser, and is reported
only for a running or cancelling job. A local job uses the newest modification time
of `progress.json` and `heartbeat.json` on the server host. A remote job reports the
child's own publication age on the worker host and the time since the server last
heard from its supervisor. The server extends both with its own clock; the browser
only anchors and advances them. Reloading the dashboard therefore classifies an
already-stale worker immediately instead of granting a fresh grace period, and an
unsynchronised worker clock never enters the calculation.

A supervisor can stay connected while the training process stops publishing. A
repeated unchanged child snapshot never counts as a fresh child heartbeat; that is
reported as **No recent updates**, while a silent supervisor is **Worker connection
lost**. A job whose lease expires then ends as failed with the reason recorded.

The browser also tracks its own last authoritative monitoring response. It polls
every 2.5 seconds; if no answer arrives for twenty seconds it reports **Monitoring
connection unavailable** rather than blaming the worker. Reconnecting adopts fresh
server state immediately. The child publication age, server-confirmed supervisor
age and browser connection therefore stay independent.

Heartbeats are written to a bounded `heartbeat.json` beside retained
`progress.json`. A reader overlays a heartbeat only when it belongs to the same
publication generation, so a stale heartbeat cannot revive a terminal state or
replace newer progress. See the
[monitoring overhead measurements](../evaluation/benchmarks/training-monitoring.md)
for measured cost.

## Timers and rendering

Elapsed time is published as a monotonic duration, never a worker timestamp. The
browser anchors each reported value when the response arrives and advances it with a
single one-second local ticker, so clock skew cannot affect the display. Timers never
move backwards: a new phase restarts only the phase timer, a new iteration restarts
only the iteration timer (identified by `active_iteration`, its start timestamp and
run identity), and a terminal job adopts authoritative backend durations and stops.
The ticker lives in the timer leaf, so one second of display time does not re-render
charts or tables.

Iteration timing exists only while an iteration runs. Loading a checkpoint, building
the model, restoring optimizer/training state and preparing inference publish job
and phase timers plus a heartbeat, but no iteration duration. The iteration timer
starts at zero when the first iteration enters `preparing`.

The live header follows `active_iteration`, while the overview **Iteration** KPI
keeps the last completed iteration used by checkpointing and resume. Finalization
(checkpoint verification, model export and registration) keeps the heartbeat
running; a failure there ends the job as failed instead of leaving it running.

## Polling, navigation and worker cadence

The dashboard polls every 2.5 seconds while jobs or registered workers need updates.
Slow requests are coalesced so their eventual responses update the dashboard without
overlapping reads. Hardware detection and worker connectivity load independently
from job progress. A local job's live state comes from its retained progress file
plus the bounded heartbeat; a remote worker republishes its snapshot at a bounded
cadence independent of its lease heartbeat, so intermediate counters reach the
server during a running phase.

The navigator requests compact job summaries. Full history and the bounded log tail
load for the selected job; switching jobs cancels obsolete detail requests and
preserves the selected analysis tab. Full histories, exports and checkpoints remain
retained.

## Operator contract

Job management and outcome never depend on heartbeat labels. The interface page is
the canonical description of displayed states, phase counters and restrictions.
Do not infer model strength or throughput from a synthetic or partially recorded
monitor snapshot.
