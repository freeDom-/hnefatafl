/* Isolated prototype: one worker, one job, eight cached positions. No API fallback. */
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
        () => this.cancel("Runtime initialization exceeded 30 seconds"),
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
      worker.onerror = (event) => this.cancel(event.message || "Worker failed");
    });
    return this.boot;
  }
  cancel(reason = "Local analysis cancelled") {
    this.generation++;
    this.worker?.terminate();
    this.worker = null;
    this.boot = null;
    this.info = null;
    this.busy = false;
    const error = new DOMException(reason, "AbortError");
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
  async request(request, { timeoutMs = 5000 } = {}) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 5000)
      throw new Error("Local job deadline must be between 1 and 5000 ms");
    if (this.busy)
      throw new Error("Only one local job can run; cancel or await it first");
    if (JSON.stringify(request).length > 512 * 1024)
      throw new Error("Input exceeds 512 KiB");
    const payload = structuredClone(request);
    const generation = this.generation;
    this.busy = true;
    try {
      await this.load();
      if (this.generation !== generation)
        throw new DOMException("Superseded request", "AbortError");
      return await new Promise((resolve, reject) => {
        const id = ++this.sequence;
        const timer = setTimeout(
          () => this.cancel("Local job exceeded its hard deadline"),
          timeoutMs,
        );
        this.pending = { id, resolve, reject, timer };
        this.worker.postMessage({ id, request: payload });
      });
    } finally {
      if (this.generation === generation) this.busy = false;
    }
  }
  async analyze(state, config = {}) {
    const snapshot = structuredClone({ state, config });
    const key = stable({ protocol: 1, ...snapshot });
    const cached = this.cache.get(key);
    if (cached) {
      this.cache.delete(key);
      this.cache.set(key, cached);
      return { ...structuredClone(cached), cached: true };
    }
    const result = await this.request({ action: "analysis", ...snapshot });
    this.cache.set(key, structuredClone(result));
    while (this.cache.size > 8)
      this.cache.delete(this.cache.keys().next().value);
    return { ...result, cached: false };
  }
}
