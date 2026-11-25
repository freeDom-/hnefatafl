/* Isolated neural inference client.
 *
 * Lifecycle and ownership guarantees:
 *  - every request belongs to a worker generation; a superseded worker's
 *    messages and errors are ignored;
 *  - hard timeouts clear their own timer and only abort the still-active
 *    generation, so an obsolete timer can never kill a replacement worker;
 *  - terminate()/dispose() reject the active promise with an AbortError instead
 *    of abandoning it, and cancel any in-flight download;
 *  - a single logical load owns the download, bounded body read, digest
 *    verification, worker initialization, session creation and model
 *    publication. Cancellation at any point prevents a stale load from
 *    publishing a replacement session;
 *  - a failed replacement never leaves the old model advertised as active;
 *  - the worker's loaded runtime/provider is tracked independently of model
 *    state, so a failed WASM load does not block a later WebGPU request from
 *    recreating the correct worker.
 *
 * Error kinds are distinguishable by `name`:
 *  - AbortError      user/model cancellation or disposal
 *  - TimeoutError    hard job deadline
 *  - ModelLoadError  model download, size or integrity failure
 *  - ProviderError   unsupported/unavailable execution provider
 *  - WorkerError     worker crash or transport failure
 */
const DOWNLOAD_TIMEOUT_MS = 60000;
const LOAD_TIMEOUT_MS = 120000;
const INFER_TIMEOUT_MS = 30000;
const BENCHMARK_TIMEOUT_MS = 180000;
const MAX_MODEL_BYTES = 64 * 1024 * 1024;

function named(name, message) {
  const error = new Error(message);
  error.name = name;
  error.code = name;
  return error;
}

async function sha256Hex(buffer) {
  if (!globalThis.crypto?.subtle)
    throw named("ModelLoadError", "crypto.subtle is unavailable for model verification");
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function readBodyBounded(response, maxBytes) {
  // Content-Length is advisory. The bounded reader is authoritative so a
  // response without a length cannot buffer an arbitrarily large body.
  const declared = Number(response.headers?.get?.("content-length") ?? 0);
  if (Number.isFinite(declared) && declared > maxBytes)
    throw named("ModelLoadError", "Model exceeds the size limit");
  const reader = response.body?.getReader?.();
  if (!reader) {
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > maxBytes)
      throw named("ModelLoadError", "Model exceeds the size limit");
    return buffer;
  }
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > maxBytes) {
      try {
        await response.body?.cancel?.();
      } catch {
        // best effort; the size violation is the actionable error
      }
      throw named("ModelLoadError", "Model exceeds the size limit");
    }
    chunks.push(value);
  }
  const combined = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return combined.buffer;
}

function validateBatchLimits(maxBatch, maxQueue) {
  if (!Number.isInteger(maxBatch) || maxBatch < 1 || maxBatch > 32)
    throw named("INPUT_INVALID", "maxBatch must be an integer in 1..32");
  if (!Number.isInteger(maxQueue) || maxQueue < 1 || maxQueue > 256)
    throw named("INPUT_INVALID", "maxQueue must be an integer in 1..256");
}

function settleBatchEntry(entry, value, error) {
  if (entry.settled) return false;
  entry.settled = true;
  if (error) entry.reject(error);
  else entry.resolve(value);
  return true;
}

export class NeuralClient {
  constructor({
    testHooks = false,
    workerUrl = null,
    batching = false,
    maxBatch = 4,
    maxQueue = 16,
    batchDelayMs = 5,
  } = {}) {
    this.worker = null;
    this.workerProvider = null;
    this.directChannel = null;
    this.pending = null;
    this.busy = false;
    this.sequence = 0;
    this.generation = 0;
    this.model = null;
    this.download = null;
    this.loading = null;
    this.sequenceLoad = 0;
    // Set while a cancelled inference is being drained; a replacement inference
    // must not be admitted against a worker that may still be executing it.
    this.draining = null;
    // Explicitly injected test adapter (browser lifecycle tests only). Normal
    // clients do not enable the worker's delay hook, and inference requests never
    // carry an arbitrary delay field.
    this.testHooks = testHooks === true;
    // A dedicated test worker entry point is required for delayed inference.
    // Normal deployments never stage that file.
    this.workerUrl = workerUrl;
    this.testInferenceDelayMs = null;
    this.testHooksConfigured = false;
    this.testHooksGeneration = null;
    // Injectable digest seam so cancellation during verification can be tested
    // deterministically. Production always uses crypto.subtle SHA-256.
    this.digestFn = sha256Hex;
    // Opt-in bounded batching for independent search contexts sharing this
    // client. Normal foreground analysis keeps the single-request path.
    validateBatchLimits(maxBatch, maxQueue);
    if (!Number.isInteger(batchDelayMs) || batchDelayMs < 0 || batchDelayMs > 1000)
      throw named("INPUT_INVALID", "batchDelayMs must be an integer in 0..1000");
    this.batching = batching === true;
    this.maxBatch = maxBatch;
    this.maxQueue = maxQueue;
    this.batchQueue = [];
    this.batchDraining = null;
    this.batchIds = new Set();
    this.batchEntries = new Map();
    this.batchInFlight = null;
    this.batchIgnored = new Set();
    this.batchDelayMs = batchDelayMs;
    this.batchTimer = null;
    this.batchStats = {
      requests: 0,
      batches: 0,
      multi_request_batches: 0,
      observed_max_batch: 0,
      last_batch_sizes: [],
      rejected: 0,
    };
  }

