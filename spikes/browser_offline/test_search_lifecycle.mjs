// Deterministic coordinator tests for browser neural MCTS. Runs in plain Node
// with a fake search worker and a fake neural client, so transport ownership,
// request association, cancellation, supersession and provider reporting are
// covered without Pyodide, ORT or a browser.
//
// Usage: node spikes/browser_offline/test_search_lifecycle.mjs
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const staging = mkdtempSync(join(tmpdir(), "hnefatafl-search-"));
copyFileSync(
  new URL("./search-client.js", import.meta.url),
  join(staging, "search-client.mjs"),
);
const { NeuralSearchCoordinator, normalizeSearchSettings, watchdogFor } = await import(
  pathToFileURL(join(staging, "search-client.mjs")).href
);

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const MODEL = { sha256: "a".repeat(64), ruleset: "brandubh", size: 7 };
const STATE = newGame();

function newGame() {
  const board = new Array(49).fill(".");
  board[24] = "K";
  for (const index of [1, 5, 7, 13, 35, 41, 43, 47]) board[index] = "A";
  for (const index of [16, 17, 23, 25, 31, 32]) board[index] = "D";
  return {
    ruleset: "brandubh",
    board,
    turn: "attackers",
    ply: 0,
    winner: null,
    reason: null,
    history: [],
  };
}

function evaluateEvent(index, legal) {
  return {
    kind: "evaluate",
    request: {
      request_index: index,
      position_digest: "b".repeat(64),
      ruleset: "brandubh",
      size: 7,
      // 6 * 7 * 7 zero bytes.
      planes: Buffer.alloc(294).toString("base64"),
      legal,
      position: STATE,
    },
    progress: null,
  };
}

function completeEvent() {
  return {
    kind: "complete",
    analysis: {
      value: 0.25,
      source: "neural_search",
      best_move: { source: 1, target: 9 },
      simulations: 8,
    },
    search: {
      completed_simulations: 8,
      requested_simulations: 8,
      neural_requests: 9,
      root_visits: [{ source: 1, target: 9, visits: 8, share: 1 }],
      completion_reason: "simulations",
      provider: null,
    },
  };
}

class FakeSearchWorker {
  constructor() {
    this.messages = [];
    this.terminated = false;
    this.onmessage = null;
    this.onerror = null;
    this.mode = "evaluate-once";
    this.responded = new Set();
    this.cancelAcks = true;
    this.startGate = null;
    this.resumeGate = null;
    this.startSearchId = null;
    this.cancelGate = null;
    this.cancelError = null;
  }
  postMessage(message) {
    this.messages.push(message);
    const payload = JSON.parse(message.payload);
    if (payload.action === "cancel") {
      if (this.cancelError) {
        const failure = this.cancelError;
        setTimeout(
          () => this.respondError(message.id, failure.code, failure.message),
          0,
        );
        return;
      }
      if (!this.cancelAcks) return;
      const ack = () =>
        this.respond(message.id, {
          kind: "cancelled",
          search_id: payload.search_id,
          cancelled: true,
          released: { search_context: true, python_generator: true },
        });
      if (this.cancelGate) this.cancelGate.then(ack);
      else setTimeout(ack, 0);
      return;
    }
    if (payload.action === "parity" || payload.action === "info") return;
    if (payload.action === "start" && this.mode !== "silent") {
      const send = () =>
        this.respond(message.id, {
          ...evaluateEvent(0, [9, 16, 18]),
          search_id: this.startSearchId ?? payload.search_id,
        });
      if (this.startGate) this.startGate.then(send);
      else setTimeout(send, 0);
    } else if (payload.action === "resume" && this.mode !== "silent") {
      const send = () =>
        this.respond(message.id, {
          ...completeEvent(),
          search_id: payload.search_id,
          python: { action_ms: 1.5 },
        });
      if (this.resumeGate) this.resumeGate.then(send);
      else setTimeout(send, 0);
    }
  }
  respond(index, event) {
    if (this.onmessage)
      this.onmessage({ data: { id: index, value: JSON.stringify({ ok: true, event }) } });
  }
  respondError(index, code, message) {
    if (this.onmessage)
      this.onmessage({
        data: { id: index, value: JSON.stringify({ ok: false, code, message }) },
      });
  }
  ready() {
    if (this.onmessage)
      this.onmessage({ data: { id: 0, value: { protocol: 1, pyodide: "0.27.7" } } });
  }
  fail(message = "fake worker failure") {
    if (this.onerror) this.onerror({ message });
  }
  terminate() {
    this.terminated = true;
  }
}

