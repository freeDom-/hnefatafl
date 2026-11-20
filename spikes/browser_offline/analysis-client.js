/* Isolated prototype: one worker, one job, eight cached positions. No API fallback.
 *
 * Watchdogs are explicitly bounded and selected per request:
 *  - initialization has its own 30 s ceiling;
 *  - an ordinary interactive job is limited to 5 s;
 *  - an analysis request may grant the search its cooperative deadline plus a
 *    margin, up to 20 s, so a legitimate 10 s search can return its last
 *    completed result instead of being killed at 5 s;
 *  - an explicit experimental benchmark case may use up to 75 s, because a
 *    fixed-work search is deliberately unbounded in time but bounded in depth.
 * No request can execute indefinitely.
 */
const MAX_REQUEST_BYTES = 512 * 1024;
const INIT_TIMEOUT_MS = 30000;
const MAX_JOB_TIMEOUT_MS = 5000;
const MAX_ANALYSIS_TIMEOUT_MS = 20000;
const MAX_EXTENDED_TIMEOUT_MS = 75000;
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
    this.startup = null;
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
    const generation = this.generation;
    this.boot = new Promise((resolve, reject) => {
      const timer = setTimeout(
        () =>
          this.abort(
            new DOMException(
              "Runtime initialization exceeded 30 seconds",
              "TimeoutError",
            ),
          ),
        INIT_TIMEOUT_MS,
      );
      this.startup = { reject, timer };
      worker.onmessage = ({ data }) => {
        // Ignore messages from a worker that has been replaced or abandoned.
        if (this.worker !== worker || this.generation !== generation) return;
        if (data.id === 0) {
          clearTimeout(timer);
          this.startup = null;
          if (data.error) {
            reject(new Error(data.error));
            this.abort(new Error("Runtime initialization failed"));
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

  // Graceful shutdown: reject active work and release the worker (page lifecycle).
  dispose() {
    this.cancel("Local analysis terminated");
    this.abort(new DOMException("Local analysis disposed", "AbortError"));
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
      const { reject } = this.startup;
      this.startup = null;
      reject(error);
    }
    if (this.pending) {
      clearTimeout(this.pending.timer);
      const { reject } = this.pending;
      this.pending = null;
      reject(error);
    }
  }

  async request(
    request,
    {
      timeoutMs = MAX_JOB_TIMEOUT_MS,
      ceilingMs = MAX_JOB_TIMEOUT_MS,
      preempt = false,
      foreground = false,
    } = {},
  ) {
    const ceiling = Math.min(ceilingMs, MAX_EXTENDED_TIMEOUT_MS);
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > ceiling)
      throw new Error(`Local job deadline must be between 1 and ${ceiling} ms`);
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
        const timer = setTimeout(() => {
          // Only the still-active worker/generation may be aborted.
          if (this.generation !== generation) return;
          this.abort(
            new DOMException(
              "Local job exceeded its hard deadline",
              "TimeoutError",
            ),
          );
        }, timeoutMs);
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

  // Watchdog for a search: the cooperative deadline plus margin, bounded.
  static watchdogFor(config, ceilingMs = MAX_ANALYSIS_TIMEOUT_MS) {
    const seconds = Number(config?.time_limit);
    const requested = Number.isFinite(seconds) && seconds > 0 ? seconds : 5;
    return Math.min(
      ceilingMs,
      Math.max(MAX_JOB_TIMEOUT_MS, Math.round(requested * 1000) + 5000),
    );
  }

  async analyze(state, config = {}, { preempt = false, ceilingMs } = {}) {
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
    const ceiling = ceilingMs ?? MAX_ANALYSIS_TIMEOUT_MS;
    const result = await this.request(
      { action: "analysis", ...snapshot },
      {
        preempt,
        ceilingMs: ceiling,
        timeoutMs: LocalAnalysisClient.watchdogFor(config, ceiling),
      },
    );
    this.cache.set(key, structuredClone(result));
    while (this.cache.size > 8)
      this.cache.delete(this.cache.keys().next().value);
    return { ...result, cached: false };
  }

  // Experimental Jarl benchmark helpers. The same serialized positions a native
  // CPython run reads are executed by the shared staged benchmark module.
  jarlPlan() {
    return this.request({ action: "jarl_plan" }, { timeoutMs: MAX_JOB_TIMEOUT_MS });
  }

  jarlIdentity(positions, configs, options = {}) {
    return this.request(
      {
        action: "jarl_identity",
        positions,
        configs,
        mode: options.mode ?? "fixed_time",
        seed: options.seed ?? 7,
      },
      { timeoutMs: MAX_JOB_TIMEOUT_MS },
    );
  }

  jarlBenchmark(positions, configs, options = {}) {
    const timeoutMs = options.timeoutMs ?? MAX_EXTENDED_TIMEOUT_MS;
    return this.request(
      {
        action: "jarl_benchmark",
        positions,
        configs,
        mode: options.mode ?? "fixed_time",
        seed: options.seed ?? 7,
      },
      {
        timeoutMs,
        ceilingMs: MAX_EXTENDED_TIMEOUT_MS,
        preempt: options.preempt ?? true,
        foreground: options.foreground ?? false,
      },
    );
  }

  // One independently bounded case. A failing or slow case never discards
  // earlier records because each case is a separate request.
  jarlCase(position, config, options = {}) {
    const timeoutMs = options.timeoutMs ?? MAX_EXTENDED_TIMEOUT_MS;
    return this.request(
      {
        action: "jarl_case",
        positions: [position],
        configs: [config],
        mode: options.mode ?? "fixed_time",
        seed: options.seed ?? 7,
      },
      {
        timeoutMs,
        ceilingMs: MAX_EXTENDED_TIMEOUT_MS,
        preempt: options.preempt ?? false,
      },
    );
  }
}
