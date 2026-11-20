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
  }
  postMessage(message) {
    this.messages.push(message);
    const payload = JSON.parse(message.payload);
    if (payload.action === "cancel") {
      if (!this.cancelAcks) return;
      setTimeout(() => this.respond(message.id, { kind: "cancelled", cancelled: true }), 0);
      return;
    }
    if (payload.action === "parity" || payload.action === "info") return;
    if (payload.action === "start" && this.mode !== "silent") {
      setTimeout(
        () => this.respond(message.id, evaluateEvent(0, [9, 16, 18])),
        0,
      );
    } else if (payload.action === "resume" && this.mode !== "silent") {
      setTimeout(
        () =>
          this.respond(message.id, {
            ...completeEvent(),
            python: { action_ms: 1.5 },
          }),
        0,
      );
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
  }
  modelIdentity() {
    return MODEL;
  }
  infer(request) {
    this.calls.push(request);
    if (this.response) return this.response(request);
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
  const outcome = await coordinator.cancel("user cancelled");
  assert.deepEqual(outcome, { cancelled: true, cooperative: true });
  await assert.rejects(started, (error) => error.name === "AbortError");
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
  assert.deepEqual(await coordinator.cancel("nothing"), { cancelled: false });
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
  assert.deepEqual(outcome, { cancelled: true, cooperative: false });
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

const failures = results.filter((row) => !row.ok);
console.log(`\n${results.length - failures.length}/${results.length} search checks passed`);
if (failures.length) process.exit(1);