class FakeNeural {
  constructor() {
    this.calls = [];
    this.response = null;
    this.cancels = [];
    this.drainState = "drained";
    this.cancelGate = null;
  }
  modelIdentity() {
    return MODEL;
  }
  cancelRequest({ reason, drainMs }) {
    this.cancels.push({ reason, drainMs });
    const result = {
      state: this.drainState,
      sessionRetained: this.drainState === "drained",
      workerRetained: this.drainState === "drained",
    };
    if (this.cancelGate) return this.cancelGate.then(() => result);
    return Promise.resolve(result);
  }
  infer(request) {
    this.calls.push(request);
    const custom = this.response ? this.response(request) : null;
    if (custom) return custom;
    return Promise.resolve({
      request_id: request.request_id,
      position_digest: request.position_digest,
      model_sha256: MODEL.sha256,
      model_ruleset: MODEL.ruleset,
      legal: request.legal,
      legal_probabilities: request.legal.map(() => 1 / request.legal.length),
      value: 0.25,
      provider_requested: "wasm",
      execution_placement: "wasm",
      gpu_execution_verified: false,
    });
  }
}

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`ok   ${name}`);
  } catch (error) {
    results.push({ name, ok: false, error: String(error?.message ?? error) });
    console.log(`FAIL ${name}: ${error?.message ?? error}`);
  }
}

let workers = [];
function installFakeWorker() {
  workers = [];
  globalThis.Worker = class extends FakeSearchWorker {
    constructor() {
      super();
      workers.push(this);
    }
  };
}

async function bootCoordinator(neural = new FakeNeural()) {
  installFakeWorker();
  const coordinator = new NeuralSearchCoordinator({ neural });
  const loading = coordinator.load();
  await sleep(1);
  workers.at(-1).ready();
  await loading;
  return { coordinator, neural, worker: workers.at(-1) };
}

// ---------------------------------------------------------------- settings

await check("search settings are validated against explicit bounds", () => {
  const settings = normalizeSearchSettings({
    iterations: 256,
    time_limit: 1,
    exploration: 1.4,
    seed: 7,
  });
  assert.deepEqual(settings, {
    algorithm: "mcts",
    iterations: 256,
    time_limit: 1,
    exploration: 1.4,
    seed: 7,
  });
  for (const [input, code] of [
    [{ iterations: 0 }, "SIMULATION_BUDGET"],
    [{ iterations: 4097 }, "SIMULATION_BUDGET"],
    [{ iterations: 1.5 }, "INVALID_SEARCH_CONFIG"],
    [{ time_limit: 0.01 }, "TIME_BUDGET"],
    [{ time_limit: Number.NaN }, "INVALID_SEARCH_CONFIG"],
    [{ exploration: 11 }, "INVALID_SEARCH_CONFIG"],
    [{ seed: -1 }, "INVALID_SEARCH_CONFIG"],
    [{ algorithm: "neural" }, "INVALID_SEARCH_CONFIG"],
    [{ dirichlet_fraction: 0.25 }, "INVALID_SEARCH_CONFIG"],
    [{ depth: 5 }, "INVALID_SEARCH_CONFIG"],
    ["nope", "INVALID_SEARCH_CONFIG"],
  ]) {
    assert.throws(
      () => normalizeSearchSettings(input),
      (error) => error.code === code,
      JSON.stringify(input),
    );
  }
});