  enableBatching({ maxBatch = 4, maxQueue = 16 } = {}) {
    validateBatchLimits(maxBatch, maxQueue);
    this.batching = true;
    this.maxBatch = maxBatch;
    this.maxQueue = maxQueue;
    return this.batchStatistics();
  }

  batchStatistics() {
    return {
      enabled: this.batching,
      max_batch: this.maxBatch,
      max_queue: this.maxQueue,
      queued: this.batchQueue.length,
      in_flight_batch: this.batchIds.size,
      ...this.batchStats,
      last_batch_sizes: [...this.batchStats.last_batch_sizes],
    };
  }

  _queueInference(payload) {
    if (this.batchQueue.length >= this.maxQueue) {
      return Promise.reject(named("QUEUE_FULL", "Batched inference queue is full"));
    }
    return new Promise((resolve, reject) => {
      this.batchQueue.push({ payload, resolve, reject, settled: false });
      this.batchStats.requests += 1;
      if (!this.batchDraining && this.batchTimer === null) {
        this.batchTimer = setTimeout(() => {
          this.batchTimer = null;
          void this._drainBatch();
        }, this.batchDelayMs);
      }
    });
  }

  async _drainBatch() {
    if (this.batchDraining) return this.batchDraining;
    this.batchDraining = (async () => {
      while (this.batchQueue.length) {
        const batch = this.batchQueue.splice(0, this.maxBatch);
        const payloads = batch.map((entry) => entry.payload);
        const model = payloads[0].model;
        const compatible = payloads.every(
          (payload) =>
            payload.model.sha256 === model.sha256 &&
            payload.model.ruleset === model.ruleset &&
            payload.model.size === model.size,
        );
        if (!compatible) {
          const error = named("MODEL_IDENTITY_MISMATCH", "Batch contains incompatible models");
          for (const entry of batch) settleBatchEntry(entry, null, error);
          continue;
        }
        const cases = payloads.map((payload) => ({
          ...(payload.planes_base64 !== undefined
            ? { planes_base64: payload.planes_base64 }
            : { planes: payload.planes }),
          legal: payload.legal,
          request_id: payload.request_id,
          position_digest: payload.position_digest,
        }));
        const batchIds = new Set(payloads.map((payload) => payload.request_id));
        this.batchIds = batchIds;
        for (const entry of batch) this.batchEntries.set(entry.payload.request_id, entry);
        this.batchStats.batches += 1;
        if (batch.length > 1) this.batchStats.multi_request_batches += 1;
        this.batchStats.observed_max_batch = Math.max(
          this.batchStats.observed_max_batch,
          batch.length,
        );
        this.batchStats.last_batch_sizes.push(batch.length);
        if (this.batchStats.last_batch_sizes.length > 32)
          this.batchStats.last_batch_sizes.shift();
        let response = null;
        let batchFailure = null;
        try {
          this.batchInFlight = this.request(
            { action: "inferManyCompact", cases, model },
            { timeoutMs: INFER_TIMEOUT_MS },
          );
          response = await this.batchInFlight;
        } catch (error) {
          batchFailure = error;
        } finally {
          this.batchInFlight = null;
        }
        if (batchFailure) {
          for (const entry of batch) {
            const ignored = this.batchIgnored.delete(entry.payload.request_id);
            settleBatchEntry(
              entry,
              null,
              ignored
                ? named("AbortError", "Batched inference was cancelled")
                : batchFailure,
            );
            if (ignored && !entry.rejectedAccounted) {
              entry.rejectedAccounted = true;
              this.batchStats.rejected += 1;
            }
          }
          for (const entry of batch) this.batchEntries.delete(entry.payload.request_id);
          this.batchIds = new Set();
          continue;
        }
        let byId = new Map();
        try {
          if (!response || !Array.isArray(response.results))
            throw named("RESPONSE_MISMATCH", "Batched inference returned no results");
          byId = new Map(response.results.map((row) => [row?.request_id, row]));
        } catch (error) {
          batchFailure = error;
        }
        for (const entry of batch) {
          const requestId = entry.payload.request_id;
          if (this.batchIgnored.delete(requestId)) {
            if (!entry.rejectedAccounted) {
              entry.rejectedAccounted = true;
              this.batchStats.rejected += 1;
            }
            settleBatchEntry(
              entry,
              null,
              named("AbortError", "Batched inference was cancelled"),
            );
            continue;
          }
          const row = byId.get(requestId);
          if (batchFailure || !row) {
            settleBatchEntry(
              entry,
              null,
              batchFailure ?? named("RESPONSE_MISMATCH", "Batched response omitted a request"),
            );
            continue;
          }
          settleBatchEntry(entry, {
            provider_requested: response.provider_requested,
            execution_placement: response.execution_placement,
            gpu_execution_verified: response.gpu_execution_verified === true,
            model_sha256: response.model_sha256,
            model_ruleset: response.model_ruleset,
            model_bytes: response.model_bytes,
            index: row.index,
            request_id: row.request_id,
            position_digest: row.position_digest,
            value: row.value,
            legal: row.legal,
            legal_probabilities: row.legal_probabilities,
            tensor_prepare_ms: response.tensor_prepare_ms,
            run_ms: response.run_ms,
            extract_ms: response.extract_ms,
            inference_ms: response.run_ms + response.tensor_prepare_ms + response.extract_ms,
            batch_size: response.batch,
            payload_mode: "legal-only",
            cache_hit: false,
          });
        }
        for (const entry of batch) this.batchEntries.delete(entry.payload.request_id);
        this.batchIds = new Set();
      }
    })().finally(() => {
      this.batchDraining = null;
      if (this.batchQueue.length && this.batchTimer === null) {
        this.batchTimer = setTimeout(() => {
          this.batchTimer = null;
          void this._drainBatch();
        }, this.batchDelayMs);
      }
    });
    return this.batchDraining;
  }

