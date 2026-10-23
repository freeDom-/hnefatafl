# Installation and deployment

[Documentation index](README.md) · [Development](DEVELOPMENT.md) · [Data and backup](TRAINING_DATA.md)

## Requirements

Linux (including Proxmox LXC), Python 3.12 recommended (3.11+ supported), Node 22+
for building the browser client, and `uv` for the locked CPU environment. Node is unnecessary while serving a built
client. Training can run on CPU; useful longer experiments benefit from NVIDIA CUDA.
CI uses Node 24 and uv 0.12.23. See [Performance](PERFORMANCE.md) for device
installation and cost. Docker is optional.

## Local production build

From the repository root:

```sh
uv sync --frozen --all-extras
npm --prefix frontend ci
npm --prefix frontend run build
export HNEFATAFL_DATA_DIR="$PWD/data"
bash scripts/start.sh
```

The built web client and API share port 8000. Startup defaults to the loopback
interface. Check readiness with `curl --fail http://127.0.0.1:8000/api/health` and
then create a game in the browser. A running process alone does not prove gameplay.

For a phone on the same trusted LAN, set `HNEFATAFL_HOST=0.0.0.0` when invoking the
startup script, allow port 8000 on your local firewall and open
`http://<server-LAN-address>:8000` on the phone. The application requires a server
connection; offline gameplay and internet multiplayer are outside this release.

For live frontend development run `bash scripts/dev.sh`. Vite proxies `/api` to
the backend. Its dev server is for a trusted development network.

## Data and configuration

`HNEFATAFL_DATA_DIR` selects persistent application storage (default `./data`). Keep
the directory writable by the service account. It contains the SQLite database,
model registry, saved tournaments and training jobs/checkpoints/logs. `.env.example` documents startup
variables; it is an example, not an automatically loaded configuration file.
Do not commit data directories, `.env` files or model weights.

Database schema initialization runs at server startup and records a schema version.
Version 0.15 upgrades earlier databases to schema 7 automatically and additively:
schema 2 added player deletion timestamps and historical model metadata; schema 3
adds saved tournaments; schema 4 adds immutable per-position agent analysis;
schema 5 adds persistent identity rune assignments; schema 6 adds enrolled remote
training workers and their credential hashes; schema 7 adds per-game analytics
preferences and revisioned advisor reports. Older servers reject schema 7.
Existing game JSON,
stable player IDs, move versions and model/training files are preserved. Startup can
repeat the migration safely. Deleted players stay hidden under the new application;
rolling back to old code does not understand those tombstones, so restore a consistent
pre-upgrade data backup when rolling back.
For the complete storage tree, SQLite jobs registration, migration limitations and
consistent backups, use [Training data](TRAINING_DATA.md). Database backup alone is
not a coordinated snapshot of checkpoints/models. PostgreSQL migration tooling is
not implemented.

| Variable | Default / scope |
| --- | --- |
| `HNEFATAFL_DATA_DIR` | `./data` resolved relative to server working directory; writable persistent root |
| `HNEFATAFL_HOST` | `127.0.0.1`; read by `scripts/start.sh` |
| `HNEFATAFL_PORT` | `8000`; read by `scripts/start.sh` |
| `OMP_NUM_THREADS`, `MKL_NUM_THREADS` | Startup/development scripts default to 2 and respect explicit overrides |
| `VITE_API_TARGET` | Frontend dev proxy target, default `http://127.0.0.1:8000` |

The systemd unit calls uvicorn directly, so change its `ExecStart` for host/port or
CUDA Python; script-only host/port variables do not alter that command.

## Linux and Proxmox LXC preparation

Install Git, Python with venv support, Node and uv through trusted platform sources.
Use a writable build checkout and a dedicated unprivileged runtime account. Mount
persistent storage with ownership/permissions for that account, sufficient disk for
replay/checkpoints and a backup destination outside the data root. Check actual CPU,
RAM and disk budgets before long plans or several remote slots. Docker is unnecessary.

