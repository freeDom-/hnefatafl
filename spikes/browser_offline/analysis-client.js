/* Isolated prototype: one worker, one job, eight cached positions. No API fallback. */
const MAX_REQUEST_BYTES = 512 * 1024;
const encoder = new TextEncoder();
const stable = (value) =>
  JSON.stringify(value, (_key, item) =>
    item && !Array.isArray(item) && typeof item === "object"
      ? Object.fromEntries(
          Object.keys(item)
            .sort()
            .map((key) => [key, item[key]]),
        )
      : item,
  );
export class LocalAnalysisClient {
  constructor() {
    this.worker = null;
    this.boot = null;
    this.pending = null;
    this.busy = false;
    this.foregroundActive = false;
    this.generation = 0;
    this.sequence = 0;
    this.cache = new Map();
    this.info = null;
  }
  load() {
    if (this.boot) return this.boot;
    if (typeof Worker === "undefined")
      return Promise.reject(new Error("Web Workers are unsupported"));
    const worker = new Worker(new URL("./worker.js", import.meta.url));
    this.worker = worker;
    this.boot = new Promise((resolve, reject) => {
      const timer = setTimeout(
        () =>
          this.abort(new Error("Runtime initialization exceeded 30 seconds")),
        30000,
      );
      this.startup = { reject, timer };
      worker.onmessage = ({ data }) => {
        if (this.worker !== worker) return;
        if (data.id === 0) {
          clearTimeout(timer);
          this.startup = null;
          if (data.error) {
            reject(new Error(data.error));
            this.cancel("Runtime initialization failed");
          } else {
            this.info = data.value;
            resolve(data.value);
          }
          return;
        }
        const pending = this.pending;
        if (!pending || pending.id !== data.id) return;
        clearTimeout(pending.timer);
        this.pending = null;
        if (data.error) pending.reject(new Error(data.error));
        else pending.resolve(data.value);
      };
      worker.onerror = (event) =>
        this.abort(new Error(event.message || "Worker failed"));
    });
    return this.boot;
  }
  // Hard cancellation: terminates the worker and rejects in-flight work as AbortError.
  // Cancelling with no active work is a no-op so an idle worker stays reusable.
  cancel(reason = "Local analysis cancelled") {
    if (!this.busy && !this.startup && !this.pending) return;
    this.abort(new DOMException(reason, "AbortError"));
  }
  // Failure path: terminates the worker and rejects in-flight work with the real error.
  abort(error) {
    this.generation++;
    this.worker?.terminate();
    this.worker = null;
    this.boot = null;
    this.info = null;
    this.busy = false;
    this.foregroundActive = false;
    if (this.startup) {
      clearTimeout(this.startup.timer);
      this.startup.reject(error);
      this.startup = null;
    }
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.reject(error);
      this.pending = null;
    }
  }
  async request(
    request,
    { timeoutMs = 5000, preempt = false, foreground = false } = {},
  ) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 5000)
      throw new Error("Local job deadline must be between 1 and 5000 ms");
    let serialized;
    try {
      serialized = JSON.stringify(request);
    } catch (error) {
      throw new Error(
        `Local request could not be serialized: ${error.message}`,
      );
    }
    if (typeof serialized !== "string")
      throw new Error("Local request could not be serialized");
    // Validate the exact UTF-8 payload handed to the worker/Python boundary.
    if (encoder.encode(serialized).length > MAX_REQUEST_BYTES)
      throw new Error("Input exceeds 512 KiB");
    if (this.busy) {
      if (!preempt)
        throw new Error("Only one local job can run; cancel or await it first");
      // Background work never terminates an active foreground operation.
      if (this.foregroundActive && !foreground)
        throw new DOMException(
          "Foreground gameplay has priority",
          "ForegroundPriorityError",
        );
      this.cancel("Superseded by a newer local job");
    }
    const generation = this.generation;
    this.busy = true;
    this.foregroundActive = foreground;
    try {
      await this.load();
      if (this.generation !== generation)
        throw new DOMException("Superseded request", "AbortError");
      return await new Promise((resolve, reject) => {
        const id = ++this.sequence;
        const timer = setTimeout(
          () =>
            this.abort(
              new DOMException(
                "Local job exceeded its hard deadline",
                "TimeoutError",
              ),
            ),
          timeoutMs,
        );
        this.pending = { id, resolve, reject, timer };
        try {
          this.worker.postMessage({ id, request: serialized });
        } catch (error) {
          // Reset the failed transport so the next request starts a fresh worker.
          this.abort(error);
        }
      });
    } finally {
      if (this.generation === generation) {
        this.busy = false;
        this.foregroundActive = false;
      }
    }
  }
  async analyze(state, config = {}, { preempt = false } = {}) {
    const snapshot = structuredClone({ state, config });
    const key = stable({ protocol: 1, ...snapshot });
    const cached = this.cache.get(key);
    if (cached) {
      // A cache hit is the newest request, but it never terminates foreground work.
      if (this.busy && !this.foregroundActive)
        this.cancel("Superseded by a cached result");
      this.cache.delete(key);
      this.cache.set(key, cached);
      return { ...structuredClone(cached), cached: true };
    }
    const result = await this.request(
      { action: "analysis", ...snapshot },
      { preempt },
    );
    this.cache.set(key, structuredClone(result));
    while (this.cache.size > 8)
      this.cache.delete(this.cache.keys().next().value);
    return { ...result, cached: false };
  }
}