  _spawn() {
    if (typeof Worker === "undefined") throw new Error("Web Workers are unsupported");
    const worker = new Worker(this.workerUrl ?? new URL("./neural-worker.js", import.meta.url));
    const generation = this.generation;
    this.worker = worker;
    worker.onmessage = ({ data }) => {
      if (this.worker !== worker || this.generation !== generation) return;
      const pending = this.pending;
      if (!pending || pending.id !== data.id) {
        // A cancelled request may still be executing; its completion frees the
        // worker for the next request but its result is ignored.
        if (this.abandoned && this.abandoned.id === data.id) {
          const abandoned = this.abandoned;
          this.abandoned = null;
          abandoned.abandonedOutcome = data.error
            ? { failed: true, code: data.code ?? null, message: data.error }
            : { failed: false, code: null, message: null };
          if (
            data.error &&
            (data.code === "RESOURCE_CLEANUP_FAILED" ||
              data.code === "SESSION_RELEASE_FAILED")
          ) {
            // The worker may be unhealthy; never retain a session whose cleanup
            // failed. abort() also resolves the drain wait.
            const error = new Error(data.error);
            error.code = data.code;
            abandoned.finished?.();
            this.abort(error);
            return;
          }
          abandoned.finished?.();
        }
        return;
      }
      clearTimeout(pending.timer);
      this.pending = null;
      this.busy = false;
      pending.finished?.();
      if (data.error) {
        const error = new Error(data.error);
        if (data.code) error.code = data.code;
        pending.reject(error);
        if (data.code === "RESOURCE_CLEANUP_FAILED" || data.code === "SESSION_RELEASE_FAILED") {
          // The worker may be unhealthy; reset it so it is never reused silently.
          this.abort(error);
        }
      } else {
        pending.resolve(data.value);
      }
    };
    worker.onerror = (event) => {
      if (this.worker !== worker) return;
      this.abort(named("WorkerError", event.message || "Neural worker failed"));
    };
    return this;
  }

  // Reject active work and release the worker. Idempotent.
  abort(error) {
    this.generation += 1;
    try {
      this.worker?.terminate();
    } catch {
      // terminating an already-dead worker is harmless
    }
    this.worker = null;
    this.workerProvider = null;
    const direct = this.directChannel;
    this.directChannel = null;
    direct?.reject?.(error);
    this.busy = false;
    this.model = null;
    this.loading = null;
    const pending = this.pending;
    this.pending = null;
    if (pending) {
      clearTimeout(pending.timer);
      pending.finished?.();
      pending.reject(error);
    }
    clearTimeout(this.batchTimer);
    this.batchTimer = null;
    for (const entry of this.batchQueue.splice(0)) settleBatchEntry(entry, null, error);
    for (const entry of this.batchEntries.values()) settleBatchEntry(entry, null, error);
    this.batchEntries.clear();
    this.batchIds = new Set();
    this.batchIgnored.clear();
    this.batchInFlight = null;
    if (this.abandoned) {
      const abandoned = this.abandoned;
      abandoned.abandonedOutcome = {
        failed: true,
        code: error?.code ?? "WorkerError",
        message: error?.message ?? String(error),
      };
      this.abandoned = null;
      abandoned.finished?.();
    }
    this._cancelDownload(error);
  }

  _cancelDownload(error) {
    const download = this.download;
    this.download = null;
    if (download) {
      clearTimeout(download.timer);
      try {
        download.controller.abort();
      } catch {
        // already aborted
      }
      download.reject(error);
    }
  }

  terminate(reason = "Neural inference terminated") {
    this.abort(named("AbortError", reason));
  }