await check("watchdog is bounded and follows the cooperative deadline", () => {
  assert.equal(watchdogFor({ time_limit: 0.05 }), 4000);
  assert.equal(watchdogFor({ time_limit: 2 }), 6000);
  assert.equal(watchdogFor({ time_limit: 10 }), 22000);
  assert.equal(watchdogFor({ time_limit: 600 }), 45000);
});

// ---------------------------------------------------------------- search

await check("a sequential search drives one evaluation per pending request", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  const progress = [];
  const result = await coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5, seed: 7 },
    model: MODEL,
    onProgress: (value) => progress.push(value),
  });
  assert.equal(result.analysis.source, "neural_search");
  assert.equal(result.analysis.best_move.source, 1);
  assert.equal(result.search.completed_simulations, 8);
  assert.ok(result.search.coordinator.end_to_end_ms >= 0);
  assert.equal(neural.calls.length, 1);
  assert.equal(neural.calls[0].request_id, `${result.search.search_id}:0`);
  const actions = worker.messages
    .map((message) => JSON.parse(message.payload))
    .filter((payload) => payload.action !== "cancel");
  assert.deepEqual(
    actions.map((payload) => payload.action),
    ["start", "resume"],
  );
  assert.deepEqual(actions[1].priors, neural.calls[0].legal.map(() => 1 / 3));
  coordinator.dispose();
});

await check("inference response association and order are enforced", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  neural.response = (request) => ({
    request_id: request.request_id,
    position_digest: request.position_digest,
    model_sha256: MODEL.sha256,
    model_ruleset: MODEL.ruleset,
    legal: [...request.legal].reverse(),
    legal_probabilities: request.legal.map(() => 1 / 3),
    value: 0.1,
    provider_requested: "wasm",
    execution_placement: "wasm",
  });
  await assert.rejects(
    () =>
      coordinator.search({
        state: STATE,
        settings: { iterations: 8, time_limit: 5 },
        model: MODEL,
      }),
    (error) => error.code === "RESPONSE_MISMATCH",
  );
  // The failed search must not leave a tree waiting for a prediction.
  const cancelled = worker.messages
    .map((message) => JSON.parse(message.payload))
    .some((payload) => payload.action === "cancel");
  assert.equal(cancelled, true);
  coordinator.dispose();
});

await check("a response from another model cannot influence the tree", async () => {
  const { coordinator, neural } = await bootCoordinator();
  neural.response = (request) => ({
    request_id: request.request_id,
    position_digest: request.position_digest,
    model_sha256: "d".repeat(64),
    model_ruleset: MODEL.ruleset,
    legal: request.legal,
    legal_probabilities: request.legal.map(() => 1 / 3),
    value: 0.1,
    provider_requested: "wasm",
    execution_placement: "wasm",
  });
  await assert.rejects(
    () =>
      coordinator.search({
        state: STATE,
        settings: { iterations: 8, time_limit: 5 },
        model: MODEL,
      }),
    (error) => error.code === "MODEL_IDENTITY_MISMATCH",
  );
  coordinator.dispose();
});

await check("a stale prepared model identity is rejected before transport", async () => {
  const { coordinator } = await bootCoordinator();
  await assert.rejects(
    () =>
      coordinator.search({
        state: STATE,
        settings: { iterations: 8, time_limit: 5 },
        model: { sha256: "e".repeat(64), ruleset: "brandubh", size: 7 },
      }),
    (error) => error.code === "MODEL_IDENTITY_MISMATCH",
  );
  assert.equal(coordinator.active, null);
  coordinator.dispose();
});

await check("search error envelopes surface their stable code", async () => {
  const { coordinator, worker } = await bootCoordinator();
  worker.mode = "silent";
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(5);
  const message = worker.messages.find(
    (entry) => JSON.parse(entry.payload).action === "start",
  );
  worker.respondError(message.id, "MODEL_RULESET_MISMATCH", "ruleset mismatch");
  await assert.rejects(started, (error) => error.code === "MODEL_RULESET_MISMATCH");
  coordinator.dispose();
});

