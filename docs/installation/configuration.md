# Configuration

Hnefatafl reads configuration from environment variables at process startup.
`.env` files are **not** loaded automatically: export variables in the shell, a
systemd `EnvironmentFile`, or the container/service configuration. Production
secrets must be protected and never committed.

The canonical machine-readable list is [`.env.example`](../../.env.example);
this page explains the groups and defaults. If the two disagree, the source and
`.env.example` win.

## Core

| Variable | Default | Meaning |
| --- | --- | --- |
| `HNEFATAFL_DATA_DIR` | `./data` | Persistent data root: SQLite databases, checkpoints, model copies, studies, workers and released runtime assets |
| `HNEFATAFL_HOST` | `127.0.0.1` | Bind address for `scripts/start.sh` |
| `HNEFATAFL_PORT` | `8000` | Bind port |
| `HNEFATAFL_SESSION_SECONDS` | `43200` | Session lifetime (12 hours) |
| `HNEFATAFL_ENV` | unset | Set to `production` to enable production safety checks |
| `HNEFATAFL_PUBLIC_URL` | unset | Canonical HTTPS origin; required in production |
| `HNEFATAFL_ALLOWED_HOSTS` | unset | Comma-separated allowed host names; required in production |
| `HNEFATAFL_TRUSTED_PROXIES` | unset | Comma-separated reverse-proxy IPs/CIDRs allowed to supply forwarded headers |

Production also requires an initial local administrator created with
`python -m hnefatafl.accounts init-admin`. See
[installation](installation.md#release-prerequisites) and
[security](../administration/security.md).

## Registration and mail

Registration is off by default. Enabling it requires an account sender and a
reachable SMTP service.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HNEFATAFL_REGISTRATION_ENABLED` | `false` | Exposes public registration flows |
| `HNEFATAFL_ACCOUNT_SENDER` | unset | From-address for verification and reset mail |
| `HNEFATAFL_ACCOUNT_SMTP_HOST` | unset | SMTP host |
| `HNEFATAFL_ACCOUNT_SMTP_PORT` | unset | SMTP port |
| `HNEFATAFL_ACCOUNT_SMTP_TLS` | unset | `starttls` or the configured TLS mode |
| `HNEFATAFL_ACCOUNT_SMTP_USERNAME` / `_PASSWORD` | unset | Supply together through protected configuration, never command arguments |
| `HNEFATAFL_ACCOUNT_TIMEOUT` | `10` | SMTP operation timeout (seconds) |
| `HNEFATAFL_ACCOUNT_MAX_ATTEMPTS` | `4` | Durable delivery attempts |
| `HNEFATAFL_ACCOUNT_RETRY_SECONDS` | `60` | Retry delay |
| `HNEFATAFL_ACCOUNT_LEASE_SECONDS` | `60` | Mail-queue lease |
| `HNEFATAFL_ACCOUNT_POLL_SECONDS` | `5` | Mail-queue poll interval |
| `HNEFATAFL_ACCOUNT_QUEUE_LIMIT` | `1000` | Maximum queued messages |
| `HNEFATAFL_ACCOUNT_WINDOW_SECONDS` | `900` | Abuse-budget window |

Registration abuse budgets (`HNEFATAFL_ACCOUNT_REGISTER_IP`,
`_REGISTER_IDENTITY`, `_RESEND_IP`, `_RESEND_IDENTITY`, `_RESET_IP`,
`_RESET_IDENTITY`, `_CONFIRM_IP`, `_CONFIRM_IDENTITY`, `_GLOBAL_REQUESTS`,
`_GLOBAL_HASHES`, `_GLOBAL_MAIL`, `_LIMIT_KEYS`) default to the values shown in
`.env.example`. The account browser suite raises the registration-IP budget to 20
for its disposable fake mailbox; production defaults are unchanged. See
[user management](../administration/user-management.md).

## Online play and public discovery

| Variable | Default | Meaning |
| --- | --- | --- |
| `HNEFATAFL_ONLINE_ENABLED` | `false` | Allow creating and joining online games |
| `HNEFATAFL_ONLINE_TIMEOUT_POLICY_APPROVED` | `true` | Finalize automatic timeout losses; set `false` only to pause during an incident |
| `HNEFATAFL_ONLINE_SWEEP_SECONDS` | `1` | Deadline sweep interval |
| `HNEFATAFL_ONLINE_SWEEP_BATCH` | `50` | Deadline sweep batch size |
| `HNEFATAFL_ONLINE_WAITING_LIMIT` | `5` | Waiting games per account |
| `HNEFATAFL_ONLINE_CREATION_LIMIT` | `10` | Rolling creation budget |
| `HNEFATAFL_ONLINE_CREATION_WINDOW_SECONDS` | `900` | Rolling creation window |
| `HNEFATAFL_PUBLIC_ONLINE_ENABLED` | `true` (enabled) | Signed-out public discovery; set `false` to return 404 for `/api/public/online/*` |
| `HNEFATAFL_PUBLIC_RATE_LIMIT` | `60` | Public requests per window |
| `HNEFATAFL_PUBLIC_RATE_WINDOW_SECONDS` | `60` | Public rate-limit window |

Public discovery is enabled by default on the shipped configuration. Set
`HNEFATAFL_PUBLIC_ONLINE_ENABLED=false` to disable the whole public namespace; the
Watch Hall then returns not found. Authenticated online play is unaffected.

A two-game match counts as one creation. Cancelling a waiting game does not refund
the rolling creation budget. See
[online operations](../administration/online-operations.md) and
[public online](../game/interface/arena.md#public-watch-hall).

## Optional browser-local analysis

Analysis is a deployment decision, not a player preference. The compatibility
flag only enables runtime delivery; an explicit mode wins.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HNEFATAFL_ANALYSIS_MODE` | derived | `browser_only`, `browser_preferred`, `server_only` or `disabled` |
| `HNEFATAFL_BROWSER_ANALYSIS_ENABLED` | `0` | Legacy delivery flag; `1` keeps historical server analysis available and may deliver the browser runtime for diagnostics |
| `HNEFATAFL_ANALYSIS_WEBGPU` | `0` | Enable the evidence-gated WebGPU path where supported |
| `HNEFATAFL_ANALYSIS_OFFLINE_SECONDS` | `900` | Offline authorization window (60–3600) |
| `HNEFATAFL_ANALYSIS_MAX_CONCURRENT` | `16` | Server fallback concurrency |
| `HNEFATAFL_ANALYSIS_MAX_PER_USER` | `4` | Server fallback per-account concurrency |
| `HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE` | `240` | Server fallback request rate |

Browser analysis remains opt-in and is not a production acceptance claim. Runtime
assets and published models live in the persistent data root; see
[installation](installation.md#browser-local-analysis-distribution-optional) and
[the roadmap](../development/roadmap/browser-analysis.md).

## Training and hardware

Training parameters are **not** environment variables. They belong to a training
job configuration; see [parameters](../training/parameters.md). Hardware selection
(`cpu`, `cuda`, `mps`, OpenVINO) is also per job. The server environment only
determines what a job or worker can use.

## Local development

`scripts/dev.sh` uses the fixed API host/port `127.0.0.1:8000` and Vite on 5173.
`VITE_API_TARGET` overrides the Vite proxy destination. `scripts/start.sh` honors
`HNEFATAFL_HOST` and `HNEFATAFL_PORT`. Do not expose the development server to an
untrusted network.

## Verification

```sh
.venv/bin/python -m hnefatafl.cli devices
curl --fail http://127.0.0.1:8000/api/health
```

Invalid or contradictory production settings fail at startup rather than being
silently clamped. Check the service journal after changing configuration.
