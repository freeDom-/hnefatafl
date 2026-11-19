/* Isolated prototype: one worker, one job, eight cached positions. No API fallback. */
const MAX_REQUEST_BYTES = 512 * 1024;
// Ordinary interactive jobs keep the original 5 s hard watchdog. Long-running
// benchmark jobs opt in explicitly and are still bounded by a finite ceiling so
// a stuck worker can never run forever.
const MAX_JOB_TIMEOUT_MS = 5000;
const MAX_EXTENDED_TIMEOUT_MS = 180000;
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
      worker.onerror = (event) => {
        // Ignore errors from workers that have been cancelled or replaced.
        if (this.worker !== worker) return;
        this.abort(new Error(event.message || "Worker failed"));
      };
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
    { timeoutMs = 5000, preempt = false, foreground = false, extended = false } = {},
  ) {
    const limit = extended ? MAX_EXTENDED_TIMEOUT_MS : MAX_JOB_TIMEOUT_MS;
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > limit)
      throw new Error(`Local job deadline must be between 1 and ${limit} ms`);
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
  // Experimental Jarl benchmark: the same serialized positions a native CPython
  // run reads, executed by the shared staged benchmark module inside the worker.
  jarlPlan() {
    return this.request({ action: "jarl_plan" }, { timeoutMs: 5000 });
  }
  jarlBenchmark(
    positions,
    configs,
    { mode = "fixed_time", seed = 7, timeoutMs = 65000 } = {},
  ) {
    return this.request(
      { action: "jarl_benchmark", positions, configs, mode, seed },
      { timeoutMs, preempt: true, extended: true },
    );
  }
}