// ---------------------------------------------------------------- cancellation

await check("cooperative cancel rejects the search and retains the worker", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  // Hold inference so cancellation lands mid-search.
  let release;
  neural.response = () => new Promise((done) => (release = done));
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  assert.ok(coordinator.active, "search must be active");
  const rejected = assert.rejects(started, (error) => error.name === "AbortError");
  const outcome = await coordinator.cancel("user cancelled");
  assert.equal(outcome.cancelled, true);
  assert.equal(outcome.released.search, true);
  assert.equal(outcome.released.searchWorkerGeneration, 0);
  assert.equal(outcome.released.inference.state, "drained");
  assert.equal(outcome.released.inference.sessionRetained, true);
  await rejected;
  assert.equal(neural.cancels.length, 1, "in-flight inference must be cancelled");
  assert.equal(worker.terminated, false, "a healthy worker is retained");
  const cancels = worker.messages
    .map((message) => JSON.parse(message.payload))
    .filter((payload) => payload.action === "cancel");
  assert.equal(cancels.length, 1);
  if (release) release(new Promise(() => {}));
  await sleep(5);
  coordinator.dispose();
});

await check("cancel with no active search is a no-op", async () => {
  const { coordinator } = await bootCoordinator();
  assert.deepEqual(await coordinator.cancel("nothing"), {
    cancelled: false,
    released: { search: false, inference: { state: "idle", sessionRetained: false } },
  });
  coordinator.dispose();
});

await check("an unacknowledged cancellation terminates the worker", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  worker.cancelAcks = false;
  neural.response = () => new Promise(() => {});
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const outcome = await coordinator.cancel("user cancelled");
  assert.equal(outcome.cancelled, true);
  assert.equal(outcome.released.search, false);
  await assert.rejects(started, (error) => error.name === "AbortError");
  coordinator.dispose();
});

await check("a newer search supersedes the running one", async () => {
  const { coordinator, neural } = await bootCoordinator();
  let release;
  neural.response = () => new Promise((done) => (release = done));
  const first = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  neural.response = null;
  const second = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await assert.rejects(first, (error) => error.name === "AbortError");
  const result = await second;
  assert.equal(result.analysis.source, "neural_search");
  if (release) release(new Promise(() => {}));
  coordinator.dispose();
});

// ---------------------------------------------------------------- failures

await check("a worker crash rejects active work and the next search recovers", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  neural.response = () => new Promise(() => {});
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  worker.fail("worker crashed");
  await assert.rejects(started, (error) => error.name === "WorkerError");
  assert.equal(coordinator.worker, null);
  neural.response = null;
  const loading = coordinator.load();
  await sleep(5);
  workers.at(-1).ready();
  await loading;
  const result = await coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  assert.equal(result.analysis.source, "neural_search");
  coordinator.dispose();
});

await check("dispose rejects active work and terminates the worker", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  neural.response = () => new Promise(() => {});
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  coordinator.dispose();
  await assert.rejects(started, (error) => error.name === "AbortError");
  assert.equal(worker.terminated, true);
  await assert.rejects(
    () =>
      coordinator.search({
        state: STATE,
        settings: { iterations: 8, time_limit: 5 },
        model: MODEL,
      }),
    (error) => error.name === "AbortError",
  );
});

await check("repeated searches reuse one initialized worker", async () => {
  const { coordinator } = await bootCoordinator();
  const before = workers.length;
  for (let index = 0; index < 3; index += 1) {
    const result = await coordinator.search({
      state: STATE,
      settings: { iterations: 8, time_limit: 5 },
      model: MODEL,
    });
    assert.equal(result.search.completed_simulations, 8);
  }
  assert.equal(workers.length, before, "no worker was recreated");
  coordinator.dispose();
});


// ------------------------------------------------- ownership / initialization

