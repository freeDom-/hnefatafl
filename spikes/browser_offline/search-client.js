/* Browser neural MCTS coordinator.
 *
 * Responsibilities:
 *  - own the Pyodide search worker and its boot/search generations;
 *  - relay each inline neural evaluation request to the persistent neural
 *    inference worker, so no inference payload is retyped by Python and no
 *    second Python runtime is created;
 *  - enforce explicit admission, per-step deadlines, one cooperative search
 *    watchdog and bounded cancellation;
 *  - validate request/response association, legal action order and model
 *    identity before any prediction can influence the tree;
 *  - publish completed results only, with the engine's actual diagnostics.
 *
 * Cancellation prefers a cooperative `cancel` message that closes the Python
 * generator. Terminating the worker is the bounded final recovery path used when
 * the worker does not acknowledge cancellation or fails. A healthy worker and
 * neural session are retained across compatible searches.
 *
 * Error kinds are distinguishable by `name`/`code`:
 *   AbortError, TimeoutError, WorkerError, ModelLoadError, SearchError,
 *   INPUT_TOO_LARGE, INVALID_SEARCH_CONFIG, MODEL_IDENTITY_REQUIRED,
 *   MODEL_IDENTITY_MISMATCH, RESPONSE_MISMATCH.
 */
const MAX_REQUEST_BYTES = 512 * 1024;
const INIT_TIMEOUT_MS = 90000;
const CANCEL_ACK_TIMEOUT_MS = 2500;
const MAX_STEP_TIMEOUT_MS = 30000;
const MIN_STEP_TIMEOUT_MS = 2000;
const MIN_WATCHDOG_MS = 4000;
const MAX_WATCHDOG_MS = 45000;
const PROGRESS_INTERVAL_MS = 100;
// How long a cancelled inference may drain before the neural worker is replaced.
const INFERENCE_DRAIN_MS = 1500;
const encoder = new TextEncoder();

const BOUNDS = {
  iterations: [1, 4096],
  time_limit: [0.05, 10],
  exploration: [0, 10],
  seed: [0, 2 ** 32 - 1],
};

function named(name, message, code = null) {
  const error = new Error(message);
  error.name = name;
  error.code = code ?? name;
  return error;
}

export function normalizeSearchSettings(raw = {}) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    throw named("INVALID_SEARCH_CONFIG", "Search settings must be an object");
  const settings = {};
  if ("algorithm" in raw) {
    if (raw.algorithm !== "mcts")
      throw named("INVALID_SEARCH_CONFIG", "Only the mcts algorithm is supported");
    settings.algorithm = "mcts";
  }
  const integers = { iterations: 128, seed: 0 };
  for (const [name, fallback] of Object.entries(integers)) {
    const value = raw[name] ?? fallback;
    if (!Number.isInteger(value) || Array.isArray(value))
      throw named("INVALID_SEARCH_CONFIG", `${name} must be an integer`);
    const [low, high] = BOUNDS[name];
    if (value < low || value > high)
      throw named(
        name === "iterations" ? "SIMULATION_BUDGET" : "INVALID_SEARCH_CONFIG",
        `${name} must be between ${low} and ${high}`,
      );
    settings[name] = value;
  }
  for (const [name, fallback] of Object.entries({ time_limit: 2, exploration: 1.4 })) {
    const value = raw[name] ?? fallback;
    if (typeof value !== "number" || !Number.isFinite(value))
      throw named("INVALID_SEARCH_CONFIG", `${name} must be a finite number`);
    const [low, high] = BOUNDS[name];
    if (value < low || value > high)
      throw named(
        name === "time_limit" ? "TIME_BUDGET" : "INVALID_SEARCH_CONFIG",
        `${name} must be between ${low} and ${high}`,
      );
    settings[name] = value;
  }
  const known = new Set(["algorithm", ...Object.keys(BOUNDS), "iterations"]);
  for (const key of Object.keys(raw))
    if (!known.has(key))
      throw named("INVALID_SEARCH_CONFIG", `Unsupported search setting: ${key}`);
  // Analysis never enables training-only exploration noise.
  settings.algorithm = "mcts";
  return settings;
}

