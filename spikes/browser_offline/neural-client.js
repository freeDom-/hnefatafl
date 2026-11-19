/* Isolated neural inference client: one worker, one model, no API fallback. */
export class NeuralClient {
  constructor() {
    this.worker = null;
    this.pending = null;
    this.busy = false;
    this.sequence = 0;
    this.model = null;
  }
  start() {
    if (this.worker) return;
    if (typeof Worker === "undefined")
      throw new Error("Web Workers are unsupported");
    this.worker = new Worker(new URL("./neural-worker.js", import.meta.url));
    this.worker.onmessage = ({ data }) => {
      const pending = this.pending;
      if (!pending || pending.id !== data.id) return;
      clearTimeout(pending.timer);
      this.pending = null;
      this.busy = false;
      if (data.error) pending.reject(new Error(data.error));
      else pending.resolve(data.value);
    };
    this.worker.onerror = (event) => {
      this.fail(new Error(event.message || "Neural worker failed"));
    };
  }
  fail(error) {
    this.worker?.terminate();
    this.worker = null;
    this.busy = false;
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.reject(error);
      this.pending = null;
    }
  }
  request(request, { timeoutMs = 120000 } = {}) {
    this.start();
    if (this.busy)
      return Promise.reject(new Error("A neural job is already running"));
    this.busy = true;
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(
        () => this.fail(new Error("Neural job exceeded its hard deadline")),
        timeoutMs,
      );
      this.pending = { id, resolve, reject, timer };
      try {
        this.worker.postMessage({ id, request });
      } catch (error) {
        this.fail(error);
      }
    });
  }
  capabilities() {
    return this.request({ action: "capabilities" }, { timeoutMs: 10000 });
  }
  async load(modelUrl, { provider = "wasm", size } = {}) {
    const response = await fetch(modelUrl);
    if (!response.ok) throw new Error(`Cannot load model: ${response.status}`);
    const buffer = await response.arrayBuffer();
    const result = await this.request(
      { action: "load", buffer, provider, size },
      { timeoutMs: 120000 },
    );
    this.model = { url: modelUrl, provider, size, ...result };
    return this.model;
  }
  infer(planes, size, legal) {
    return this.request({ action: "infer", planes, size, legal }, { timeoutMs: 30000 });
  }
  benchmark(planes, size, batchSizes, { warmup = 2, reps = 5 } = {}) {
    return this.request(
      { action: "benchmark", planes, size, batchSizes, warmup, reps },
      { timeoutMs: 180000 },
    );
  }
  terminate() {
    this.worker?.terminate();
    this.worker = null;
    this.busy = false;
    this.pending = null;
  }
}
