# Remote training workers

[Documentation index](README.md) · [Data and migration](TRAINING_DATA.md)

The server keeps the queue, progress, charts, checkpoints and registered models.
Remote workers pull jobs over HTTP and run the existing self-play trainer on
their own CPU or GPU. Each job has a separate training process. Several workers,
or several slots on one worker, can train independent models simultaneously.
This does not split a single model's optimization across machines.

## Connect a cloud machine

1. Update the server and worker checkout to the same application release.
2. Open **AI workshop → Remote workers → Connect a worker**. Enter its name and
   the number of parallel jobs its hardware can support. Save the worker token:
   the server returns it only once and stores its hash.
3. Install Python and `uv` on the cloud machine, then install training dependencies
   from the checkout. The browser client and Node are unnecessary on workers:

   ```sh
   uv sync --frozen --extra train
   .venv/bin/python -m hnefatafl.cli devices
   ```

4. Supply the token privately through the environment and start the worker:

   ```sh
   read -r -s -p 'Worker token: ' HNEFATAFL_WORKER_TOKEN
   export HNEFATAFL_WORKER_TOKEN
   .venv/bin/python -m hnefatafl.cli worker \
     --server https://tafl.example.net \
     --work-dir ./data/cloud-worker \
     --concurrency 2
   ```

   The UI uses the server address from your browser. Replace it if the worker
   needs a different reachable address; do not use loopback for a separate machine.
   Adjust concurrency for your setup. No incoming port needs to be opened on the
   worker. Keep its work directory persistent and private.
   The token is not accepted as a command-line argument or placed in URLs.

5. The worker appears online with its reported hardware. In the workshop, choose
   **Run training on → Any remote worker** or that named worker and start training.
   Submit additional models while earlier jobs are running. Pending jobs wait in
   the server queue until a compatible slot can claim them.

