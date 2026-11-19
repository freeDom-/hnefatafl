/* Isolated neural inference client.
 *
 * Lifecycle guarantees mirror the proven classical analysis client:
 *  - every request belongs to a worker generation; a superseded worker's
 *    messages and errors are ignored;
 *  - hard timeouts clear their own timer and only abort the still-active
 *    generation, so an obsolete timer can never kill a replacement worker;
 *  - terminate()/dispose() reject the active promise with an AbortError instead
 *    of abandoning it;
 *  - model download is bounded and cancellable, and a failed load never leaves
 *    the interface believing a new model is active;
 *  - a provider switch requires worker recreation and is handled explicitly.
 *
 * Error kinds are distinguishable by `name`:
 *  - AbortError      user/model cancellation or disposal
 *  - TimeoutError    hard job deadline
 *  - ModelLoadError  model download or integrity failure
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

export class NeuralClient {
  constructor() {
    this.worker = null;
    this.pending = null;
    this.busy = false;
    this.sequence = 0;
    this.generation = 0;
    this.model = null;
    this.download = null;
    this.modelUrl = null;
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
      } else {
        pending.resolve(data.value);
      }
    };
    childErrorHandler(this, worker);
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
    this.busy = false;
    this.model = null;
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
      download.controller.abort();
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

  async request(request, { timeoutMs = INFER_TIMEOUT_MS, name = "WorkerError" } = {}) {
    if (!this.worker) this._spawn();
    if (this.busy) throw new Error("A neural job is already running");
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
        this.abort(named(name, `Neural transport failure: ${error?.message ?? error}`));
      }
    });
  }

  capabilities() {
    return this.request({ action: "capabilities" }, { timeoutMs: 15000 });
  }

  // Bounded, cancellable model download that verifies the staged digest before
  // handing bytes to the worker.
  async fetchModel(url, { expectedSha256 } = {}) {
    if (this.download) throw new Error("A model download is already in progress");
    const controller = new AbortController();
    const download = { controller, reject: null, timer: null };
    const promise = new Promise((resolve, reject) => {
      download.reject = reject;
      download.timer = setTimeout(() => {
        controller.abort();
        reject(named("TimeoutError", "Model download exceeded its deadline"));
      }, DOWNLOAD_TIMEOUT_MS);
      fetch(url, { signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw named("ModelLoadError", `Cannot load model: ${response.status}`);
          const length = Number(response.headers.get("content-length") ?? 0);
          if (length > MAX_MODEL_BYTES) throw named("ModelLoadError", "Model exceeds the size limit");
          const buffer = await response.arrayBuffer();
          if (buffer.byteLength > MAX_MODEL_BYTES)
            throw named("ModelLoadError", "Model exceeds the size limit");
          clearTimeout(download.timer);
          if (this.download === download) this.download = null;
          if (expectedSha256) {
            const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", buffer))]
              .map((byte) => byte.toString(16).padStart(2, "0"))
              .join("");
            if (digest !== expectedSha256)
              throw named("ModelLoadError", "Downloaded model failed its integrity check");
          }
          resolve({ buffer, sha256: expectedSha256 ?? null });
        })
        .catch((error) => {
          clearTimeout(download.timer);
          if (this.download === download) this.download = null;
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
    return promise;
  }

  async load(modelUrl, { provider = "wasm", size, ruleset, expectedSha256 } = {}) {
    // Switching provider on an existing worker is rejected by the worker; make
    // the recreation explicit instead of producing a confusing failure.
    if (this.worker && this.model && this.model.provider !== provider)
      this.abort(named("AbortError", "Recreating worker for provider switch"));
    const { buffer } = await this.fetchModel(modelUrl, { expectedSha256 });
    const result = await this.request(
      { action: "load", buffer, provider, size, ruleset, expectedSha256 },
      { timeoutMs: LOAD_TIMEOUT_MS },
    );
    this.model = { url: modelUrl, provider, size, ruleset, ...result };
    this.modelUrl = modelUrl;
    return this.model;
  }

  infer(planes, size, legal) {
    return this.request({ action: "infer", planes, size, legal }, { timeoutMs: INFER_TIMEOUT_MS });
  }

  inferMany(cases) {
    return this.request({ action: "inferMany", cases }, { timeoutMs: INFER_TIMEOUT_MS });
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
    return result;
  }
}

// Attach the error handler separately so it can be replaced in tests.
function childErrorHandler(client, worker) {
  worker.onerror = (event) => {
    if (client.worker !== worker) return;
    client.abort(named("WorkerError", event.message || "Neural worker failed"));
  };
}
