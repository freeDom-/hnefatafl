# Installation and deployment

## Requirements

Linux (including Proxmox LXC), Python 3.11+, Node 22+ for building the browser client,
and `uv` for the locked CPU environment. Node is unnecessary while serving a built
client. Training can run on CPU; useful longer experiments benefit from NVIDIA CUDA.
See [TRAINING.md](TRAINING.md) for device installation and cost. Docker is optional.

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
Version 0.6 upgrades earlier databases to schema 3 automatically and additively:
schema 2 added player deletion timestamps and historical model metadata; schema 3
adds saved tournaments. Existing game JSON,
stable player IDs, move versions and model/training files are preserved. Startup can
repeat the migration safely. Deleted players stay hidden under the new application;
rolling back to old code does not understand those tombstones, so restore a consistent
pre-upgrade data backup when rolling back.
Before updates, stop the service and archive the entire data directory. SQLite WAL
files must not be copied independently while writes continue. A consistent backup
can also be made with SQLite's backup API. Test restoration on an isolated directory.
Migrating to PostgreSQL is a future storage-adapter task; no automatic migration is
claimed by this release.

## systemd

Create a dedicated `hnefatafl` system user and install the checkout at
`/opt/hnefatafl`. Install dependencies and build the frontend there. Copy
`deployment/hnefatafl.service` to `/etc/systemd/system/`, then run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now hnefatafl
sudo systemctl status hnefatafl
```

The service stores data in `/var/lib/hnefatafl` and binds loopback. Run one API
worker: training process ownership and cancellation belong to that server process.
On host restart, files remain; interrupted training jobs need checkpoint resumption.
Stopped tournament workers retain their completed matches and show a terminal
status rather than being assumed to have continued. The systemd template is supplied for deployment;
systemd operation must be validated on the target host.

## Reverse proxy and access

Use `deployment/nginx.conf.example` as a proxy layout. Add your TLS certificate and
authentication before public exposure. This release supports trusted local profiles,
not authenticated online accounts: game and training management endpoints are
administrative capabilities. Put the entire application behind a trusted network or
an authenticated reverse proxy. Configure upload limits and rate/resource limits
appropriate to your machine. API version checks protect concurrent writes; they do
not authenticate players. Model compatibility validation and tensor-only loading do
not replace resource limits for untrusted uploads.

## Updating

Stop the service, back up data, check out a published release, run the locked
installation/build commands and restart. Read that release's notes for incompatibilities
and migrations. Validate `/api/health`, a legal game move and model listing after
the update. Keep the previous checkout and data backup for rollback.