The locked default installs CPU PyTorch. Follow the authoritative
[separate CUDA environment recipe](PERFORMANCE.md#separate-cuda-environment)
on an NVIDIA worker and use that environment's Python for the worker command.
Do not resynchronize a CPU lock into the CUDA environment. Verify actual device
availability with `devices` first. The server need not have a GPU to queue a job
for a GPU worker; explicit device jobs wait for compatible advertised hardware.
Automatic device selection runs on the worker. CUDA/MPS execution remains
unverified on the release's CPU host.

Slots share the worker's visible devices; the client does not automatically
allocate a different GPU to each slot. To isolate GPUs, run separate enrolled
workers with separate work directories and `CUDA_VISIBLE_DEVICES` selections.
Choose concurrency according to available GPU memory and CPU/RAM, not just the
number of models you want to queue.

Worker job-slot concurrency is separate from a single job's
`self_play_concurrency` and `inference_batch_size`. See [Performance](PERFORMANCE.md)
for concurrent MCTS scheduling. Keep workers updated with the server's release;
v0.17.0 fields require the updated trainer even though the protocol remains 1.
The CLI accepts 1–16 slots; enrollment capacity must also permit those claims.

## Progress, models and resumption

The workshop lists every queued/running job and its worker. Open any job to view
the existing loss/evaluation charts, metrics and logs. Progress comes from saved
server reports and survives browser reloads or server restarts. Training continues
on a remote machine while the browser is closed.

Workers send reports independently of expensive training moves and upload full
checkpoints as training advances. The server validates tensor-only checkpoints
before retaining them. Completed inference models and optional new-best snapshots
are imported into the server's model registry; regular games need no access to
the worker's filesystem. Each artifact is limited to 128 MiB.

**Stop training** cancels a queued job immediately. For a claimed job it requests
a graceful stop; the worker saves and uploads its latest full checkpoint. Stop
only that job while other worker slots continue. If the worker is unreachable,
the request remains pending until the lease expires.

**Resume from checkpoint** reviews the last checkpoint received by the server.
Choose a higher target total iteration count and the execution target. A new job
gets its own copy of the source checkpoint, including optimizer/replay/random
state. Source-job deletion therefore cannot invalidate a queued remote resume.
The remote machine's most recent local progress may be newer than the last
successfully uploaded checkpoint after a network failure.

Training-data deletion and model deletion remain separate. Removing a stopped
job's server data preserves imported models and game histories. Worker-local
artifacts stay in that machine's private work directory; manage its disk retention
there. Remote Optuna studies are not supported in this release; tuning continues
to run on the server.

## Disconnects and access

Each claimed job has an expiring lease and ordered progress reports. Workers
renew the lease while training, including during long moves. Brief network errors
are retried. Repeated claims, reports and completion requests do not start another
copy of the same job. A lost/expired lease rejects late results and stops the
client's training process. The server marks the job failed, retaining its last
reported progress and uploaded checkpoint. Explicitly resume it to continue;
expired work is not automatically duplicated on another machine.

**Revoke access** invalidates a worker token, ends its active leases and cancels
jobs pinned to that worker. Other workers remain available. Create a fresh
credential to reconnect a revoked machine. Revoked machines disappear from the
management list immediately and stay hidden after browser or server restarts.
Disconnected machines with valid credentials remain listed. Revocation retains
worker audit records, training results, uploaded checkpoints and imported models.
Tokens are installation credentials, not accounts for other application features.

Use HTTPS through your existing reverse proxy (or a private VPN). Browser sessions
and administrator permissions protect enrollment and training management. Scoped
worker bearer tokens authenticate only exact worker routes and require an active
enrolling administrator. Legacy workers without that association must be re-enrolled
after upgrading; retained jobs and checkpoints remain intact. TLS verification remains enabled;
the client does not offer an insecure certificate bypass. Give a token only to
machines you trust with the assigned model/configuration and checkpoints.

The supported deployment still uses one API process. SQLite and filesystem state
must be backed up together, including worker credentials, jobs and models.

For an unattended Linux machine, adapt
[`hnefatafl-worker.service`](../deployment/hnefatafl-worker.service) and
[`worker.env.example`](../deployment/worker.env.example). Create the dedicated
service user and writable `/var/lib/hnefatafl-worker`, keep the real environment
file private, and match the service's concurrency to the enrolled capacity. GPU
device permissions must also be available to that service user. These templates
require validation on the target host; the functional tests run the CLI directly.

## API

The application API documentation at `/docs` describes request fields.

| Route | Purpose |
| --- | --- |
| `GET /api/training/workers` | List authorized workers (online or offline), capacity and capabilities |
| `POST /api/training/workers` | Create a named worker and return its one-time token |
| `DELETE /api/training/workers/{id}` | Revoke that worker |
| `POST /api/training/jobs` | Use `execution: "remote"` and optional `worker_id` to queue training |
| `POST /api/worker/heartbeat` | Authenticated worker presence and protocol/capabilities |
| `POST /api/worker/claim` | Claim work within worker capacity; retry with the same `request_id` |
| `POST /api/worker/jobs/{id}/progress` | Ordered progress/log report and lease renewal |
| `GET /api/worker/jobs/{id}/resume` | Download only that leased job's resume input |
| `POST /api/worker/jobs/{id}/artifacts/{kind}` | Upload `checkpoint`, `inference` or retained `best-{iteration}` |
| `POST /api/worker/jobs/{id}/finish` | Complete, cancel or fail the claimed job |

Worker routes require `Authorization: Bearer <worker token>`. Job updates also
require the current lease token. Resume downloads use `X-Lease-Token`; other
reports send the lease in their JSON/multipart bodies. Public job/worker lists
never include credentials or lease secrets.

Example job submitted from the trusted administrative side:

```json
{
  "execution": "remote",
  "kind": "train",
  "preset": "smoke",
  "ruleset": "brandubh",
  "model_name": "Cloud Raven",
  "device": "auto"
}
```

Without an explicit remote execution target, existing clients continue to start
local training. Local training retains its one-job limit; it can run alongside
remote training. Workers use protocol version 1 and should run the same release
as the server. Keep token values out of logs and URLs in custom clients as well.

## Troubleshooting

| Symptom | Check and next action |
| --- | --- |
| Job stays queued | Worker online status, token, enrolled/free slots, selected worker ID and advertised requested device; `auto` can use CPU |
| Unauthorized worker request | Correct server URL and current token; revoked credentials require fresh enrollment, not an insecure TLS bypass |
| Lease expired after disconnect | Inspect retained progress/checkpoint; explicitly resume a new job rather than reusing the old lease |
| Server lacks newest progress | Compare last successful report/upload with worker-local progress; only uploaded state can resume on the server |
| Model available but job not resumable | A compact model lacks full state; see [checkpoint types](TRAINING_DATA.md#checkpoints-registry-import-and-export) |
| GPU unavailable or out of memory | Check the worker's Python/driver/device permissions; reduce independent slots and per-job batches |
| Cleanup did not free worker disk | Server cleanup does not remove worker-local artifacts; stop affected processes and manage that private directory separately |

Service templates are examples. Verify real HTTP training, upload and server-side
model play on the target machine; heartbeat success alone does not validate it.


## Evaluation settings and compact logs

Updated workers accept the same Shield bearer/Jarl/Champion selection and resume
changes as local training. Full checkpoint uploads preserve versioned benchmark
and champion state, optimizer/replay/RNG state and history. Update the server and
workers to the same release before using these settings; older worker versions
reject unknown configuration fields. Progress polling still carries full history.
Worker subprocess stdout uses compact incremental events, omitting accumulated
history and detailed game lists while retaining failures and phase/result summaries.
See [evaluation and promotion](TRAINING.md#evaluation-and-model-selection).

Large histories use optional indexed chunks rather than repeatedly exceeding the
progress request limit. Ordered report sequences make retries idempotent, and
recovery retains already saved history. Small reports retain the original full
snapshot contract. Full checkpoints and worker JSON keep all evaluation records.

## Optional OpenVINO inference

See [Intel GPU worker setup](OPENVINO.md) for opt-in setup, defaults, checkpoint portability and measured limitations.

## Application compatibility and upgrades

Server and workers must run **the exact same Hnefatafl release**. Starting with
0.26.0, matching major/minor versions are insufficient: 0.25.0 and 0.26.0, or
0.26.0 and 0.26.1, cannot start new work together by default. Both processes use
Python's installed `importlib.metadata.version("hnefatafl")`, the same authority
as the server's application version. Reinstall/synchronize dependencies after
changing a checkout; a Git branch name does not change installed metadata.
Unknown, missing or malformed versions, including the uninstalled `0+local`
fallback, cannot establish eligibility. Heartbeat capabilities are full snapshots;
a report omitting `package_version` clears the preceding version report.

Optional `git_revision` comes only from immutable PEP 610 VCS installation
metadata (`direct_url.json`, complete 40/64-digit commit ID). Editable checkouts
and source/wheel installs without reliable VCS metadata report no revision.
Neither server nor worker invokes Git in normal operation. If both revisions are
known, they must match too. Missing optional revisions do not reject otherwise
matching releases. Version reporting is authenticated worker self-reporting; it
is not remote code attestation and does not detect unreported local modifications.

The **Remote workers** cards distinguish connectivity (online/offline) from
compatibility, show both installed versions, optional revisions, hardware and
any override. An outdated worker stays enrolled, keeps its token and reports
heartbeats normally. Explicitly selecting an incompatible worker fails with a
clear error; automatic selection skips it. The job dashboard distinguishes waits
for capacity, hardware/inference capabilities, application version and protocol.
Updating a worker's reported version automatically restores eligibility without
re-enrollment. A protocol mismatch remains rejected with HTTP 409, but its last
heartbeat and incompatibility remain visible to administrators.

A server restart/version change does not cancel claimed remote jobs. Valid
leases can still renew through progress, upload checkpoints/models, and complete;
idempotent retries of the original claim remain valid. Every additional claim is
checked against the running server, protocol, hardware, inference backend/cache
and current override under the same job lock. Lease expiry, ownership, enrolling
account authorization and revocation continue to apply. Keep downtime shorter
than the 120-second lease, or jobs may expire normally. This applies independently
to multiple simultaneous jobs. The production updater currently refuses active
jobs: finish/cancel them before using that updater. Lease continuity describes
an ordinary server restart/replacement, not a bypass of the updater's preflight.

To update a worker from 0.25.0 to 0.26.0:

1. Let existing training finish. Pause its claim loop during maintenance.
2. Update the installed source to the server's release (`git fetch --tags`, then
   `git checkout 0.26.0` for a checkout installation).
3. Run `uv sync --frozen --extra train` (add `--extra openvino` if needed).
4. Restart the worker command with the same token and private work directory.
5. Confirm that the next heartbeat shows matching versions and **Compatible**.

### Temporary administrator override

Only a logged-in administrator can enable **Version override** on an individual
worker after acknowledging the warning. It defaults off, persists in the worker's
existing SQLite JSON record, and records every change with account ID and UTC
timestamp in `version_override_history`; the latest actor/time are also exposed.
No schema migration or credential rotation is needed for existing registrations.
The yellow indicator identifies an active unsupported version combination.
Different versions can alter learning, checkpoint semantics or evaluation
behavior, making training inconsistent or benchmark comparisons invalid.

Overrides permit known application-version/commit mismatches only. They cannot
supply missing/invalid version metadata, bypass protocol/hardware/inference
checks, authorize revoked credentials or change existing leases. Disable the
exception after maintenance; incompatible workers immediately lose eligibility
for new claims while existing leases remain valid. There is no global bypass.

### API and retained software identity

- `GET /api/training/workers` and successful heartbeat responses add
  `compatibility`: `eligible`, `code`, localized-by-client `reason`, `mismatches`,
  `protocol_compatible`, `override_enabled`, `override_active`, `worker_version`,
  `server_version`, `worker_git_revision`, `server_git_revision`.
- Codes are `compatible`, `application_version_mismatch`,
  `unknown_application_version`, `git_revision_mismatch`, `incompatible_protocol`
  and `override_active`. Connectivity remains the separate `status` field.
  Eligibility covers application/protocol; per-job hardware checks also apply.
- `PATCH /api/training/workers/{worker_id}/version-override` accepts exactly
  `{"enabled": true}` or `{"enabled": false}` and returns the refreshed worker.
  An active administrator session, CSRF and same-origin protections are required;
  worker bearer credentials do not grant access. Revoked/missing workers return 404.
- Claims add the same `compatibility` object. An application-incompatible worker
  receives HTTP 200 with `job: null`; protocol incompatibility returns HTTP 409.
  Explicit incompatible targets return HTTP 409. Queued job progress adds
  `queue_reason_code` (`capacity`, `hardware`, `application_version`, `protocol`)
  alongside the human-readable `queue_reason`.
- At claim time, `job.software` freezes `worker` and `server` objects containing
  `package_version`/`git_revision`, plus `version_override_active`. Later heartbeats,
  server updates or claim retries do not rewrite this evidence. It appears in
  job Details and portable job ZIPs. Legacy jobs have no invented software identity;
  checkpoint tensor format and model metadata are unchanged.

### Troubleshooting incompatible workers

Compare both versions in the card, then run
`.venv/bin/python -c 'from importlib.metadata import version; print(version("hnefatafl"))'`
using the interpreter that actually starts the worker. Check for an old virtual
environment, a checkout changed without `uv sync`, or a worker supervisor that
was not restarted. Wait for its next heartbeat and refresh the workshop. An old
worker omitting `package_version` must be updated; re-enrolling alone will not fix
it. If known commits differ, install the same source revision on both sides.
Hardware/backend/cache incompatibilities require the corresponding runtime and
operational device, even when version overrides are enabled. Offline status
requires checking reachability and worker supervision separately from versions.