export function watchdogFor(settings) {
  const seconds = Number(settings?.time_limit);
  return Math.min(
    MAX_WATCHDOG_MS,
    Math.max(MIN_WATCHDOG_MS, Math.ceil((Number.isFinite(seconds) ? seconds : 2) * 2000) + 2000),
  );
}

function decodePlanes(base64) {
  const binary = atob(base64);
  const planes = new Float32Array(binary.length);
  for (let index = 0; index < binary.length; index += 1)
    planes[index] = binary.charCodeAt(index);
  return Array.from(planes);
}

function sameArray(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  return left.every((value, index) => value === right[index]);
}

export class NeuralSearchCoordinator {
  constructor({ neural = null } = {}) {
    this.neural = neural;
    this.worker = null;
    this.boot = null;
    this.pending = null;
    this.startup = null;
    this.sequence = 0;
    this.searchSequence = 0;
    this.generation = 0;
    this.active = null;
    this.info = null;
    // All outstanding cancellation/drain work. A new admission inherits the whole
    // barrier instead of only waiting for the immediately previous search.
    this.cleanupBarrier = Promise.resolve();
    // Set when a superseded search's cancellation acknowledgement failed after a
    // newer admission took ownership. The newer owner replaces the worker itself.
    this.workerUnhealthy = false;
    // Prefer the legal-only inference payload. The full-logit path remains for
    // raw parity/benchmark comparisons and as a controlled A/B reference.
    this.compactInference = true;
    // Optional direct search-worker <-> inference-worker MessageChannel. The
    // coordinator retains admission, ownership, watchdog, progress and final
    // validation; only the per-leaf payload relay moves off the main thread.
    this.directInference = false;
    // A disposed coordinator is permanently retired: no later call may spawn a
    // replacement worker after page teardown. Callers that want a usable
    // coordinator after a lifecycle reset must create a new instance.
    this.disposed = false;
  }

