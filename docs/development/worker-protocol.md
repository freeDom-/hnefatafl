# Remote worker protocol

[Development index](README.md) · [Worker guide](../training/remote-workers.md) · [API](api/README.md)

## Enrollment and identity

An administrator enrolls a worker in the Workshop. The server returns a one-time
bearer token and stores only its hash; the token is scoped to that worker and never
returned again. Worker cards expose name, capacity, status and advertised
capabilities but never the token.

Each heartbeat advertises package version, protocol, devices, inference backends,
cache/symmetry modes and optional feature capabilities. The server assigns only jobs
the worker can execute. Exact package/commit matching is the default; an administrator
can persist a version override for compatible software, but hardware/protocol and
algorithm capability gates cannot be bypassed.

## Claim, lease and progress

`POST /api/worker/claim` atomically selects compatible queued work and issues a lease.
Progress reports are ordered and owner-bound; a stale lease, obsolete generation or
different worker cannot publish. Progress includes bounded structured counters and
may be paired with a heartbeat; full history stays in the checkpoint, not in every log
line.

## Artifacts and completion

The worker uploads full checkpoints, registered model copies, history/progress and
optional tuning artifacts under size/type/digest validation. Temporary uploads are
cleaned up; incomplete final checkpoints are rejected. Completion acknowledgement is
idempotent; a lost response can be retried without double-registering a model.

A disconnect leaves uploaded state resumable. The server never resumes from worker
memory or a private local path. Resume transport copies the source checkpoint and
omits only backend preferences when they are not portable, preserving tensors,
optimizer/replay/RNG and source files.

## Compatibility

Jobs advertise capabilities such as parallel search, evaluation batching, symmetry
augmentation, Schedule-Free, HyperController state versions and iteration dwell.
Older workers may omit newer keys; a job requiring an absent capability stays queued
or is rejected. Server cleanup does not remove worker-local files. See
[worker troubleshooting](../training/troubleshooting.md#remote-workers).
