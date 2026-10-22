# Remote training workers

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

   Replace the server URL and concurrency with your setup. No incoming port needs
   to be opened on the worker. Keep its work directory persistent and private.
   The token is not accepted as a command-line argument or placed in URLs.

5. The worker appears online with its reported hardware. In the workshop, choose
   **Run training on → Any remote worker** or that named worker and start training.
   Submit additional models while earlier jobs are running. Pending jobs wait in
   the server queue until a compatible slot can claim them.

The locked default installs CPU PyTorch. On an NVIDIA machine, install the
appropriate official CUDA build and verify `cuda_available` with `devices` before
choosing CUDA. Recent `uv` versions support automatic backend selection:

```sh
uv pip install --python .venv/bin/python --reinstall --torch-backend=auto 'torch>=2.6,<3'
.venv/bin/python -m hnefatafl.cli devices
```

Check the [PyTorch installation selector](https://pytorch.org/get-started/locally/)
for your driver/platform. A later locked `uv sync` can reinstall the CPU wheel.
The server does not need a GPU to accept a job intended for a CUDA worker.
Choose **Automatic** to let the worker select its available device. GPU execution
requires suitable hardware and drivers; the release's functional checks use CPU.

Slots share the worker's visible devices; the client does not automatically
allocate a different GPU to each slot. To isolate GPUs, run separate enrolled
workers with separate work directories and `CUDA_VISIBLE_DEVICES` selections.
Choose concurrency according to available GPU memory and CPU/RAM, not just the
number of models you want to queue.

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
credential to reconnect a revoked machine. Tokens are installation credentials,
not accounts for other application features.

Use a private network/VPN, or HTTPS with a reverse proxy that permits remote
access only to `/api/worker/` routes. Keep the browser, enrollment routes
(`/api/training/workers`) and other administrative APIs private or protected by
your proxy's authentication. Worker bearer authentication does **not** add account
authentication to the rest of the application. TLS verification remains enabled;
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
| `GET /api/training/workers` | List worker status, capacity and reported capabilities |
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
