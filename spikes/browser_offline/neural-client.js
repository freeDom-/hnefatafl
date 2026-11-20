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
  constructor() {
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
      if (!pending || pending.id !== data.id) return;
      clearTimeout(pending.timer);
      this.pending = null;
      this.busy = false;
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
      pending.reject(error);
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
    this.terminate("Neural inference disposed");
  }

  async request(request, { timeoutMs = INFER_TIMEOUT_MS } = {}) {
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
      this.pending = { id, resolve, reject, timer };
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

  infer(planes, size, legal, ruleset, identity = {}) {
    return this.request(
      {
        action: "infer",
        planes,
        size,
        legal,
        ruleset,
        request_id: identity.request_id,
        position_digest: identity.position_digest,
      },
      { timeoutMs: INFER_TIMEOUT_MS },
    );
  }

  inferMany(cases) {
    return this.request({ action: "inferMany", cases }, { timeoutMs: INFER_TIMEOUT_MS });
  }

  sustained(cases, iterations) {
    return this.request(
      { action: "sustained", cases, iterations },
      { timeoutMs: BENCHMARK_TIMEOUT_MS },
    );
  }

  benchmark(cases, batchSizes, { warmup = 2, reps = 5 } = {}) {
    return this.request(
      { action: "benchmark", cases, batchSizes, warmup, reps },
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