await check("cancellation during cold initialization is immediate and recoverable", async () => {
  installFakeWorker();
  const neural = new FakeNeural();
  const coordinator = new NeuralSearchCoordinator({ neural });
  const booting = coordinator.load();
  await sleep(1);
  const worker = workers.at(-1);
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  // Ownership exists before worker readiness.
  assert.ok(coordinator.active, "search ownership must be reserved synchronously");
  await sleep(5);
  const rejected = assert.rejects(started, (error) => error.name === "AbortError");
  const outcome = await coordinator.cancel("cancel during initialization");
  assert.equal(outcome.cancelled, true);
  assert.equal(outcome.released.search, false, "no Python context existed yet");
  await rejected;
  assert.equal(coordinator.active, null);
  assert.equal(worker.terminated, false, "a booting worker is retained for reuse");
  worker.ready();
  await booting;
  // The cancelled search never posted a start request.
  assert.equal(
    worker.messages.some((message) => JSON.parse(message.payload).action === "start"),
    false,
  );
  const result = await coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  assert.equal(result.search.completed_simulations, 8);
  coordinator.dispose();
});

await check("simultaneous initialization waiters cannot both own a search", async () => {
  installFakeWorker();
  const neural = new FakeNeural();
  const coordinator = new NeuralSearchCoordinator({ neural });
  const booting = coordinator.load();
  await sleep(1);
  const worker = workers.at(-1);
  const first = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  const second = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  const firstRejected = assert.rejects(first, (error) => error.name === "AbortError");
  worker.ready();
  await booting;
  await firstRejected;
  const result = await second;
  assert.equal(result.search.completed_simulations, 8);
  const starts = worker.messages
    .map((message) => JSON.parse(message.payload))
    .filter((payload) => payload.action === "start");
  assert.equal(starts.length, 1, "only the owning search may start");
  coordinator.dispose();
});

await check("multiple competing searches leave only the newest owner", async () => {
  const { coordinator, worker } = await bootCoordinator();
  const attempts = [0, 1, 2].map(() =>
    coordinator.search({
      state: STATE,
      settings: { iterations: 8, time_limit: 5 },
      model: MODEL,
    }),
  );
  const rejected = attempts.slice(0, 2).map((attempt) =>
    assert.rejects(attempt, (error) => error.name === "AbortError"),
  );
  await Promise.all(rejected);
  const result = await attempts[2];
  assert.equal(result.search.completed_simulations, 8);
  assert.equal(coordinator.active, null);
  assert.equal(worker.terminated, false);
  coordinator.dispose();
});

await check("a stale start response is rejected, never overwritten", async () => {
  const { coordinator, worker } = await bootCoordinator();
  worker.startSearchId = "s-other-context";
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await assert.rejects(started, (error) => error.code === "RESPONSE_MISMATCH");
  // Association is untrustworthy: the worker is replaced for the next search.
  assert.equal(coordinator.worker, null);
  const loading = coordinator.load();
  await sleep(5);
  workers.at(-1).ready();
  await loading;
  worker.startSearchId = null;
  const result = await coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  assert.equal(result.search.completed_simulations, 8);
  coordinator.dispose();
});

await check("cancellation immediately before result publication publishes nothing", async () => {
  const { coordinator, worker } = await bootCoordinator();
  let release;
  worker.resumeGate = new Promise((done) => (release = done));
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const rejected = assert.rejects(started, (error) => error.name === "AbortError");
  await coordinator.cancel("cancel before publication");
  release();
  await rejected;
  await sleep(20);
  assert.equal(coordinator.active, null);
  coordinator.dispose();
});

