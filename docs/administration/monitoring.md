# Monitoring and service health

This page lists the supported operational checks. The application does not ship a
metrics backend, alerting stack or admin dashboard for host resources.

## Health and readiness

```sh
curl --fail http://127.0.0.1:8000/api/health
systemctl status hnefatafl
journalctl -u hnefatafl -n 100 --no-pager
```

`/api/health` is a readiness check for the HTTP service. It does not prove that
reverse-proxy TLS, DNS, mail delivery, worker hardware or a restored backup are
correct. The production installer validates the service plus its startup checks
under the deployment lock.

## Online games

- Waiting/active/completed games are owned by the database, not by a browser or
  worker process.
- Deadline finalization runs in the server lifespan. Inspect the timeout policy
  and the deadline sweep logs before pausing it during an incident.
- `HNEFATAFL_ONLINE_TIMEOUT_POLICY_APPROVED=false` pauses automatic timeout
  losses; it does not cancel existing games or stop ordinary moves.
- League settlement and player-tournament advancement run through durable
  outboxes/workers. A restart resumes unfinished work; do not delete rows to force
  a retry.
- Creation quotas, waiting limits and public rate limits are server-configurable.
  See [configuration](../installation/configuration.md).

Operational details and recovery rules: [online operations](online-operations.md).

## Training workers

Remote workers report status and leases to the server. An offline worker does not
invalidate uploaded models; a job may remain queued until a compatible worker has
capacity. Use the Workshop **Workers** view and the worker logs. Do not treat a
running job's absence from a browser tab as cancellation. See
[remote workers](../training/remote-workers.md).

## Storage

Monitor the persistent data root, not the release directory:

- SQLite databases and WAL files.
- Training jobs, checkpoints and model copies.
- Optimization studies and job archives.
- Published browser-analysis runtime and models, if enabled.
- Backups.

Release retention is bounded; persistent data is not. The installer keeps
completed release directories for rollback but old releases and backups must be
pruned deliberately. See
[storage retention](../installation/updating.md#storage-retention-preflight-and-cleanup).

## Backups

A backup is not verified until it has been restored into a disposable directory
and the database, accounts, records and model files have been checked. Stop
writers or use the supported backup command; copying a live SQLite database file
without its WAL is not a consistent backup. See
[backup and restoration](../training/models.md#backup-and-restoration).

## What is not monitored here

- GPU temperature, driver health or OpenVINO compilation.
- Host-level disk SMART data, network reachability or TLS expiry.
- Mail-provider deliverability and reputation.
- External uptime.

Use host and network monitoring for those concerns. The application reports its
own errors in the service journal and returns structured API errors; it does not
silently retry a failed deployment.