Inside LXC, normal CPU deployment uses the same commands. NVIDIA acceleration
additionally needs host devices and compatible driver libraries passed into the
container, plus service-user permission to access them. Verify `nvidia-smi` and the
chosen environment's `devices` output. A CUDA wheel does not configure host drivers
or LXC passthrough. Follow [separate CUDA setup](PERFORMANCE.md#separate-cuda-environment);
privileged container settings and hardware-specific passthrough require host-admin
review and are not validated by this CPU environment.

## systemd

Create a dedicated `hnefatafl` system user and install the checkout at
`/opt/hnefatafl`. Install dependencies and build the frontend there. Copy
[deployment/hnefatafl.service](../deployment/hnefatafl.service) to `/etc/systemd/system/`, then run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now hnefatafl
sudo systemctl status hnefatafl
sudo journalctl -u hnefatafl -n 100 --no-pager
```

The service stores data in `/var/lib/hnefatafl` and binds loopback. `StateDirectory`
creates the data directory; the checkout and built assets must be readable by the service user. Its filesystem
hardening permits writes only to the state directory; keep runtime outputs there
and build/install dependencies before starting the unit. Run one API worker: training process ownership and cancellation belong to that server process.
On host restart, files remain; interrupted training jobs need checkpoint resumption.
Stopped tournament workers retain their completed matches and show a terminal
status rather than being assumed to have continued. The systemd template is supplied for deployment;
systemd operation must be validated on the target host.

Remote training has an independent queue and lease lifecycle. Cloud workers only
need outbound HTTP(S) access and may run multiple independent training slots.
The workshop retains their reports and imported model copies. Follow
[REMOTE_TRAINING.md](REMOTE_TRAINING.md) for enrollment, GPU installation,
resumption and private-network/HTTPS access requirements. Keep administrative
routes private: worker bearer tokens protect only `/api/worker/` routes.

## Reverse proxy and access

Use [deployment/nginx.conf.example](../deployment/nginx.conf.example) as a proxy layout. Add your TLS certificate and
authentication before public exposure. This release supports trusted local profiles,
not authenticated online accounts: game and training management endpoints are
administrative capabilities. Put the entire application behind a trusted network or
an authenticated reverse proxy. Configure upload limits and rate/resource limits
appropriate to your machine. The nginx example is HTTP-only and has no configured
authentication; it is a layout, not a secure deployment by itself. Its 128 MiB body
limit can be smaller than a multipart request carrying a maximum-sized artifact,
so account for transport overhead while retaining server-side artifact limits.
Worker routing guidance is in [Remote training](REMOTE_TRAINING.md#disconnects-and-access).
API version checks protect concurrent writes; they do
not authenticate players. Model compatibility validation and tensor-only loading do
not replace resource limits for untrusted uploads.

## Updating

1. Read the target [release notes](README.md#rulesets-release-history-and-historical-requirements).
   Quiesce local jobs, tournaments and remote workers, then stop the service.
2. Make and verify a [consistent full backup](TRAINING_DATA.md#consistent-backup-and-restoration).
   Record the source code version, storage path and any separate CUDA/CLI environments.
3. Select the intended published release, run `make install` and `make build` in
   the checkout. Update remote-worker code to the same release. The CPU lock can
   replace ad-hoc GPU wheels; use the separate CUDA environment when appropriate.
4. Restart the service, inspect logs and `/api/health`, create a legal game move,
   load a model and inspect retained jobs/history. Test restoration/resumption on
   isolated data before relying on the backup.
5. If rollback is needed, stop writers and restore the matching prior code,
   dependencies, built frontend **and complete pre-upgrade data backup**. Do not
   point old code at a newer schema or overlay unrelated live model/job catalogs.

v0.16.0/v0.17.0 keep schema 7 and the checkpoint format; update workers for new
training parameters. This is compatibility guidance, not evidence of GPU or
service-manager execution on your machine.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Startup says browser build missing | Run `make build` in the checkout before `scripts/start.sh` |
| Health works but browser is stale | Rebuild frontend and confirm proxy/browser cache points at this checkout |
| Phone cannot connect | Default bind is loopback; use the trusted-LAN host setting and firewall rule |
| Data vanishes or wrong jobs appear | Confirm effective absolute data root and service working directory; `.env` is not auto-loaded |
| Permission denied under systemd | State directory ownership, readable checkout/venv and hardening write paths |
| GPU not detected | Correct server/worker Python, official GPU wheel, host driver and device permissions |
| Job interrupted by server restart | Local processes do not survive; resume retained full checkpoint into a new job |
| Restore lists models but jobs cannot resume | Verify full data root, jobs registry and saved absolute paths; see [migration](TRAINING_DATA.md#moving-data-between-installations) |
