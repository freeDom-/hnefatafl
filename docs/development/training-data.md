# Training data, checkpoints and archives

[Development index](README.md) · [Models guide](../training/models.md) · [API](api/README.md)

## Storage

The persistent data root contains the account SQLite database, training jobs,
full checkpoints, registered model copies, optimization studies, job archives,
remote-worker state and optional published browser artifacts. Release directories
and virtual environments are separate and replaceable; backups must include the
complete data root.

A training job's directory holds `config.json`, `progress.json`, `heartbeat.json`,
structured logs, full `last.pt` checkpoints, evaluation series and final `result.json`.
A registered model copy is separate from its source job. Deleting training data does
not delete a playable model; deleting a model does not delete its job.

## Checkpoint formats

`tafl-policy-value-v1` checkpoints store tensors and primitive values, never pickled
model instances. Format version 1 includes model/optimizer state, replay, RNG and
CUDA/scaler state for resumption. The validated tensor-only loader enforces a 128 MiB
safe limit and architecture compatibility. Inference-only exports omit full training
state and cannot resume.

Immutable resume identity: ruleset, channels, blocks, seed, optimizer and
Schedule-Free warm-up duration. Iterations are a target total and cannot discard
completed work. Other settings are editable under review. Schedule-Free keeps its
averaging/moment state; an unsafe live optimizer transition blocks further checkpoint
writes in favour of the previous valid checkpoint.

## Job archives and migration

Fixture provenance for legacy archives is documented in
[`tests/fixtures/job_archives/README.md`](../../tests/fixtures/job_archives/README.md).

Portable job archives preserve provenance, rune/model identity, checkpoints, history
and, where applicable, contained Optuna studies. Import validates the archive version,
integrity and references; incompatible/corrupt/oversized archives fail closed.
Moving an installation requires the database plus job/model/study directories; a
model import can restore playable weights but does not recreate a training job.

## Backup and recovery

Use a stopped-writer or supported consistent backup of the complete data root. A
live SQLite database file copied without its WAL is not consistent. Restore into a
disposable directory first, verify accounts, games, jobs, studies, model hashes and
an actual resume, then replace live data. The operator procedure and commands are in
[models](../training/models.md#backup-and-restoration) and
[installation recovery](../installation/updating.md).

## Tuning study identity

The HTTP API takes the source as a
portable `source_checkpoint` token (`<job_id>:last.pt`), resolves it server-side
and records it for archive export; the CLI uses the nested `training.resume`
path. The API also accepts `<job_id>:trial-N/objective.pt` for the full state of the
selected candidate, or `<job_id>:trial-N/last.pt` for the final trial state.

## Study identity and resumption

Identity version 2 records tuning mode; a stable content digest of source
weights, optimizer, replay and RNG/checkpoint state; source iteration; ruleset and
network architecture; all fixed effective training settings (including iterations,
games, epochs, replay size, optimization, numerical backend, concurrency and
search deadlines); model and sampler seeds; search space; evaluation protocol,
game budgets and seeds; immutable frozen-model content; opponent weights;
objective policy; and sampler/pruner settings. Machine-local paths and cosmetic
names are excluded. Source identities are cached in a generation-checked sidecar
when loaded for creation, never hashed during dashboard polls. Replacing a source
file invalidates the cache.

Both the API (before launching a subprocess) and the worker compare identities
and report conflicting fields. The additional trial/failure budgets, verification
request, model label and artifact publication cadence may change. Fixed trial
workload, including iterations per trial, cannot. Previous configuration and
Optuna history/result files are preserved on rejection. A nonblocking file lock
serializes local SQLite CLI invocations through finalization. Each trial checks
its loaded source generation/content before updating, so replaced/tampered source
state cannot silently enter an existing study. External RDB deployments must
serialize study invocations operationally; the server supports owned SQLite only.

Legacy studies are **never assigned the current requested experiment**. A missing
fingerprint can be reconstructed only when every historical trial independently
records the same complete version-2 identity. Version-1 fingerprints lack required
workload/content and use historical scoring semantics. Ambiguous older studies
remain inspectable/exportable with unchanged rankings; further trials require a
new study. Use the old winner's configuration or checkpoint to start that study.