await check("delayed obsolete neural responses cannot affect a newer search", async () => {
  const { coordinator, neural } = await bootCoordinator();
  let releaseFirst;
  let first = true;
  neural.response = () => {
    if (!first) return null;
    first = false;
    return new Promise((done) => {
      releaseFirst = () =>
        done({
          request_id: "stale",
          position_digest: "b".repeat(64),
          model_sha256: MODEL.sha256,
          model_ruleset: MODEL.ruleset,
          legal: [],
          legal_probabilities: [],
          value: 0,
        });
    });
  };
  const cancelled = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const rejected = assert.rejects(cancelled, (error) => error.name === "AbortError");
  await coordinator.cancel("cancel while inference pending");
  releaseFirst();
  await rejected;
  neural.response = null;
  const result = await coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  assert.equal(result.search.completed_simulations, 8);
  coordinator.dispose();
});

await check("a neural failure while the generator is suspended releases it", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  neural.response = () =>
    Promise.reject(Object.assign(new Error("neural worker crashed"), { name: "WorkerError" }));
  await assert.rejects(
    () =>
      coordinator.search({
        state: STATE,
        settings: { iterations: 8, time_limit: 5 },
        model: MODEL,
      }),
    (error) => error.name === "WorkerError",
  );
  const cancels = worker.messages
    .map((message) => JSON.parse(message.payload))
    .filter((payload) => payload.action === "cancel");
  assert.equal(cancels.length, 1, "the suspended search context is cancelled");
  neural.response = null;
  const result = await coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  assert.equal(result.search.completed_simulations, 8);
  coordinator.dispose();
});

await check("a search worker failure during inference cancels the neural request", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  let release;
  neural.response = () => new Promise((done) => (release = done));
  const started = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const rejected = assert.rejects(started, (error) => error.name === "WorkerError");
  worker.fail("search worker crashed during inference");
  await rejected;
  assert.equal(neural.cancels.length, 1);
  if (release) release(new Promise(() => {}));
  coordinator.dispose();
});

await check("repeated cancellation and recovery keeps working", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  for (let index = 0; index < 3; index += 1) {
    let release;
    neural.response = () => new Promise((done) => (release = done));
    const started = coordinator.search({
      state: STATE,
      settings: { iterations: 8, time_limit: 5 },
      model: MODEL,
    });
    await sleep(5);
    const rejected = assert.rejects(started, (error) => error.name === "AbortError");
    await coordinator.cancel(`cancel ${index}`);
    if (release) release(new Promise(() => {}));
    await rejected;
    neural.response = null;
    const result = await coordinator.search({
      state: STATE,
      settings: { iterations: 8, time_limit: 5 },
      model: MODEL,
    });
    assert.equal(result.search.completed_simulations, 8);
  }
  assert.equal(worker.terminated, false, "healthy workers survive repeated cancellation");
  coordinator.dispose();
});

await check("a newer search survives a failed cancellation of the previous one", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  let release;
  neural.response = () => new Promise((done) => (release = done));
  const first = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const rejected = assert.rejects(first, (error) => error.name === "AbortError");
  // The old worker refuses the cooperative cancel; the newer search must still
  // run on a replacement worker and not be terminated by the old search.
  worker.cancelAcks = false;
  neural.response = null;
  const second = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(2800); // bounded cancellation acknowledgement deadline elapses
  const replacement = workers.at(-1);
  if (replacement !== worker) replacement.ready();
  const result = await second;
  await rejected;
  assert.equal(result.search.completed_simulations, 8);
  assert.equal(worker.terminated, true, "the unresponsive old worker is replaced");
  assert.notEqual(coordinator.worker, null);
  if (release) release(new Promise(() => {}));
  coordinator.dispose();
});

function defer() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function startMessages(worker) {
  return worker.messages
    .map((message) => JSON.parse(message.payload))
    .filter((payload) => payload.action === "start");
}

