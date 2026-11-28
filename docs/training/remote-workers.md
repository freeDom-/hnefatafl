# Remote training workers

[Training index](README.md) · [Interface](interface.md) · [Worker protocol](../development/worker-protocol.md) · [Troubleshooting](troubleshooting.md)

Remote workers run the same CLI/training loop on another machine. The server remains
authoritative for job state, leases and model registration. Workers make outbound
requests only; no inbound port is required.

## Prepare the worker

1. Use the same application version/source revision as the server.
2. Install locked dependencies in the worker environment:
   ```sh
   uv sync --frozen --extra train
   .venv/bin/python -m hnefatafl.cli devices
   ```
3. In the Workshop as administrator, open **Remote workers → Connect a worker**.
4. Choose a name and parallel slot count, then copy the one-time token and command.
5. Run the command on the worker machine. The token is read from
   `HNEFATAFL_WORKER_TOKEN`; it is shown once and never stored in the browser.

Example:

```sh
read -r -s HNEFATAFL_WORKER_TOKEN
export HNEFATAFL_WORKER_TOKEN
.venv/bin/python -m hnefatafl.cli worker --server https://tafl.example.org \
  --work-dir ./data/remote-worker --concurrency 2
```

The worker may run behind NAT/firewall with outbound HTTPS and a trusted server
certificate. Do not disable TLS verification. Use a private work directory.

## Slots, progress and resumption

- A worker advertises CPU/CUDA/MPS/OpenVINO capabilities and package/protocol
  versions.
- The server assigns only compatible work. `auto` can use CPU; an explicit GPU
  request requires a compatible worker.
- Each slot runs an independent job; jobs upload progress and full checkpoints.
- Progress on the server is only what the worker has uploaded. A disconnect does
  not make local unuploaded work resumable.
- After reconnect/lease expiry, explicitly resume from the uploaded checkpoint;
  do not reuse an expired lease.
- A worker's cleanup does not delete its private local work directory.

Exact version matching is the default. An administrator can persist a version
override for compatible software, but protocol/hardware/capability gates still
apply. Update both sides and restart the worker supervisor after upgrading.

## Compatibility and capabilities

Workers advertise parallel search, evaluation batching, training symmetry,
Schedule-Free, HyperController versions/iteration dwell and evaluation execution
modes. A job requiring an unavailable capability stays queued or is rejected.
Legacy resume transports omit only backend preferences that cannot be copied;
tensors, optimizer/replay/RNG and source files are preserved.

## Evaluation and logs

Workers use the job's configured evaluation opponent/mode/budget. Compact JSON-line
logs carry bounded progress; full history stays in checkpoints/progress files.
Queue waiting counts against wall deadlines. GPU capability data is not a strength
claim.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Job stays queued | Worker online/free slots, token, selected worker, device and capabilities |
| Unauthorized | Correct server URL/current token; revoked credentials need re-enrollment, not a TLS bypass |
| Lease expired | Inspect uploaded progress; resume a new job explicitly |
| Model not resumable | Full source checkpoint was not uploaded; an inference export cannot resume |
| Version/capability mismatch | Compare both package versions/installed source; restart supervisor; capability gates cannot be overridden |
| GPU unavailable/OOM | Check worker driver/device permissions; reduce slots/batches |
| Cleanup did not free disk | Server cleanup does not remove worker-local files |

See [worker protocol](../development/worker-protocol.md) for API, leases and
artifact validation.