  load() {
    if (this.disposed)
      return Promise.reject(named("AbortError", "Search coordinator disposed"));
    if (this.boot) return this.boot;
    if (typeof Worker === "undefined")
      return Promise.reject(named("WorkerError", "Web Workers are unsupported"));
    const worker = new Worker(new URL("./search-worker.js", import.meta.url));
    this.worker = worker;
    const generation = this.generation;
    this.boot = new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => this.abort(named("TimeoutError", "Search runtime initialization timed out")),
        INIT_TIMEOUT_MS,
      );
      this.startup = { reject, timer };
      worker.onmessage = ({ data }) => {
        if (this.worker !== worker || this.generation !== generation) return;
        if (data.kind?.startsWith("direct_")) {
          const search = this.active;
          if (!search || search.searchId !== data.search_id || !search.directFinal) return;
          if (data.kind === "direct_progress") {
            this._publishProgress(search, { progress: data.progress }, search.onProgress);
            return;
          }
          if (data.kind === "direct_inference") {
            search.inferencePending = data.phase === "start";
            if (data.phase === "end") search.inferencePending = false;
            return;
          }
          const { resolve, reject } = search.directFinal;
          search.directFinal = null;
          if (data.kind === "direct_complete") resolve(data.event);
          else if (data.kind === "direct_cancelled")
            reject(search.error ?? named("AbortError", "Direct search was cancelled"));
          else
            reject(
              named(
                data.error?.code === "CANCELLED" ? "AbortError" : "WorkerError",
                data.error?.message ?? "Direct search failed",
              ),
            );
          return;
        }
        if (data.id === 0) {
          clearTimeout(timer);
          this.startup = null;
          if (data.error) {
            reject(named("WorkerError", `Search runtime initialization failed: ${data.error}`));
            this.abort(named("WorkerError", "Search runtime initialization failed"));
          } else {
            this.info = data.value;
            resolve(data.value);
          }
          return;
        }
        const pending = this.pending;
        if (!pending || pending.id !== data.id) return;
        this._settlePending(pending);
        if (data.error) pending.reject(named("WorkerError", data.error));
        else pending.resolve(data.value);
      };
      worker.onerror = (event) => {
        if (this.worker !== worker) return;
        this.abort(named("WorkerError", event.message || "Search worker failed"));
      };
    });
    return this.boot;
  }

  _settlePending(pending) {
    clearTimeout(pending.timer);
    if (this.pending === pending) this.pending = null;
    pending.finished?.();
  }

  // Failure path: terminate the worker and reject in-flight work with the cause.
  abort(error) {
    this.generation += 1;
    const active = this.active;
    if (active) {
      active.error = active.error ?? error;
      clearTimeout(active.watchdog);
      // Release an inference await so the search loop cannot outlive the worker.
      active.rejectCancel?.(active.error);
      if (active.directFinal) {
        const { reject } = active.directFinal;
        active.directFinal = null;
        reject(error);
      }
    }
    try {
      this.worker?.terminate();
    } catch {
      // terminating an already-dead worker is harmless
    }
    this.worker = null;
    this.boot = null;
    this.info = null;
    if (this.startup) {
      clearTimeout(this.startup.timer);
      const { reject } = this.startup;
      this.startup = null;
      reject(error);
    }
    const pending = this.pending;
    if (pending) {
      this._settlePending(pending);
      pending.reject(error);
    }
  }

  // Replace an unresponsive worker without touching the active search record.
  // Used when a *previous* search's cancellation fails after a newer admission
  // has already taken ownership.
  _replaceWorker(error) {
    this.generation += 1;
    const worker = this.worker;
    this.worker = null;
    this.boot = null;
    this.info = null;
    this.workerUnhealthy = false;
    try {
      worker?.terminate();
    } catch {
      // terminating an already-dead worker is harmless
    }
    if (this.startup) {
      clearTimeout(this.startup.timer);
      const { reject } = this.startup;
      this.startup = null;
      reject(error);
    }
    const pending = this.pending;
    if (pending) {
      this._settlePending(pending);
      pending.reject(error);
    }
  }

  dispose() {
    this.disposed = true;
    const active = this.active;
    if (active) {
      active.cancelled = true;
      active.error = active.error ?? named("AbortError", "Search coordinator disposed");
    }
    this.abort(named("AbortError", "Search coordinator disposed"));
  }

  _owner(search) {
    return this.active === search && !this.disposed;
  }

  _assertOwner(search) {
    if (this.disposed) throw named("AbortError", "Search coordinator disposed");
    if (search.cancelled) throw search.error ?? named("AbortError", "Search cancelled");
    if (this.active !== search)
      throw named("AbortError", "Search superseded by a newer search");
  }

  _newSearch(configuration, expectedModel) {
    const sequence = ++this.searchSequence;
    const search = {
      // Unique before the first await: ownership is never granted after
      // initialization, so cancellation and competing admissions are effective
      // during cold start.
      searchId: `s${sequence}-${this.generation}-${Math.random().toString(36).slice(2, 8)}`,
      generation: this.generation,
      configuration,
      expectedModel,
      cancelled: false,
      error: null,
      started: performance.now(),
      searchStarted: null,
      startedRequest: false,
      inferencePending: false,
      watchdog: null,
      cancelPromise: null,
      phase: "admitted",
      timings: {
        steps: 0,
        step_ms: 0,
        python_ms: 0,
        neural_wait_ms: 0,
        inferences: 0,
        progress_events: 0,
        tensor_prepare_ms: 0,
        inference_execution_ms: 0,
        policy_extract_ms: 0,
        serialize_ms: 0,
        parse_ms: 0,
        request_bytes: 0,
        response_bytes: 0,
        cancellation_ms: 0,
        inference_payload_mode: null,
        response_policy_values: 0,
        cache_hits: 0,
        cache_misses: 0,
      },
      provider: null,
    };
    search.cancelSignal = new Promise((_resolve, reject) => {
      search.rejectCancel = reject;
    });
    // Never let the cancellation signal become an unhandled rejection.
    search.cancelSignal.catch(() => {});
    return search;
  }

  // Cooperative cancellation with a bounded final recovery path. Idempotent.
  // Never cancels a search context that belongs to a newer worker generation and
  // never terminates a worker owned by a newer search.
  cancel(reason = "Search cancelled") {
    const active = this.active;
    if (!active) {
      return Promise.resolve({
        cancelled: false,
        released: { search: false, inference: { state: "idle", sessionRetained: false } },
      });
    }
    return this._cancelSearch(active, reason);
  }

  _cancelSearch(search, reason) {
    if (search.cancelPromise) return search.cancelPromise;
    search.cancelled = true;
    search.error = search.error ?? named("AbortError", reason);
    clearTimeout(search.watchdog);
    // Stop the search loop immediately, including during initialization.
    search.rejectCancel?.(search.error);
    // A pending request may belong to an older search's own cleanup; only the
    // owner's pending request may be rejected here.
    const pending = this.pending;
    if (pending && pending.owner === search) {
      this._settlePending(pending);
      pending.reject(search.error);
    }
    const started = performance.now();
    search.cancelPromise = (async () => {
      const released = {
        search: false,
        inference: { state: "idle", sessionRetained: Boolean(this.neural?.model) },
        searchWorkerGeneration: this.generation,
      };
      if (search.startedRequest && this.worker && search.generation === this.generation) {
        try {
          const envelope = await this._request(
            { action: "cancel", search_id: search.searchId },
            { timeoutMs: CANCEL_ACK_TIMEOUT_MS, abortOnTimeout: false, owner: search },
          );
          const event = envelope.event;
          if (event.search_id !== search.searchId) {
            throw named("RESPONSE_MISMATCH", "Cancellation acknowledged another search");
          }
          released.search = true;
          released.context = event.released ?? null;
        } catch (cancelError) {
          if (this.generation === search.generation && this.worker) {
            const failure = named(
              "WorkerError",
              "Search worker did not acknowledge cancellation",
            );
            if (this.active === search) {
              this.abort(failure);
            } else {
              // A newer search owns the worker; never terminate it from older
              // cleanup. The owner performs bounded replacement itself.
              this.workerUnhealthy = true;
            }
          }
        }
      }
      if (search.inferencePending) {
        try {
          released.inference = await this._releaseInference(search, reason);
        } catch (inferenceError) {
          // A failed drain must not reject the cancellation contract or block a
          // newer admission; recovery is reported as part of the acknowledgement.
          released.inference = {
            state: "failed",
            sessionRetained: false,
            error: String(inferenceError?.message ?? inferenceError),
          };
        }
      }
      search.timings.cancellation_ms = performance.now() - started;
      search.phase = "cancelled";
      return { cancelled: true, released };
    })();
    const priorBarrier = this.cleanupBarrier;
    this.cleanupBarrier = Promise.allSettled([priorBarrier, search.cancelPromise]).then(
      () => {},
    );
    return search.cancelPromise;
  }

  async _releaseInference(search, reason) {
    if (!this.neural || typeof this.neural.cancelRequest !== "function")
      return { state: "unavailable", sessionRetained: false };
    try {
      return await this.neural.cancelRequest({
        reason,
        drainMs: INFERENCE_DRAIN_MS,
        requestId: search.inferenceRequestId,
      });
    } finally {
      search.inferencePending = false;
    }
  }

  async _request(payload, { timeoutMs, abortOnTimeout = true, owner = null }) {
    await this.load();
    if (this.pending) throw named("WorkerError", "A search message is already in flight");
    const worker = this.worker;
    const generation = this.generation;
    const serializeStarted = performance.now();
    const serialized = JSON.stringify(payload);
    if (typeof serialized !== "string")
      throw named("INPUT_TOO_LARGE", "Search request could not be serialized");
    const requestBytes = encoder.encode(serialized).length;
    if (requestBytes > MAX_REQUEST_BYTES)
      throw named("INPUT_TOO_LARGE", "Search request exceeds 512 KiB");
    if (owner) {
      owner.timings.serialize_ms += performance.now() - serializeStarted;
      owner.timings.request_bytes += requestBytes;
    }
    const raw = await new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        if (this.generation !== generation || this.worker !== worker) return;
        const error = named("TimeoutError", `Search step exceeded ${timeoutMs} ms`);
        if (abortOnTimeout) {
          this.abort(error);
          return;
        }
        // A cancellation acknowledgement may time out without aborting the
        // worker that a newer admission now owns.
        const outstanding = this.pending;
        if (!outstanding) return;
        this._settlePending(outstanding);
        outstanding.reject(error);
      }, timeoutMs);
      const pending = { id, resolve, reject, timer, owner };
      pending.finished = () => {};
      this.pending = pending;
      try {
        worker.postMessage({ id, payload: serialized });
      } catch (error) {
        this._settlePending(pending);
        this.abort(named("WorkerError", `Search transport failure: ${error?.message ?? error}`));
      }
    });
    let envelope;
    const parseStarted = performance.now();
    try {
      envelope = JSON.parse(raw);
    } catch {
      throw named("WorkerError", "Search worker returned malformed JSON");
    }
    if (owner) {
      owner.timings.parse_ms += performance.now() - parseStarted;
      owner.timings.response_bytes += typeof raw === "string" ? raw.length : 0;
    }
    if (!envelope || typeof envelope !== "object")
      throw named("WorkerError", "Search worker returned no envelope");
    if (envelope.ok !== true)
      throw named(
        envelope.code === "CANCELLED" ? "AbortError" : "SearchError",
        envelope.message ?? "Search failed",
        envelope.code ?? "SEARCH_FAILED",
      );
    return envelope;
  }

  async infoAction() {
    const envelope = await this._request({ action: "info" }, { timeoutMs: 15000 });
    return { worker: this.info, python: envelope.info };
  }

  // Deterministic shared-driver parity (Level A). No inference is requested: the
  // evaluator runs inside this runtime, exercising the real generator.
  async parity({ state, settings = {}, trace = true }) {
    const configuration = normalizeSearchSettings(settings);
    const envelope = await this._request(
      { action: "parity", state, settings: configuration, trace },
      { timeoutMs: MAX_STEP_TIMEOUT_MS },
    );
    return envelope.event;
  }

  _validateEvent(search, event) {
    if (!event || typeof event !== "object")
      throw named("WorkerError", "Search worker returned no event");
    if (event.search_id !== search.searchId) {
      const error = named(
        "RESPONSE_MISMATCH",
        "Search response belongs to another search context",
      );
      search.error = search.error ?? error;
      if (this._owner(search)) this.abort(error);
      throw error;
    }
    return event;
  }

  async search({
    state,
    settings = {},
    model = null,
    provider = null,
    trace = false,
    onProgress = null,
  }) {
    if (this.disposed) throw named("AbortError", "Search coordinator disposed");
    const configuration = normalizeSearchSettings(settings);
    // The immutable identity is captured when the workload is prepared. Never
    // substitute whatever model happens to be loaded now.
    const expectedModel = model;
    if (!expectedModel)
      throw named("MODEL_IDENTITY_REQUIRED", "An expected model identity is required");
    if (!this.neural) throw named("MODEL_NOT_LOADED", "Neural inference is unavailable");
    const activeModel = this.neural.modelIdentity();
    if (
      activeModel.sha256 !== expectedModel.sha256 ||
      activeModel.ruleset !== expectedModel.ruleset ||
      activeModel.size !== expectedModel.size
    )
      throw named(
        "MODEL_IDENTITY_MISMATCH",
        "The prepared model identity does not match the loaded model",
      );
    // Reserve ownership synchronously, before the first asynchronous operation.
    const previous = this.active;
    const search = this._newSearch(configuration, {
      sha256: expectedModel.sha256,
      ruleset: expectedModel.ruleset,
      size: expectedModel.size,
    });
    this.active = search;
    try {
      if (previous) {
        // Ownership is re-checked after cancelling the previous search: a newer
        // admission may have replaced this one while it waited.
        await this._cancelSearch(previous, "Superseded by a newer search");
        this._assertOwner(search);
      }
      // Recover an unresponsive worker left by older cleanup only when this
      // admission owns the current worker.
      if (this.workerUnhealthy && this._owner(search)) this._replaceWorker(
        named("WorkerError", "Replacing an unhealthy search worker"),
      );
      // Cancel during cold initialization must be effective immediately, so the
      // shared boot promise is raced against this search's cancellation signal.
      await Promise.race([this.load(), search.cancelSignal]);
      this._assertOwner(search);
      // Inherit every outstanding cleanup dependency, not just the immediately
      // previous search's cancellation.
      await Promise.race([this.cleanupBarrier, search.cancelSignal]);
      this._assertOwner(search);
      if (this.workerUnhealthy && this._owner(search)) {
        // Older cleanup reported an unresponsive worker while this admission was
        // booting; replace it now that the owner has no in-flight boot promise.
        this._replaceWorker(named("WorkerError", "Replacing an unhealthy search worker"));
        await Promise.race([this.load(), search.cancelSignal]);
        this._assertOwner(search);
      }
      if (search.generation !== this.generation) {
        // A previous search's failed cancellation may have replaced the shared
        // worker before this search submitted anything. The fresh worker is now
        // owned by this admission; re-bind rather than failing the newer search.
        search.generation = this.generation;
      }
      // The cooperative search budget and the watchdog start when the search is
      // actually submitted, so cold worker/Pyodide initialization is bounded by
      // the separate initialization timeout instead of the search budget.
      search.searchStarted = performance.now();
      search.phase = "starting";
      search.startedRequest = true;
      search.watchdog = setTimeout(() => {
        if (!this._owner(search)) return;
        this.abort(
          named(
            "TimeoutError",
            `Search exceeded its ${watchdogFor(configuration)} ms hard watchdog`,
          ),
        );
      }, watchdogFor(configuration));
      search.onProgress = onProgress;
      if (this.directInference) {
        return await this._searchDirect(
          search,
          { state, settings: configuration, model: expectedModel, provider, trace },
          configuration,
        );
      }
      let event = this._validateEvent(
        search,
        (await this._step(
          search,
          {
            action: "start",
            search_id: search.searchId,
            state,
            settings: configuration,
            model: expectedModel,
            provider,
            trace,
          },
          search.searchStarted + configuration.time_limit * 1000,
        )),
      );
      this._assertOwner(search);
      this._publishProgress(search, event, onProgress);
      while (event.kind === "evaluate") {
        this._assertOwner(search);
        const deadline = search.searchStarted + configuration.time_limit * 1000;
        search.phase = "inference";
        const inference = await this._infer(search, event.request, expectedModel, deadline);
        this._assertOwner(search);
        search.phase = "resuming";
        event = this._validateEvent(
          search,
          await this._step(
            search,
            {
              action: "resume",
              search_id: search.searchId,
              request_index: event.request.request_index,
              legal: event.request.legal,
              priors: inference.legal_probabilities,
              value: inference.value,
            },
            search.searchStarted + configuration.time_limit * 1000,
          ),
        );
        this._assertOwner(search);
        this._publishProgress(search, event, onProgress);
      }
      if (event.kind === "cancelled") {
        const timedOut = event.reason === "deadline";
        throw timedOut
          ? named("TimeoutError", "Search reached its cooperative deadline")
          : search.error ?? named("AbortError", "Search was cancelled");
      }
      if (event.kind !== "complete")
        throw named("WorkerError", `Unexpected search event: ${event.kind}`);
      const result = this._finalize(search, event, configuration);
      // Final publication is an ownership boundary.
      this._assertOwner(search);
      search.phase = "completed";
      return result;
    } catch (error) {
      const finalError = search.error ?? error;
      if (this._owner(search) && (search.startedRequest || this.worker)) {
        try {
          await this._cancelSearch(search, "Search failed");
        } catch {
          // cancellation acknowledgement failure is bounded by abort()
        }
      }
      throw finalError;
    } finally {
      clearTimeout(search.watchdog);
      if (this._owner(search)) this.active = null;
    }
  }

  async _attachSearchDirect(port) {
    const worker = this.worker;
    if (!worker) throw named("WorkerError", "Search worker is not loaded");
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        worker.removeEventListener?.("message", onMessage);
        reject(named("TimeoutError", "Search direct port attachment timed out"));
      }, 5000);
      const onMessage = ({ data }) => {
        if (data?.id !== id) return;
        clearTimeout(timer);
        worker.removeEventListener?.("message", onMessage);
        if (data.value?.kind === "direct_attached") resolve();
        else reject(named("WorkerError", "Search direct port attachment failed"));
      };
      worker.addEventListener?.("message", onMessage);
      worker.postMessage({ control: "attachDirect", id, port }, [port]);
    });
  }

  async _searchDirect(search, payload, configuration) {
    this._assertOwner(search);
    if (!this.neural || typeof this.neural.attachDirectPort !== "function")
      throw named("WorkerError", "Neural direct inference is unavailable");
    const channel = new MessageChannel();
    let attached = false;
    try {
      await Promise.race([
        this.neural.attachDirectPort(channel.port2),
        search.cancelSignal,
      ]);
      this._assertOwner(search);
      await Promise.race([
        this._attachSearchDirect(channel.port1),
        search.cancelSignal,
      ]);
      attached = true;
      this._assertOwner(search);
      search.directFinal = { resolve: null, reject: null };
      const finalPromise = new Promise((resolve, reject) => {
        search.directFinal.resolve = resolve;
        search.directFinal.reject = reject;
      });
      const admitted = await this._request(
        { ...payload, action: "start_direct", search_id: search.searchId },
        { timeoutMs: MAX_STEP_TIMEOUT_MS, owner: search },
      );
      if (admitted.event?.kind !== "direct_admitted")
        throw named("WorkerError", "Search worker did not admit direct execution");
      const event = await Promise.race([finalPromise, search.cancelSignal]);
      this._assertOwner(search);
      if (!event || event.search_id !== search.searchId)
        throw named("RESPONSE_MISMATCH", `Direct search returned another search context: ${JSON.stringify(event).slice(0, 300)}`);
      if (event.kind === "cancelled")
        throw search.error ?? named("AbortError", "Direct search was cancelled");
      if (event.kind !== "complete")
        throw named("WorkerError", `Unexpected direct search event: ${event.kind}`);
      this._assertOwner(search);
      search.phase = "completed";
      return this._finalize(search, event, configuration);
    } finally {
      if (attached && typeof this.neural?.detachDirectPort === "function") {
        try {
          await this.neural.detachDirectPort();
        } catch {
          // Bounded detach; a replacement direct attach will be rejected while
          // an in-flight inference still owns the channel.
        }
      }
      channel.port1.close?.();
      channel.port2.close?.();
      if (search.directFinal) search.directFinal = null;
    }
  }

  async _step(search, payload, deadline) {
    const remaining = Math.max(0, deadline + 1000 - performance.now());
    const timeoutMs = Math.min(
      MAX_STEP_TIMEOUT_MS,
      Math.max(MIN_STEP_TIMEOUT_MS, Math.ceil(remaining)),
    );
    const started = performance.now();
    const envelope = await this._request(payload, { timeoutMs, owner: search });
    const elapsed = performance.now() - started;
    search.timings.steps += 1;
    search.timings.step_ms += elapsed;
    const pythonMs = envelope.event?.python?.action_ms;
    if (Number.isFinite(pythonMs)) search.timings.python_ms += pythonMs;
    return envelope.event;
  }

  async _infer(search, request, expectedModel, deadline) {
    if (search.cancelled) throw search.error;
    if (performance.now() > deadline + 1000)
      throw named("TimeoutError", "Search deadline passed before leaf inference");
    const requestId = `${search.searchId}:${request.request_index}`;
    search.inferenceRequestId = requestId;
    const started = performance.now();
    search.inferencePending = true;
    const compact = this.compactInference && typeof this.neural.inferCompact === "function";
    const inference = Promise.resolve(
      compact
        ? this.neural.inferCompact({
            // The encoded planes stay base64 until the inference worker decodes
            // them directly into a Float32Array.
            planes_base64: request.planes,
            size: request.size,
            legal: request.legal,
            ruleset: request.ruleset,
            request_id: requestId,
            position_digest: request.position_digest,
            model: expectedModel,
          })
        : this.neural.infer({
            planes: decodePlanes(request.planes),
            size: request.size,
            legal: request.legal,
            ruleset: request.ruleset,
            request_id: requestId,
            position_digest: request.position_digest,
            model: expectedModel,
          }),
    );
    // The inference promise may settle after cancellation raced it; keep that
    // late settlement from becoming an unhandled rejection.
    inference.catch(() => {});
    let response;
    try {
      response = await Promise.race([inference, search.cancelSignal]);
    } finally {
      search.timings.neural_wait_ms += performance.now() - started;
      search.timings.inferences += 1;
    }
    search.inferencePending = false;
    search.inferenceRequestId = null;
    if (response.request_id !== requestId)
      throw named("RESPONSE_MISMATCH", "Inference response belongs to another request");
    if (response.position_digest !== request.position_digest)
      throw named("RESPONSE_MISMATCH", "Inference response belongs to another position");
    if (!sameArray(response.legal, request.legal))
      throw named(
        "RESPONSE_MISMATCH",
        "Inference response legal actions do not match the requested order",
      );
    if (
      response.model_sha256 !== expectedModel.sha256 ||
      response.model_ruleset !== expectedModel.ruleset
    )
      throw named("MODEL_IDENTITY_MISMATCH", "Inference response came from another model");
    if (Number.isFinite(response.tensor_prepare_ms))
      search.timings.tensor_prepare_ms += response.tensor_prepare_ms;
    if (Number.isFinite(response.run_ms))
      search.timings.inference_execution_ms += response.run_ms;
    if (Number.isFinite(response.extract_ms))
      search.timings.policy_extract_ms += response.extract_ms;
    const payloadMode = response.payload_mode ?? (compact ? "legal-only" : "full-logits");
    if (search.timings.inference_payload_mode === null)
      search.timings.inference_payload_mode = payloadMode;
    else if (search.timings.inference_payload_mode !== payloadMode)
      throw named("RESPONSE_MISMATCH", "Inference payload mode changed during the search");
    search.timings.response_policy_values += Array.isArray(response.policy_logits)
      ? response.policy_logits.length
      : 0;
    if (response.cache_hit === true) search.timings.cache_hits += 1;
    else search.timings.cache_misses += 1;
    const provider = {
      requested: response.provider_requested ?? null,
      execution_placement: response.execution_placement ?? null,
      gpu_execution_verified: response.gpu_execution_verified === true,
    };
    if (search.provider === null) search.provider = provider;
    else if (
      search.provider.requested !== provider.requested ||
      search.provider.execution_placement !== provider.execution_placement
    )
      throw named("RESPONSE_MISMATCH", "Execution provider changed during the search");
    return response;
  }

  _publishProgress(search, event, onProgress) {
    if (!onProgress || !event.progress) return;
    if (!this._owner(search)) return;
    const now = performance.now();
    search.timings.progress_events += 1;
    if (now - (search.lastProgressAt ?? 0) < PROGRESS_INTERVAL_MS) return;
    search.lastProgressAt = now;
    onProgress({
      ...event.progress,
      search_id: search.searchId,
      elapsed_ms: now - search.started,
    });
  }

  _finalize(search, event, configuration) {
    const elapsed = performance.now() - search.started;
    const searchMetrics = { ...event.search };
    const completed = Number(searchMetrics.completed_simulations) || 0;
    const timings = { ...search.timings };
    const unattributed = Math.max(0, elapsed - timings.step_ms - timings.neural_wait_ms);
    return {
      analysis: event.analysis,
      search: {
        ...searchMetrics,
        search_id: search.searchId,
        provider: search.provider,
        coordinator: {
          end_to_end_ms: elapsed,
          completed_simulations: completed,
          simulations_per_second: completed / (elapsed / 1000),
          steps: timings.steps,
          step_ms: timings.step_ms,
          python_action_ms: timings.python_ms,
          // Serialization + Pyodide call boundary between the coordinator and
          // the search worker (inside step_ms, outside python_action_ms).
          bridge_overhead_ms: Math.max(0, timings.step_ms - timings.python_ms),
          neural_wait_ms: timings.neural_wait_ms,
          unattributed_ms: unattributed,
          inferences: timings.inferences,
          progress_events: timings.progress_events,
          // Nested inside neural_wait_ms: tensor preparation and ORT execution.
          tensor_prepare_ms: timings.tensor_prepare_ms,
          inference_execution_ms: timings.inference_execution_ms,
          policy_extract_ms: timings.policy_extract_ms,
          serialize_ms: timings.serialize_ms,
          parse_ms: timings.parse_ms,
          request_bytes: timings.request_bytes,
          response_bytes: timings.response_bytes,
          inference_payload_mode: timings.inference_payload_mode,
          response_policy_values: timings.response_policy_values,
          cache_hits: timings.cache_hits,
          cache_misses: timings.cache_misses,
          cancellation_ms: timings.cancellation_ms,
          direct: this.directInference === true,
          worker_reused: this.info !== null,
        },
      },
      settings: configuration,
      authoritative: false,
    };
  }
}