await check("cascading admissions inherit all outstanding cleanup", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  let blocked = true;
  neural.response = () => {
    if (!blocked) return null;
    blocked = false;
    return new Promise(() => {});
  };
  const ack = defer();
  const drain = defer();
  worker.cancelGate = ack.promise;
  neural.cancelGate = drain.promise;
  const first = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const firstRejected = assert.rejects(first, (error) => error.name === "AbortError");
  const second = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  const secondRejected = assert.rejects(second, (error) => error.name === "AbortError");
  await sleep(5);
  const third = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(20);
  // The final owner has not started while A's acknowledgement and drain are
  // still outstanding, and B's cancellation did not disturb A's request.
  assert.equal(startMessages(worker).length, 1, "only the first search has started");
  ack.resolve();
  await sleep(20);
  assert.equal(neural.cancels.length, 1, "only the owner's inference is drained");
  assert.equal(startMessages(worker).length, 1, "the final owner waits for the drain");
  drain.resolve();
  const result = await third;
  await firstRejected;
  await secondRejected;
  assert.equal(result.search.completed_simulations, 8);
  assert.equal(coordinator.active, null);
  coordinator.dispose();
});

await check("a delayed cancellation acknowledgement delays, then permits, the next search", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  let blocked = true;
  neural.response = () => {
    if (!blocked) return null;
    blocked = false;
    return new Promise(() => {});
  };
  const ack = defer();
  worker.cancelGate = ack.promise;
  const first = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const firstRejected = assert.rejects(first, (error) => error.name === "AbortError");
  const second = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(20);
  assert.equal(startMessages(worker).length, 1);
  ack.resolve();
  const result = await second;
  await firstRejected;
  assert.equal(result.search.completed_simulations, 8);
  coordinator.dispose();
});

await check("cancellation acknowledgement failure recovers on a fresh worker", async () => {
  const { coordinator, neural, worker } = await bootCoordinator();
  const workerCount = workers.length;
  let blocked = true;
  neural.response = () => {
    if (!blocked) return null;
    blocked = false;
    return new Promise(() => {});
  };
  worker.cancelError = { code: "WORKER_BUSY", message: "cancel rejected" };
  const first = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const firstRejected = assert.rejects(first, (error) => error.name === "AbortError");
  const second = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  const secondRejected = assert.rejects(second, (error) => error.name === "AbortError");
  await sleep(5);
  const third = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  // The final owner replaces the unhealthy worker before submitting work.
  for (let attempt = 0; attempt < 50 && workers.length === workerCount; attempt += 1)
    await sleep(5);
  assert.equal(workers.length, workerCount + 1, "a fresh worker is created");
  assert.equal(worker.terminated, true, "the unhealthy worker is terminated");
  workers.at(-1).ready();
  const result = await third;
  await firstRejected;
  await secondRejected;
  assert.equal(result.search.completed_simulations, 8);
  coordinator.dispose();
});

await check("a failed inference drain does not block chained cleanup", async () => {
  const { coordinator, neural } = await bootCoordinator();
  let blocked = true;
  neural.response = () => {
    if (!blocked) return null;
    blocked = false;
    return new Promise(() => {});
  };
  neural.cancelRequest = () => Promise.reject(new Error("drain failed"));
  const first = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  await sleep(10);
  const firstRejected = assert.rejects(first, (error) => error.name === "AbortError");
  const second = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  const secondRejected = assert.rejects(second, (error) => error.name === "AbortError");
  const third = coordinator.search({
    state: STATE,
    settings: { iterations: 8, time_limit: 5 },
    model: MODEL,
  });
  const result = await third;
  await firstRejected;
  await secondRejected;
  assert.equal(result.search.completed_simulations, 8);
  coordinator.dispose();
});

await check("a neural search must supply the prepared model identity explicitly", async () => {
  const { coordinator, worker } = await bootCoordinator();
  const posted = worker.messages.length;
  await assert.rejects(
    async () =>
      coordinator.search({
        state: STATE,
        settings: { iterations: 8, time_limit: 5 },
      }),
    (error) => error.code === "MODEL_IDENTITY_REQUIRED",
  );
  assert.equal(worker.messages.length, posted, "no request may reach the worker");
  coordinator.dispose();
});

const failures = results.filter((row) => !row.ok);
console.log(`\n${results.length - failures.length}/${results.length} search checks passed`);
if (failures.length) process.exit(1);