  // Explicit teardown for page lifecycle; releases worker and session resources.
  dispose() {
    this.draining = null;
    this.abandoned = null;
    this.terminate("Neural inference disposed");
  }

  async request(request, { timeoutMs = INFER_TIMEOUT_MS } = {}) {
    if (this.draining)
      throw named("WorkerError", "A cancelled neural request is still draining");
    if (!this.worker) this._spawn();
    if (this.busy) throw named("WorkerError", "A neural job is already running");
    const worker = this.worker;
    const generation = this.generation;
    this.busy = true;
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        // Only the generation that scheduled this timer may be aborted.
        if (this.generation !== generation || this.worker !== worker) return;
        this.abort(named("TimeoutError", "Neural job exceeded its hard deadline"));
      }, timeoutMs);
      const pending = { id, resolve, reject, timer };
      pending.finished = () => {};
      this.pending = pending;
      try {
        worker.postMessage({ id, request });
      } catch (error) {
        this.abort(named("WorkerError", `Neural transport failure: ${error?.message ?? error}`));
      }
    });
  }

  capabilities() {
    return this.request({ action: "capabilities" }, { timeoutMs: 15000 });
  }

  attachDirectPort(port, { channelId, timeoutMs = 5000 } = {}) {
    if (!this.worker) throw named("WorkerError", "Neural worker is not loaded");
    if (typeof channelId !== "string" || !channelId)
      throw named("INPUT_INVALID", "A direct channel identity is required");
    if (this.directChannel) throw named("DIRECT_BUSY", "A direct channel already owns the session");
    const worker = this.worker;
    const generation = this.generation;
    const id = ++this.sequence;
    // Reserve ownership synchronously before transferring the port. A concurrent
    // load/provider switch must not terminate the worker during this window.
    const reservation = {
      channelId,
      worker,
      generation,
      state: "pending",
      reject: null,
    };
    this.directChannel = reservation;
    return new Promise((resolve, reject) => {
      let settled = false;
      let timer = null;
      const cleanup = () => {
        if (timer !== null) clearTimeout(timer);
        timer = null;
        worker.removeEventListener?.("message", onMessage);
      };
      const fail = (error) => {
        if (settled) return;
        settled = true;
        cleanup();
        if (this.directChannel === reservation) reservation.state = "detaching";
        // A timed-out/rejected attachment may still have reached the worker.
        // Keep ownership reserved while cleanup runs; loads/provider switches
        // must not touch a session with an uncertain MessagePort.
        void this.detachDirectPort({ channelId, timeoutMs })
          .then((value) => {
            if (
              value?.pending === true &&
              this.directChannel === reservation &&
              this.worker === worker &&
              this.generation === generation
            )
              this.abort(named("TimeoutError", "Direct attachment cleanup did not settle"));
          })
          .catch(() => {
            if (this.directChannel === reservation) {
              reservation.state = "released";
              this.directChannel = null;
              this.abort(named("WorkerError", "Direct attachment cleanup failed"));
            }
          });
        reject(error);
      };
      reservation.reject = fail;
      const onMessage = ({ data }) => {
        if (data?.id !== id) return;
        if (settled) return;
        if (this.worker !== worker || this.generation !== generation) {
          fail(named("AbortError", "Direct attachment generation changed"));
          return;
        }
        if (data.value?.kind === "direct_port_attached") {
          if (this.directChannel !== reservation) {
            fail(named("AbortError", "Direct attachment reservation was superseded"));
            return;
          }
          settled = true;
          reservation.state = "attached";
          reservation.reject = null;
          cleanup();
          resolve(data.value);
          return;
        }
        const error = named("WorkerError", data.error ?? "Neural direct port attachment failed");
        error.code = data.code ?? "WorkerError";
        fail(error);
      };
      timer = setTimeout(
        () => fail(named("TimeoutError", "Neural direct port attachment timed out")),
        timeoutMs,
      );
      worker.addEventListener?.("message", onMessage);
      try {
        worker.postMessage({ control: "attachDirectPort", id, channelId, port }, [port]);
      } catch (error) {
        fail(named("WorkerError", `Direct port transfer failed: ${error?.message ?? error}`));
      }
    });
  }

  detachDirectPort({ channelId, timeoutMs = 5000 } = {}) {
    const reservation = this.directChannel;
    if (!this.worker) {
      if (reservation?.channelId === channelId) {
        reservation.state = "released";
        this.directChannel = null;
      }
      return Promise.resolve({ kind: "direct_port_detached", pending: false });
    }
    const worker = this.worker;
    const generation = this.generation;
    if (reservation?.channelId === channelId) reservation.state = "detaching";
    const id = ++this.sequence;
    return new Promise((resolve) => {
      let settled = false;
      let timer = null;
      const ownsReservation = () =>
        this.directChannel === reservation &&
        reservation?.channelId === channelId &&
        reservation.worker === worker &&
        reservation.generation === generation;

      const finish = (value, { timedOut = false, failed = false } = {}) => {
        if (settled) return;
        settled = true;
        if (timer !== null) clearTimeout(timer);
        timer = null;
        worker.removeEventListener?.("message", onMessage);
        const current = ownsReservation();
        if (current && (timedOut || failed)) {
          reservation.state = "released";
          this.directChannel = null;
          this.abort(
            named(
              timedOut ? "TimeoutError" : "WorkerError",
              timedOut ? "Direct port detach timed out" : value.error ?? "Direct detach failed",
            ),
          );
        } else if (current && value.pending !== true) {
          reservation.state = "released";
          this.directChannel = null;
        }
        resolve(value);
      };

      const malformed = (message) => {
        const value = {
          kind: "direct_port_detached",
          pending: false,
          stale: false,
          failed: true,
          error: message,
          code: "DETACH_INVALID",
        };
        finish(value, { failed: true });
      };

      const onMessage = ({ data }) => {
        if (data?.id !== id) return;
        if (data.error !== undefined && data.error !== null) {
          finish(
            {
              kind: "direct_port_detached",
              pending: false,
              stale: false,
              failed: true,
              error: String(data.error),
              code: data.code ?? "DETACH_FAILED",
            },
            { failed: true },
          );
          return;
        }
        const value = data.value;
        if (!value || typeof value !== "object" || Array.isArray(value)) {
          malformed("Detach response is missing");
          return;
        }
        if (value.kind !== "direct_port_detached") {
          malformed("Detach response has an unexpected kind");
          return;
        }
        if (typeof value.pending !== "boolean") {
          malformed("Detach response pending flag is missing or malformed");
          return;
        }
        if (value.stale !== undefined && typeof value.stale !== "boolean") {
          malformed("Detach response stale flag is malformed");
          return;
        }
        if (value.stale === true && value.pending === true) {
          malformed("Detach response is contradictory");
          return;
        }
        if (
          value.failed === true ||
          value.timedOut === true ||
          value.error !== undefined ||
          value.code !== undefined
        ) {
          finish(
            {
              kind: "direct_port_detached",
              pending: false,
              stale: false,
              failed: true,
              error: value.error ?? "Direct detach reported failure",
              code: value.code ?? "DETACH_FAILED",
            },
            { failed: true },
          );
          return;
        }
        finish(value);
      };
      timer = setTimeout(() => {
        finish(
          {
            kind: "direct_port_detached",
            pending: true,
            timedOut: true,
            failed: true,
            error: "Direct port detach timed out",
            code: "DETACH_TIMEOUT",
          },
          { timedOut: true },
        );
      }, timeoutMs);
      worker.addEventListener?.("message", onMessage);
      try {
        worker.postMessage({ control: "detachDirectPort", id, channelId });
      } catch (error) {
        finish(
          {
            kind: "direct_port_detached",
            pending: true,
            timedOut: true,
            failed: true,
            error: String(error?.message ?? error),
            code: "DETACH_TIMEOUT",
          },
          { timedOut: true },
        );
      }
    });
  }

  configureInferenceCache({ enabled, maxEntries = 256, maxBytes = 4 * 1024 * 1024 } = {}) {
    return this.request(
      {
        action: "configureInferenceCache",
        enabled: enabled === true,
        max_entries: maxEntries,
        max_bytes: maxBytes,
      },
      { timeoutMs: 5000 },
    );
  }

  inferenceCacheStats() {
    return this.request({ action: "inferenceCacheStats" }, { timeoutMs: 5000 });
  }

  directStats() {
    return this.request({ action: "directStats" }, { timeoutMs: 5000 });
  }

  clearInferenceCache() {
    return this.request({ action: "clearInferenceCache" }, { timeoutMs: 5000 });
  }

  // Cancel one in-flight inference without losing a healthy session when it can
  // drain within ``drainMs``. The rejected request settles immediately for the
  // caller; only the worker drain is awaited. If the worker does not drain, it
  // is terminated and the session must be reloaded.
  async cancelRequest({ reason = "Neural request cancelled", drainMs = 1500, requestId = null } = {}) {
    if (this.batching && requestId) {
      const index = this.batchQueue.findIndex(
        (entry) => entry.payload.request_id === requestId,
      );
      if (index >= 0) {
        const [entry] = this.batchQueue.splice(index, 1);
        if (settleBatchEntry(entry, null, named("AbortError", reason))) {
          entry.rejectedAccounted = true;
          this.batchStats.rejected += 1;
        }
        return {
          state: "drained",
          sessionRetained: this.model !== null,
          workerRetained: this.worker !== null,
          outcome: "aborted",
        };
      }
      if (this.batchIds.has(requestId)) {
        const entry = this.batchEntries.get(requestId);
        this.batchIgnored.add(requestId);
        if (entry && settleBatchEntry(entry, null, named("AbortError", reason))) {
          entry.rejectedAccounted = true;
          this.batchStats.rejected += 1;
        }
        const worker = this.worker;
        const generation = this.generation;
        // Do not report drained until the worker batch that may still be
        // executing has settled. If it does not settle within drainMs, fail
        // closed by terminating the worker/session rather than leaving other
        // contexts suspended on an unhealthy batch.
        const completion = (this.batchInFlight ?? Promise.resolve()).then(
          () => ({ timedOut: false, failed: false }),
          (error) => ({ timedOut: false, failed: true, error }),
        );
        let timer = null;
        const timeout = new Promise((resolve) => {
          timer = setTimeout(() => resolve({ timedOut: true, failed: false }), drainMs);
        });
        return Promise.race([completion, timeout]).then((result) => {
          clearTimeout(timer);
          if (result.timedOut) {
            this.abort(named("TimeoutError", "Batched inference did not drain in time"));
            return {
              state: "terminated",
              sessionRetained: false,
              workerRetained: false,
              outcome: "timeout",
              errorCode: "DRAIN_TIMEOUT",
            };
          }
          return {
            state:
              this.worker === worker && this.generation === generation
                ? "drained"
                : "terminated",
            sessionRetained: this.model !== null,
            workerRetained: this.worker === worker,
            outcome: result.failed ? "failed" : "aborted",
            errorCode: result.failed ? (result.error?.code ?? null) : null,
          };
        });
      }
    }
    if (this.draining) return this.draining;
    const pending = this.pending;
    if (!pending) {
      return {
        state: "idle",
        sessionRetained: this.model !== null,
        workerRetained: this.worker !== null,
      };
    }
    const worker = this.worker;
    const generation = this.generation;
    const finished = new Promise((resolve) => {
      pending.finished = resolve;
    });
    clearTimeout(pending.timer);
    this.pending = null;
    this.busy = false;
    this.abandoned = pending;
    pending.reject(named("AbortError", reason));
    // `finished` resolves only when the worker actually returns or is replaced;
    // it must not resolve just because the caller stopped waiting.
    this.draining = (async () => {
      let timer = null;
      const drained = await Promise.race([
        finished.then(() => true),
        new Promise((resolve) => {
          timer = setTimeout(() => resolve(false), drainMs);
        }),
      ]);
      clearTimeout(timer);
      const abandoned = pending;
      this.abandoned = null;
      const outcome = abandoned.abandonedOutcome ?? null;
      if (!drained) {
        this.abort(named("TimeoutError", "The neural worker did not drain the cancelled request"));
        return {
          state: "terminated",
          sessionRetained: false,
          workerRetained: false,
          outcome: "timeout",
          errorCode: "DRAIN_TIMEOUT",
        };
      }
      if (this.worker !== worker || this.generation !== generation)
        return {
          state: "terminated",
          sessionRetained: false,
          workerRetained: false,
          outcome: outcome?.failed ? "failed" : "aborted",
          errorCode: outcome?.code ?? null,
        };
      return {
        state: "drained",
        sessionRetained: this.model !== null,
        workerRetained: true,
        outcome: outcome?.failed ? "failed" : "completed",
        errorCode: outcome?.code ?? null,
      };
    })().finally(() => {
      this.draining = null;
    });
    return this.draining;
  }

  // UI-level cancellation: abort an owned model download/initialization or a
  // single inference. A healthy session is retained when only inference drains.
  async cancelWork({ reason = "Cancelled by user", drainMs = 1500 } = {}) {
    if (this.loading !== null || this.download) {
      // A model operation is owned by the caller; terminate so it cannot publish
      // a session after cancellation.
      this.terminate(reason);
      return { state: "load-aborted", sessionRetained: false, workerRetained: false };
    }
    return this.cancelRequest({ reason, drainMs });
  }

  // Bounded, cancellable model download that verifies the staged digest. The
  // download owns admission until digest verification has completed.
  async fetchModel(url, { expectedSha256 } = {}) {
    if (this.download) throw named("ModelLoadError", "A model download is already in progress");
    const controller = new AbortController();
    const startGeneration = this.generation;
    const download = { controller, reject: null, timer: null };
    const promise = new Promise((resolve, reject) => {
      download.reject = reject;
      download.timer = setTimeout(() => {
        controller.abort();
        reject(named("TimeoutError", "Model download exceeded its deadline"));
      }, DOWNLOAD_TIMEOUT_MS);
      (async () => {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw named("ModelLoadError", `Cannot load model: ${response.status}`);
        const buffer = await readBodyBounded(response, MAX_MODEL_BYTES);
        if (controller.signal.aborted || this.generation !== startGeneration)
          throw named("AbortError", "Model download cancelled");
        if (expectedSha256) {
          const digest = await this.digestFn(buffer);
          if (controller.signal.aborted || this.generation !== startGeneration)
            throw named("AbortError", "Model download cancelled during verification");
          if (digest !== expectedSha256)
            throw named("ModelLoadError", "Downloaded model failed its integrity check");
        }
        resolve({ buffer, sha256: expectedSha256 ?? null });
      })().catch((error) => {
        reject(
          error?.name === "AbortError"
            ? named("AbortError", "Model download cancelled")
            : error?.name === "ModelLoadError" || error?.name === "TimeoutError"
              ? error
              : named("ModelLoadError", `Model download failed: ${error?.message ?? error}`),
        );
      });
    });
    this.download = download;
    try {
      return await promise;
    } finally {
      clearTimeout(download.timer);
      if (this.download === download) this.download = null;
    }
  }

  _loadAdmission(provider) {
    if (!["wasm", "webgpu"].includes(provider))
      throw named("ProviderError", `Unsupported provider: ${provider}`);
    if (this.loading) throw named("ModelLoadError", "A model load is already in progress");
    if (
      this.directChannel &&
      this.directChannel.worker === this.worker &&
      this.directChannel.generation === this.generation
    )
      throw named("DIRECT_BUSY", "A direct search owns the inference session");
    if (this.busy || this.batchInFlight || this.batchDraining || this.draining)
      throw named("SESSION_BUSY", "A neural session operation is already running");
    // A worker that loaded a different ORT bundle cannot serve this provider,
    // even when the previous load failed before publishing a model.
    if (this.directChannel)
      throw named("DIRECT_BUSY", "A direct search owns the inference session");
    if (this.worker && this.workerProvider !== provider)
      this.abort(named("AbortError", "Recreating worker for provider switch"));
  }

  /**
   * Shared session publication for URL-fetched and application-verified bytes.
   * Exactly one worker initialization path exists; callers differ only in how
   * they obtained and verified the model buffer.
   */
  async _initializeSession(buffer, {
    modelUrl,
    provider,
    size,
    ruleset,
    expectedSha256,
    token,
    generation,
  }) {
    if (this.generation !== generation || this.loading !== token)
      throw named("AbortError", "Model load superseded");
    if (!this.worker) this._spawn();
    this.workerProvider = provider;
    const previousModel = this.model;
    const worker = this.worker;
    // From here the worker may release the previous session, so the interface
    // must not keep advertising it as active.
    this.model = null;
    let result;
    try {
      result = await this.request(
        { action: "load", buffer, provider, size, ruleset, expectedSha256 },
        { timeoutMs: LOAD_TIMEOUT_MS },
      );
    } catch (error) {
      // A rejected admission did not touch the session: preserve the previous
      // advertised identity only when the same worker generation is intact.
      if (
        previousModel &&
        (error?.code === "DIRECT_BUSY" || error?.code === "SESSION_BUSY") &&
        this.worker === worker &&
        this.generation === generation
      )
        this.model = previousModel;
      throw error;
    }
    if (this.generation !== generation || this.loading !== token)
      throw named("AbortError", "Model load superseded during session creation");
    this.model = { url: modelUrl, provider, size, ruleset, ...result };
    return this.model;
  }

  async load(modelUrl, { provider = "wasm", size, ruleset, expectedSha256 } = {}) {
    this._loadAdmission(provider);
    const token = ++this.sequenceLoad;
    this.loading = token;
    try {
      this.loading = token;
      const generation = this.generation;
      const { buffer } = await this.fetchModel(modelUrl, { expectedSha256 });
      return await this._initializeSession(buffer, {
        modelUrl,
        provider,
        size,
        ruleset,
        expectedSha256,
        token,
        generation,
      });
    } finally {
      if (this.loading === token) this.loading = null;
    }
  }

  /**
   * Load application-verified model bytes through the same session path.
   *
   * The buffer is validated again (size, digest, identity) and passed by
   * structured clone, so the caller's persistent/cache buffer is never detached.
   */
  async loadModelBytes(
    buffer,
    { provider = "wasm", size, ruleset, expectedSha256 } = {},
  ) {
    const bytes =
      buffer instanceof ArrayBuffer
        ? buffer
        : ArrayBuffer.isView(buffer)
          ? buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength)
          : null;
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength === 0)
      throw named("ModelLoadError", "Verified model bytes are required");
    if (bytes.byteLength > MAX_MODEL_BYTES)
      throw named("ModelLoadError", "Model bytes exceed the experimental size limit");
    this._loadAdmission(provider);
    const token = ++this.sequenceLoad;
    this.loading = token;
    try {
      this.loading = token;
      const generation = this.generation;
      const digest = await this.digestFn(bytes);
      if (this.generation !== generation || this.loading !== token)
        throw named("AbortError", "Model load superseded during verification");
      if (expectedSha256 && digest !== expectedSha256)
        throw named("ModelLoadError", "Verified model bytes failed their integrity check");
      return await this._initializeSession(bytes, {
        modelUrl: "verified-bytes",
        provider,
        size,
        ruleset,
        expectedSha256,
        token,
        generation,
      });
    } finally {
      if (this.loading === token) this.loading = null;
    }
  }

  // Capture the immutable identity of the currently loaded model. Callers must
  // capture this when preparing a workload, not when submitting it.
  modelIdentity() {
    if (!this.model) throw named("MODEL_NOT_LOADED", "No model session is loaded");
    return {
      sha256: this.model.model_sha256,
      ruleset: this.model.model_ruleset,
      size: this.model.size,
    };
  }

  _assertModel(expected) {
    if (!expected || typeof expected !== "object")
      throw named("MODEL_IDENTITY_REQUIRED", "An expected model identity is required");
    const active = this.modelIdentity();
    if (expected.sha256 !== active.sha256)
      throw named(
        "MODEL_IDENTITY_MISMATCH",
        `Requested model ${expected.sha256} does not match active model ${active.sha256}`,
      );
    if (expected.ruleset !== active.ruleset)
      throw named("RULESET_MISMATCH", "Requested ruleset does not match the active model");
    if (expected.size !== active.size)
      throw named("BOARD_SIZE_MISMATCH", "Requested board size does not match the active model");
  }

  setTestInferenceDelay(milliseconds) {
    if (!this.testHooks)
      throw named("TEST_HOOKS_DISABLED", "Inference delay is unavailable outside lifecycle tests");
    if (milliseconds !== null) {
      if (!Number.isInteger(milliseconds) || milliseconds < 0 || milliseconds > 5000)
        throw named("INPUT_INVALID", "test inference delay must be 0..5000 ms or null");
      if (milliseconds === 0) milliseconds = null;
    }
    this.testInferenceDelayMs = milliseconds;
  }

  async _ensureTestHooks() {
    if (!this.testHooks || !this.workerUrl)
      throw named(
        "TEST_HOOKS_DISABLED",
        "Delayed inference requires the dedicated test worker entry point",
      );
    if (this.testHooksConfigured && this.testHooksGeneration === this.generation) return;
    await this.request(
      { action: "test", test_action: "configure", enabled: true },
      { timeoutMs: 5000 },
    );
    this.testHooksConfigured = true;
    this.testHooksGeneration = this.generation;
  }

  async infer({
    planes,
    size,
    legal,
    ruleset,
    request_id: requestId,
    position_digest: positionDigest,
    model,
  }) {
    this._assertModel(model);
    let action = "infer";
    let testDelay = null;
    if (this.testHooks && Number.isInteger(this.testInferenceDelayMs) && this.testInferenceDelayMs > 0) {
      await this._ensureTestHooks();
      action = "test";
      testDelay = this.testInferenceDelayMs;
    }
    return this.request(
      {
        action,
        ...(testDelay === null ? {} : { test_action: "infer", test_delay_ms: testDelay }),
        planes,
        size,
        legal,
        ruleset,
        request_id: requestId,
        position_digest: positionDigest,
        model,
      },
      { timeoutMs: INFER_TIMEOUT_MS },
    );
  }

  // Legal-only payload for MCTS: the search never consumes full policy logits.
  // Raw inference/parity/benchmark paths keep the complete response.
  async inferCompact({
    planes = null,
    planes_base64: planesBase64 = null,
    size,
    legal,
    ruleset,
    request_id: requestId,
    position_digest: positionDigest,
    model,
  }) {
    this._assertModel(model);
    if ((planes === null) === (planesBase64 === null))
      throw named("INPUT_INVALID", "Provide exactly one of planes or planes_base64");
    let action = "inferCompact";
    let testDelay = null;
    if (
      this.testHooks &&
      Number.isInteger(this.testInferenceDelayMs) &&
      this.testInferenceDelayMs > 0
    ) {
      await this._ensureTestHooks();
      action = "test";
      testDelay = this.testInferenceDelayMs;
    }
    const payload = {
      action,
      ...(testDelay === null
        ? {}
        : { test_action: "infer_compact", test_delay_ms: testDelay }),
      ...(planesBase64 === null ? { planes } : { planes_base64: planesBase64 }),
      size,
      legal,
      ruleset,
      request_id: requestId,
      position_digest: positionDigest,
      model,
    };
    if (this.batching && testDelay === null) return this._queueInference(payload);
    return this.request(payload, { timeoutMs: INFER_TIMEOUT_MS });
  }

  inferManyCompact({ cases, model }) {
    this._assertModel(model);
    return this.request(
      { action: "inferManyCompact", cases, model },
      { timeoutMs: INFER_TIMEOUT_MS },
    );
  }

  inferMany({ cases, model }) {
    this._assertModel(model);
    return this.request({ action: "inferMany", cases, model }, { timeoutMs: INFER_TIMEOUT_MS });
  }

  // Every workload must name the exact model it was prepared for. A missing or
  // stale identity is rejected before any request reaches the worker.
  sustained({ cases, iterations, model }) {
    this._assertModel(model);
    return this.request(
      { action: "sustained", cases, iterations, model },
      { timeoutMs: BENCHMARK_TIMEOUT_MS },
    );
  }

  benchmark({ cases, batchSizes, warmup = 2, reps = 5, model }) {
    this._assertModel(model);
    return this.request(
      { action: "benchmark", cases, batchSizes, warmup, reps, model },
      { timeoutMs: BENCHMARK_TIMEOUT_MS },
    );
  }

  async release({ unloadBundle = false } = {}) {
    if (!this.worker) return { released: false };
    const result = await this.request({ action: "release", unloadBundle }, { timeoutMs: 10000 });
    this.model = null;
    if (unloadBundle) this.workerProvider = null;
    return result;
  }
}
