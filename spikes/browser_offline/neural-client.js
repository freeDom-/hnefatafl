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

export class NeuralClient {
  constructor({ testHooks = false } = {}) {
    this.worker = null;
    this.workerProvider = null;
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
    this.testInferenceDelayMs = null;
    this.testHooksConfigured = false;
    this.testHooksGeneration = null;
    // Injectable digest seam so cancellation during verification can be tested
    // deterministically. Production always uses crypto.subtle SHA-256.
    this.digestFn = sha256Hex;
  }

  _spawn() {
    if (typeof Worker === "undefined") throw new Error("Web Workers are unsupported");
    const worker = new Worker(new URL("./neural-worker.js", import.meta.url));
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

  // Cancel one in-flight inference without losing a healthy session when it can
  // drain within ``drainMs``. The rejected request settles immediately for the
  // caller; only the worker drain is awaited. If the worker does not drain, it
  // is terminated and the session must be reloaded.
  async cancelRequest({ reason = "Neural request cancelled", drainMs = 1500 } = {}) {
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

  async load(modelUrl, { provider = "wasm", size, ruleset, expectedSha256 } = {}) {
    if (!["wasm", "webgpu"].includes(provider))
      throw named("ProviderError", `Unsupported provider: ${provider}`);
    if (this.loading) throw named("ModelLoadError", "A model load is already in progress");
    const token = ++this.sequenceLoad;
    this.loading = token;
    try {
      // A worker that loaded a different ORT bundle cannot serve this provider,
      // even when the previous load failed before publishing a model. Abort
      // before capturing the generation so the new load owns the new worker.
      if (this.worker && this.workerProvider !== provider) {
        this.abort(named("AbortError", "Recreating worker for provider switch"));
        this.loading = token;
      }
      const generation = this.generation;
      const { buffer } = await this.fetchModel(modelUrl, { expectedSha256 });
      if (this.generation !== generation || this.loading !== token)
        throw named("AbortError", "Model load superseded");
      if (!this.worker) this._spawn();
      this.workerProvider = provider;
      // From here the worker may release the previous session, so the interface
      // must not keep advertising it as active.
      this.model = null;
      const result = await this.request(
        { action: "load", buffer, provider, size, ruleset, expectedSha256 },
        { timeoutMs: LOAD_TIMEOUT_MS },
      );
      if (this.generation !== generation || this.loading !== token)
        throw named("AbortError", "Model load superseded during session creation");
      this.model = { url: modelUrl, provider, size, ruleset, ...result };
      return this.model;
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
    if (!this.testHooks)
      throw named("TEST_HOOKS_DISABLED", "Inference delay is unavailable outside lifecycle tests");
    if (this.testHooksConfigured && this.testHooksGeneration === this.generation) return;
    await this.request(
      { action: "configureTestHooks", enabled: true },
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
      action = "inferTestonly";
      testDelay = this.testInferenceDelayMs;
    }
    return this.request(
      {
        action,
        planes,
        size,
        legal,
        ruleset,
        request_id: requestId,
        position_digest: positionDigest,
        model,
        ...(testDelay === null ? {} : { test_delay_ms: testDelay }),
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
      action = "inferCompactTestonly";
      testDelay = this.testInferenceDelayMs;
    }
    return this.request(
      {
        action,
        ...(planesBase64 === null ? { planes } : { planes_base64: planesBase64 }),
        size,
        legal,
        ruleset,
        request_id: requestId,
        position_digest: positionDigest,
        model,
        ...(testDelay === null ? {} : { test_delay_ms: testDelay }),
      },
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
