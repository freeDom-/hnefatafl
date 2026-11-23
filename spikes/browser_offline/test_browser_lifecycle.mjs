// Deterministic lifecycle tests for the isolated browser clients. Runs in plain
// Node with a fake Worker, so it exercises generation guards, timer cleanup,
// cancellation, download admission and provider reporting without a browser.
//
// Usage: node spikes/browser_offline/test_browser_lifecycle.mjs
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// The isolated clients are browser ES modules with a `.js` extension. Node
// treats `.js` as CommonJS when no package type is declared, so import temporary
// `.mjs` copies to keep this test independent of the Node version and of
// `--experimental-detect-module`.
const staging = mkdtempSync(join(tmpdir(), "hnefatafl-lifecycle-"));
for (const name of ["analysis-client.js", "neural-client.js"]) {
  copyFileSync(
    new URL(`./${name}`, import.meta.url),
    join(staging, name.replace(/\.js$/, ".mjs")),
  );
}
const { LocalAnalysisClient } = await import(
  pathToFileURL(join(staging, "analysis-client.mjs")).href
);
const { NeuralClient } = await import(pathToFileURL(join(staging, "neural-client.mjs")).href);
// neural-provider.js is a classic (CommonJS) script; load it through require.
const provider = createRequire(import.meta.url)("./neural-provider.js");

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

class FakeWorker {
  constructor() {
    this.messages = [];
    this.terminated = false;
    this.onmessage = null;
    this.onerror = null;
    this.listeners = new Set();
  }
  addEventListener(type, listener) {
    if (type === "message") this.listeners.add(listener);
  }
  removeEventListener(type, listener) {
    if (type === "message") this.listeners.delete(listener);
  }
  dispatch(data) {
    this.onmessage?.({ data });
    for (const listener of [...this.listeners]) listener({ data });
  }
  postMessage(message) {
    this.messages.push(message);
  }
  terminate() {
    this.terminated = true;
  }
  respond(index, value, error = null, code = null) {
    const message = this.messages[index];
    this.dispatch({ id: message.id, value, error, code });
  }
  fail(message = "fake worker failure") {
    if (this.onerror) this.onerror({ message });
  }
}

const results = [];
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      results.push({ name, ok: true });
      console.log(`ok   ${name}`);
    })
    .catch((error) => {
      results.push({ name, ok: false, error: String(error?.message ?? error) });
      console.log(`FAIL ${name}: ${error?.message ?? error}`);
    });
}

// ---------- provider placement reporting (review item 5) ----------
await check("wasm provider reports wasm placement", () => {
  assert.equal(provider.placementFor("wasm", null), "wasm");
  assert.equal(provider.placementFor("wasm", ["wasm"]), "wasm");
  assert.equal(provider.gpuVerified("wasm"), false);
});
await check("webgpu without evidence is unverified, not GPU", () => {
  const placement = provider.placementFor("webgpu", null);
  assert.equal(placement, "webgpu-session-unverified");
  assert.equal(provider.gpuVerified(placement), false);
  assert.notEqual(placement, "wasm");
});
await check("webgpu-only evidence is unverified, not proven GPU execution", () => {
  const placement = provider.placementFor("webgpu", ["webgpu"]);
  assert.equal(placement, "webgpu-session-unverified");
  assert.equal(provider.gpuVerified(placement), false);
  assert.equal(provider.perOperatorPlacementAvailable(), false);
  // Even an explicit verified-looking placement is never claimed without a
  // supported per-operator diagnostic.
  assert.equal(provider.gpuVerified("webgpu-session-verified"), false);
});
await check("webgpu session falling back to wasm is reported as cpu-fallback", () => {
  assert.equal(provider.placementFor("webgpu", ["wasm"]), "cpu-fallback");
  assert.equal(provider.gpuVerified("cpu-fallback"), false);
});
await check("mixed provider lists are mixed, not CPU-only or verified GPU", () => {
  assert.equal(provider.placementFor("webgpu", ["webgpu", "wasm"]), "webgpu-mixed");
  assert.equal(provider.gpuVerified("webgpu-mixed"), false);
  assert.equal(provider.placementFor("webgpu", ["foo"]), "placement-unknown");
  assert.equal(provider.gpuVerified("placement-unknown"), false);
});
await check("providerEvidence reads known session surfaces only", () => {
  assert.equal(provider.providerEvidence({ executionProviders: ["webgpu"] })[0], "webgpu");
  assert.equal(provider.providerEvidence({}), null);
  assert.equal(provider.providerEvidence(null), null);
});

// ---------- classical analysis client watchdog selection (review item 3) ----------
await check("analysis watchdog covers a legitimate 10 second search", () => {
  const ten = LocalAnalysisClient.watchdogFor({ time_limit: 10 });
  assert.ok(ten >= 10000, `watchdog ${ten} must permit a 10 s search`);
  assert.ok(ten <= 20000, `watchdog ${ten} must stay bounded`);
  const small = LocalAnalysisClient.watchdogFor({ time_limit: 0.02 });
  assert.ok(small >= 5000 && small <= 6000, `tiny watchdog ${small} must stay near the 5 s floor`);
});
await check("analysis request rejects a deadline above its ceiling", async () => {
  globalThis.Worker = FakeWorker;
  const client = new LocalAnalysisClient();
  await assert.rejects(
    () => client.request({ action: "info" }, { timeoutMs: 5001 }),
    /deadline must be between 1 and 5000/,
  );
  client.dispose();
});
await check("classical cancel keeps foreground priority and rejects as AbortError", async () => {
  globalThis.Worker = FakeWorker;
  const client = new LocalAnalysisClient();
  const worker = (client.load(), client.worker);
  await Promise.resolve();
  worker.onmessage?.({ data: { id: 0, value: { ready: true } } });
  const pending = client.request({ action: "probe" }, { timeoutMs: 1000 });
  await sleep(5);
  client.cancel("test cancel");
  await assert.rejects(pending, (error) => error.name === "AbortError");
  client.dispose();
});

await check("classical hard timeout rejects and the next request recovers", async () => {
  globalThis.Worker = FakeWorker;
  const client = new LocalAnalysisClient();
  const worker = (client.load(), client.worker);
  await Promise.resolve();
  worker.onmessage?.({ data: { id: 0, value: { ready: true } } });
  await assert.rejects(
    () => client.request({ action: "probe" }, { timeoutMs: 20 }),
    (error) => error.name === "TimeoutError",
  );
  assert.equal(client.worker, null, "a timed-out worker must be released");
  const next = client.request({ action: "info" }, { timeoutMs: 500 });
  await sleep(5);
  // The replacement worker must complete its startup handshake first.
  client.worker.onmessage?.({ data: { id: 0, value: { ready: true } } });
  await sleep(5);
  client.worker.onmessage?.({ data: { id: client.pending.id, value: { ok: true } } });
  assert.deepEqual(await next, { ok: true });
  client.dispose();
});
await check("classical dispose rejects active work and clears timers", async () => {
  globalThis.Worker = FakeWorker;
  const client = new LocalAnalysisClient();
  const worker = (client.load(), client.worker);
  await Promise.resolve();
  worker.onmessage?.({ data: { id: 0, value: { ready: true } } });
  const pending = client.request({ action: "probe" }, { timeoutMs: 5000 });
  await sleep(5);
  client.dispose();
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(client.pending, null);
});

// ---------- neural client lifecycle (review item 6) ----------
function fakeFetchFailure() {
  globalThis.fetch = async () => ({ ok: false, status: 404, headers: new Map() });
}

await check("neural terminate rejects the active promise with AbortError", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  const pending = client.request({ action: "infer" }, { timeoutMs: 5000 });
  await sleep(5);
  client.terminate("user cancelled");
  await assert.rejects(pending, (error) => error.name === "AbortError");
});
await check("neural hard timeout rejects with TimeoutError and recovers", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  await assert.rejects(
    () => client.request({ action: "infer" }, { timeoutMs: 20 }),
    (error) => error.name === "TimeoutError",
  );
  const worker = client.worker;
  assert.equal(worker, null, "a timed-out worker must be released");
  const next = client.request({ action: "infer" }, { timeoutMs: 500 });
  await sleep(5);
  client.worker.respond(0, { value: 1 });
  assert.deepEqual(await next, { value: 1 });
  client.terminate();
});
await check("obsolete neural timeout cannot abort a replacement worker", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  const first = client.request({ action: "infer" }, { timeoutMs: 20 });
  await sleep(5);
  // Recreate the transport before the first timer fires.
  client.abort(new Error("replace"));
  await assert.rejects(first, /replace/);
  const second = client.request({ action: "infer" }, { timeoutMs: 2000 });
  await sleep(60); // past the original 20 ms timer
  assert.ok(client.worker, "replacement worker must survive the obsolete timer");
  client.worker.respond(0, { value: 2 });
  assert.deepEqual(await second, { value: 2 });
  client.terminate();
});
await check("stale worker errors are ignored after replacement", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  const first = client.request({ action: "infer" }, { timeoutMs: 2000 });
  await sleep(5);
  const stale = client.worker;
  client.abort(new Error("replaced"));
  await assert.rejects(first, /replaced/);
  const second = client.request({ action: "infer" }, { timeoutMs: 2000 });
  await sleep(5);
  stale.fail("late crash from the old worker");
  assert.ok(client.worker, "a stale worker error must not kill the replacement");
  client.worker.respond(0, { value: 3 });
  assert.deepEqual(await second, { value: 3 });
  client.terminate();
});
await check("a failed model load never leaves a model active", async () => {
  globalThis.Worker = FakeWorker;
  fakeFetchFailure();
  const client = new NeuralClient();
  await assert.rejects(
    () => client.load("./model.onnx", { provider: "wasm", size: 7, expectedSha256: "0".repeat(64) }),
    (error) => error.name === "ModelLoadError",
  );
  assert.equal(client.model, null);
  client.terminate();
});
await check("concurrent model downloads are explicitly rejected", async () => {
  globalThis.Worker = FakeWorker;
  let release;
  globalThis.fetch = () =>
    new Promise((done) => {
      release = () =>
        done({ ok: true, headers: new Map(), arrayBuffer: async () => new ArrayBuffer(8) });
    });
  const client = new NeuralClient();
  const first = client.fetchModel("./model.onnx");
  await sleep(5);
  await assert.rejects(() => client.fetchModel("./model.onnx"), /already in progress/);
  release();
  await first;
  client.terminate();
});
await check("successful load records the model, provider switch recreates", async () => {
  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map([["content-length", "8"]]),
    arrayBuffer: async () => new Uint8Array(8).buffer,
  });
  const client = new NeuralClient();
  const load = client.load("./model.onnx", { provider: "wasm", size: 7 });
  await sleep(5);
  client.worker.respond(0, {
    provider_requested: "wasm",
    execution_placement: "wasm",
    gpu_execution_verified: false,
    model_sha256: "0".repeat(64),
  });
  const loaded = await load;
  assert.equal(loaded.execution_placement, "wasm");
  assert.ok(client.model);
  // Provider switch must terminate and recreate instead of reusing the worker.
  const oldWorker = client.worker;
  const switched = client.load("./model.onnx", { provider: "webgpu", size: 7 }).catch(() => null);
  await sleep(5);
  assert.equal(oldWorker.terminated, true, "provider switch must recreate the worker");
  assert.notEqual(client.worker, oldWorker);
  client.terminate();
  await switched;
});
await check("a failed replacement load clears the previously active model", async () => {
  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map([["content-length", "8"]]),
    arrayBuffer: async () => new Uint8Array(8).buffer,
  });
  const client = new NeuralClient();
  client.model = { provider: "wasm", digest: "old" };
  const replacement = client.load("./model.onnx", { provider: "wasm", size: 7 });
  await sleep(5);
  client.worker.respond(0, null, "session creation failed", "SESSION_CREATE_FAILED");
  await assert.rejects(replacement, /session creation failed/);
  assert.equal(client.model, null, "a failed replacement must not expose the old model");
  client.terminate();
});
await check("release clears the active model", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client._spawn();
  client.model = { provider: "wasm" };
  const pending = client.release();
  await sleep(5);
  client.worker.respond(0, { released: true });
  await pending;
  assert.equal(client.model, null);
  client.terminate();
});

await check("cancellation during digest verification prevents publication", async () => {
  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map(),
    arrayBuffer: async () => new Uint8Array(8).buffer,
  });
  const client = new NeuralClient();
  let releaseDigest;
  client.digestFn = () =>
    new Promise((resolve) => {
      releaseDigest = resolve;
    });
  const loading = client.load("./model.onnx", {
    provider: "wasm",
    size: 7,
    expectedSha256: "a".repeat(64),
  });
  await sleep(5);
  client.terminate("cancel during digest");
  await assert.rejects(loading, (error) => error.name === "AbortError");
  if (releaseDigest) releaseDigest("a".repeat(64));
  await sleep(10);
  assert.equal(client.model, null, "cancelled load must not publish a model");
  assert.equal(client.worker, null, "cancelled load must not keep a worker");
});
await check("competing model loads have deterministic admission", async () => {
  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map(),
    arrayBuffer: async () => new Uint8Array(8).buffer,
  });
  const client = new NeuralClient();
  client.digestFn = () => new Promise(() => {});
  const first = client.load("./model.onnx", {
    provider: "wasm",
    size: 7,
    expectedSha256: "a".repeat(64),
  });
  await sleep(5);
  await assert.rejects(
    () =>
      client.load("./model.onnx", { provider: "wasm", size: 7, expectedSha256: "a".repeat(64) }),
    /already in progress/,
  );
  client.terminate("stop");
  await assert.rejects(first, (error) => error.name === "AbortError");
});
await check("a failed WASM load does not block a later WebGPU worker", async () => {
  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map(),
    arrayBuffer: async () => new Uint8Array(8).buffer,
  });
  const client = new NeuralClient();
  const wasmLoad = client.load("./model.onnx", { provider: "wasm", size: 7 });
  await sleep(5);
  const wasmWorker = client.worker;
  wasmWorker.respond(0, null, "session creation failed", "SESSION_CREATE_FAILED");
  await assert.rejects(wasmLoad, /session creation failed/);
  assert.equal(client.model, null);
  const webgpu = client.load("./model.onnx", { provider: "webgpu", size: 7 });
  await sleep(5);
  assert.equal(wasmWorker.terminated, true, "provider change must recreate the worker");
  client.worker.respond(0, {
    provider_requested: "webgpu",
    execution_placement: "webgpu-session-unverified",
    gpu_execution_verified: false,
    model_sha256: "a".repeat(64),
  });
  const loaded = await webgpu;
  assert.equal(loaded.execution_placement, "webgpu-session-unverified");
  client.terminate();
});
await check("an oversized response without Content-Length is rejected", async () => {
  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map(),
    body: {
      getReader() {
        let sent = false;
        return {
          async read() {
            if (sent) return { done: true };
            sent = true;
            return { done: false, value: new Uint8Array(70 * 1024 * 1024) };
          },
        };
      },
    },
  });
  const client = new NeuralClient();
  await assert.rejects(
    () => client.fetchModel("./model.onnx"),
    (error) => error.name === "ModelLoadError" && /size limit/.test(error.message),
  );
  client.terminate();
});
await check("worker creation after an obsolete operation was cancelled", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.terminate("cancelled before any work");
  const capability = client.capabilities();
  await sleep(5);
  client.worker.respond(0, { webGPUAdapter: false });
  const value = await capability;
  assert.equal(value.webGPUAdapter, false);
  client.terminate();
});

await check("a request prepared for model A cannot execute against model B", async () => {
  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map(),
    arrayBuffer: async () => new Uint8Array(8).buffer,
  });
  const client = new NeuralClient();
  client.digestFn = async () => "a".repeat(64);
  const loadA = client.load("./model.onnx", {
    provider: "wasm",
    size: 7,
    ruleset: "brandubh",
    expectedSha256: "a".repeat(64),
  });
  await sleep(5);
  client.worker.respond(0, {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    execution_placement: "wasm",
    gpu_execution_verified: false,
  });
  await loadA;
  const identityA = client.modelIdentity();
  client.digestFn = async () => "b".repeat(64);
  const loadB = client.load("./model.onnx", {
    provider: "wasm",
    size: 7,
    ruleset: "brandubh",
    expectedSha256: "b".repeat(64),
  });
  await sleep(5);
  client.worker.respond(1, {
    model_sha256: "b".repeat(64),
    model_ruleset: "brandubh",
    execution_placement: "wasm",
    gpu_execution_verified: false,
  });
  await loadB;
  assert.equal(client.modelIdentity().sha256, "b".repeat(64));
  const posted = client.worker.messages.length;
  const planes = new Array(6 * 7 * 7).fill(0);
  await assert.rejects(
    async () =>
      client.infer({
        planes,
        size: 7,
        legal: [0],
        ruleset: "brandubh",
        request_id: "p0",
        position_digest: "c".repeat(64),
        model: identityA,
      }),
    (error) => error.code === "MODEL_IDENTITY_MISMATCH",
  );
  assert.equal(client.worker.messages.length, posted, "stale identity must not reach the worker");
  const request = client.infer({
    planes,
    size: 7,
    legal: [0],
    ruleset: "brandubh",
    request_id: "p0",
    position_digest: "c".repeat(64),
    model: client.modelIdentity(),
  });
  await sleep(5);
  client.worker.respond(client.worker.messages.length - 1, {
    request_id: "p0",
    position_digest: "c".repeat(64),
    model_sha256: "b".repeat(64),
    legal: [0],
    legal_probabilities: [1],
    policy_logits: [0],
    value: 0,
    policy_shape: [1, 2401],
    value_shape: [1],
    policy_dtype: "float32",
    value_dtype: "float32",
  });
  await request;
  client.terminate();
});
await check("an incompatible batched request identity is rejected", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
    provider: "wasm",
  };
  await assert.rejects(
    async () =>
      client.inferMany({
        cases: [{ planes: [], legal: [0] }],
        model: { sha256: "a".repeat(64), ruleset: "tablut", size: 9 },
      }),
    (error) => error.code === "RULESET_MISMATCH" || error.code === "BOARD_SIZE_MISMATCH",
  );
  client.terminate();
});

await check("benchmark and sustained require an explicit model identity", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  const A = "a".repeat(64);
  const B = "b".repeat(64);
  client.model = { model_sha256: A, model_ruleset: "brandubh", size: 7, provider: "wasm" };
  const cases = [{ planes: [], legal: [0], request_id: "p0", position_digest: "c".repeat(64) }];
  const posted = () => client.worker.messages.length;
  await assert.rejects(
    async () => client.benchmark({ cases, batchSizes: [1] }),
    (error) => error.code === "MODEL_IDENTITY_REQUIRED",
  );
  await assert.rejects(
    async () => client.sustained({ cases, iterations: 1 }),
    (error) => error.code === "MODEL_IDENTITY_REQUIRED",
  );
  const before = client.worker ? posted() : 0;
  await assert.rejects(
    async () =>
      client.benchmark({
        cases,
        batchSizes: [1],
        model: { sha256: B, ruleset: "brandubh", size: 7 },
      }),
    (error) => error.code === "MODEL_IDENTITY_MISMATCH",
  );
  assert.equal(client.worker ? posted() : 0, before, "missing identity must not reach the worker");
  // A valid identity still reaches the worker and completes.
  const valid = { sha256: A, ruleset: "brandubh", size: 7 };
  const benchmark = client.benchmark({ cases, batchSizes: [1], model: valid });
  await sleep(5);
  client.worker.respond(client.worker.messages.length - 1, { rows: [] });
  assert.deepEqual(await benchmark, { rows: [] });
  const sustained = client.sustained({ cases, iterations: 1, model: valid });
  await sleep(5);
  client.worker.respond(client.worker.messages.length - 1, { iterations: 1 });
  assert.deepEqual(await sustained, { iterations: 1 });
  client.terminate();
});

await check("a cancelled inference drains and retains a healthy session", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.model = { provider: "wasm", model_sha256: "a".repeat(64) };
  const pending = client.request({ action: "infer" }, { timeoutMs: 5000 });
  await sleep(5);
  const worker = client.worker;
  const rejected = assert.rejects(pending, (error) => error.name === "AbortError");
  setTimeout(() => worker.respond(0, { ok: true }), 10);
  const outcome = await client.cancelRequest({ reason: "cancel", drainMs: 500 });
  await rejected;
  assert.equal(outcome.state, "drained");
  assert.equal(outcome.sessionRetained, true);
  assert.equal(outcome.workerRetained, true);
  assert.equal(client.busy, false);
  assert.ok(client.model, "the model session is retained");
  // The drained worker accepts the next request.
  const next = client.request({ action: "infer" }, { timeoutMs: 500 });
  await sleep(5);
  client.worker.respond(1, { ok: true });
  assert.deepEqual(await next, { ok: true });
  client.terminate();
});
await check("an unresponsive inference is terminated after the drain deadline", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.model = { provider: "wasm", model_sha256: "a".repeat(64) };
  const pending = client.request({ action: "infer" }, { timeoutMs: 5000 });
  await sleep(5);
  const worker = client.worker;
  const rejected = assert.rejects(pending, (error) => error.name === "AbortError");
  const outcome = await client.cancelRequest({ reason: "cancel", drainMs: 20 });
  await rejected;
  assert.equal(outcome.state, "terminated");
  assert.equal(outcome.sessionRetained, false);
  assert.equal(worker.terminated, true);
  assert.equal(client.model, null);
  client.terminate();
});
await check("a replacement inference is refused while draining", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.draining = Promise.resolve();
  await assert.rejects(
    async () => client.request({ action: "infer" }),
    /still draining/,
  );
  client.draining = null;
  client.terminate();
});
await check("cancelWork aborts an owned model load without publishing it", async () => {
  globalThis.Worker = FakeWorker;
  let releaseFetch;
  globalThis.fetch = () =>
    new Promise((done) => {
      releaseFetch = () => done({ ok: true, headers: new Map(), arrayBuffer: async () => new ArrayBuffer(8) });
    });
  const client = new NeuralClient();
  const loading = client.load("./model.onnx", { provider: "wasm", size: 7 });
  await sleep(5);
  const outcome = await client.cancelWork({ reason: "cancel during load" });
  assert.equal(outcome.state, "load-aborted");
  assert.equal(outcome.sessionRetained, false);
  await assert.rejects(loading, (error) => error.name === "AbortError");
  assert.equal(client.model, null);
  if (releaseFetch) releaseFetch();
  await sleep(10);
  assert.equal(client.model, null, "a cancelled load never publishes a session");
  client.terminate();
});
await check("cancelRequest on an idle client reports session retention", async () => {
  const client = new NeuralClient();
  client.model = { provider: "wasm" };
  const outcome = await client.cancelRequest();
  assert.deepEqual(outcome, {
    state: "idle",
    sessionRetained: true,
    workerRetained: false,
  });
  client.terminate();
});

async function abandonedScenario(outcomeError, outcomeCode) {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.model = { provider: "wasm", model_sha256: "a".repeat(64) };
  const pending = client.request({ action: "infer" }, { timeoutMs: 5000 });
  await sleep(5);
  const worker = client.worker;
  const rejected = assert.rejects(pending, (error) => error.name === "AbortError");
  const draining = client.cancelRequest({ reason: "cancel", drainMs: 500 });
  worker.respond(0, null, outcomeError, outcomeCode);
  const outcome = await draining;
  await rejected;
  return { client, worker, outcome };
}

await check("an abandoned request that succeeds drains and retains the session", async () => {
  const { client, outcome } = await abandonedScenario(undefined, undefined);
  assert.equal(outcome.state, "drained");
  assert.equal(outcome.outcome, "completed");
  assert.equal(outcome.sessionRetained, true);
  const next = client.request({ action: "infer" }, { timeoutMs: 500 });
  await sleep(5);
  client.worker.respond(1, { ok: true });
  assert.deepEqual(await next, { ok: true });
  client.terminate();
});
await check("an abandoned RESOURCE_CLEANUP_FAILED invalidates the session", async () => {
  const { client, worker, outcome } = await abandonedScenario(
    "tensor cleanup failed",
    "RESOURCE_CLEANUP_FAILED",
  );
  assert.equal(outcome.state, "terminated");
  assert.equal(outcome.sessionRetained, false);
  assert.equal(outcome.outcome, "failed");
  assert.equal(outcome.errorCode, "RESOURCE_CLEANUP_FAILED");
  assert.equal(worker.terminated, true);
  assert.equal(client.model, null);
  // Bounded recovery: a fresh worker/session can be loaded and used.
  globalThis.fetch = async () => ({
    ok: true,
    headers: new Map([["content-length", "8"]]),
    arrayBuffer: async () => new Uint8Array(8).buffer,
  });
  client.digestFn = async () => "a".repeat(64);
  const loading = client.load("./model.onnx", {
    provider: "wasm",
    size: 7,
    ruleset: "brandubh",
    expectedSha256: "a".repeat(64),
  });
  await sleep(5);
  client.worker.respond(0, {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    execution_placement: "wasm",
    gpu_execution_verified: false,
  });
  await loading;
  assert.ok(client.model);
  client.terminate();
});
await check("an abandoned SESSION_RELEASE_FAILED invalidates the session", async () => {
  const { client, outcome } = await abandonedScenario(
    "session release failed",
    "SESSION_RELEASE_FAILED",
  );
  assert.equal(outcome.state, "terminated");
  assert.equal(outcome.sessionRetained, false);
  assert.equal(outcome.errorCode, "SESSION_RELEASE_FAILED");
  assert.equal(client.model, null);
  client.terminate();
});
await check("an abandoned generic inference error drains and keeps the worker", async () => {
  const { client, worker, outcome } = await abandonedScenario(
    "model produced a non-finite logit",
    "OUTPUT_INVALID",
  );
  assert.equal(outcome.state, "drained");
  assert.equal(outcome.outcome, "failed");
  assert.equal(outcome.errorCode, "OUTPUT_INVALID");
  assert.equal(outcome.sessionRetained, true);
  assert.equal(worker.terminated, false);
  const next = client.request({ action: "infer" }, { timeoutMs: 500 });
  await sleep(5);
  client.worker.respond(1, { ok: true });
  assert.deepEqual(await next, { ok: true });
  client.terminate();
});
await check("a delayed obsolete-generation response cannot affect a replacement", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.model = { provider: "wasm" };
  const pending = client.request({ action: "infer" }, { timeoutMs: 5000 });
  await sleep(5);
  const staleWorker = client.worker;
  const rejected = assert.rejects(pending, (error) => error.name === "AbortError");
  const outcome = await client.cancelRequest({ reason: "cancel", drainMs: 20 });
  await rejected;
  assert.equal(outcome.state, "terminated");
  const next = client.request({ action: "infer" }, { timeoutMs: 500 });
  await sleep(5);
  // A late message from the replaced worker is ignored by the generation guard.
  assert.ok(client.pending, "the replacement request is still pending");
  staleWorker.onmessage?.({
    data: { id: staleWorker.messages[0].id, value: null, error: "late failure" },
  });
  assert.ok(client.pending, "a stale response cannot settle the replacement");
  client.worker.respond(0, { ok: true });
  assert.deepEqual(await next, { ok: true });
  client.terminate();
});

const failures = results.filter((row) => !row.ok);
await check("the inference delay hook is unavailable to a normal client", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  assert.throws(
    () => client.setTestInferenceDelay(100),
    (error) => error.code === "TEST_HOOKS_DISABLED",
  );
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
  };
  const pending = client.infer({
    planes: new Array(294).fill(0),
    size: 7,
    legal: [0, 1],
    ruleset: "brandubh",
    request_id: "request-1",
    position_digest: "b".repeat(64),
    model: { sha256: "a".repeat(64), ruleset: "brandubh", size: 7 },
  });
  await sleep(5);
  const message = client.worker.messages.at(-1).request;
  assert.equal(message.action, "infer");
  assert.equal("test_delay_ms" in message, false);
  client.worker.respond(client.worker.messages.length - 1, { ok: true });
  await pending;
  client.terminate();
});
await check("test hooks require explicit configuration before a delayed inference", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient({
    testHooks: true,
    workerUrl: "/neural-worker-test.js",
  });
  client.setTestInferenceDelay(25);
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
  };
  const pending = client.infer({
    planes: new Array(294).fill(0),
    size: 7,
    legal: [0, 1],
    ruleset: "brandubh",
    request_id: "request-2",
    position_digest: "b".repeat(64),
    model: { sha256: "a".repeat(64), ruleset: "brandubh", size: 7 },
  });
  await sleep(5);
  const worker = client.worker;
  assert.equal(worker.messages[0].request.action, "test");
  assert.equal(worker.messages[0].request.test_action, "configure");
  assert.equal(worker.messages[0].request.enabled, true);
  worker.respond(0, { test_hooks: true });
  await sleep(5);
  assert.equal(worker.messages[1].request.action, "test");
  assert.equal(worker.messages[1].request.test_action, "infer");
  assert.equal(worker.messages[1].request.test_delay_ms, 25);
  worker.respond(1, { ok: true });
  await pending;
  client.terminate();
});
await check("ordinary inference rejects an injected delay field", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
  };
  const pending = client.request(
    { action: "infer", test_delay_ms: 10 },
    { timeoutMs: 1000 },
  );
  await sleep(5);
  const worker = client.worker;
  worker.respond(0, null, "test_delay_ms is not accepted", "TEST_HOOKS_DISABLED");
  await assert.rejects(pending, (error) => error.code === "TEST_HOOKS_DISABLED");
  client.terminate();
});
await check("a normal client cannot activate any test-worker action", async () => {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  for (const action of [
    "configureTestHooks",
    "inferTestonly",
    "inferCompactTestonly",
  ]) {
    const pending = client.request({ action }, { timeoutMs: 1000 });
    await sleep(2);
    client.worker.respond(client.worker.messages.length - 1, null, "unavailable", "TEST_HOOKS_DISABLED");
    await assert.rejects(pending, (error) => error.code === "TEST_HOOKS_DISABLED");
  }
  // Ordinary inference still works after the rejected attempts.
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
  };
  const pending = client.infer({
    planes: new Array(294).fill(0),
    size: 7,
    legal: [0, 1],
    ruleset: "brandubh",
    request_id: "normal-after-test",
    position_digest: "b".repeat(64),
    model: { sha256: "a".repeat(64), ruleset: "brandubh", size: 7 },
  });
  await sleep(5);
  client.worker.respond(client.worker.messages.length - 1, { ok: true });
  assert.deepEqual(await pending, { ok: true });
  client.terminate();
});

if (failures.length) {
  for (const row of failures) console.error(`  - ${row.name}: ${row.error}`);
  process.exit(1);
}

// ---------- bounded independent-search batching ----------
function batchingClient(maxBatch = 4, maxQueue = 4) {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient({ batching: true, maxBatch, maxQueue, batchDelayMs: 1 });
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
  };
  return client;
}

function compactRequest(id, planes = null) {
  return {
    planes: planes ?? new Array(294).fill(0),
    size: 7,
    legal: [0, 1],
    ruleset: "brandubh",
    request_id: id,
    position_digest: "b".repeat(64),
    model: { sha256: "a".repeat(64), ruleset: "brandubh", size: 7 },
  };
}

function batchResult(requestId, value = 0.25) {
  return {
    index: 0,
    request_id: requestId,
    position_digest: "b".repeat(64),
    value,
    legal: [0, 1],
    legal_probabilities: [0.5, 0.5],
  };
}

await check("independent inference requests share one bounded batch", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("r1"));
  const second = client.inferCompact(compactRequest("r2"));
  await sleep(10);
  const worker = client.worker;
  assert.equal(worker.messages.length, 1);
  const payload = worker.messages[0].request;
  assert.equal(payload.action, "inferManyCompact");
  assert.equal(payload.cases.length, 2);
  worker.respond(0, {
    provider_requested: "wasm",
    execution_placement: "wasm",
    gpu_execution_verified: false,
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    model_bytes: 1,
    results: [batchResult("r2", 0.4), batchResult("r1", 0.2)],
    tensor_prepare_ms: 1,
    run_ms: 2,
    extract_ms: 0,
    batch: 2,
  });
  const [one, two] = await Promise.all([first, second]);
  assert.equal(one.value, 0.2);
  assert.equal(two.value, 0.4);
  assert.equal(one.request_id, "r1");
  assert.equal(two.request_id, "r2");
  assert.equal(client.batchStatistics().batches, 1);
  assert.equal(client.batchStatistics().observed_max_batch, 2);
  client.terminate();
});
await check("batch queue is bounded and rejects overflow", async () => {
  const client = batchingClient(2, 1);
  const first = client.inferCompact(compactRequest("q1"));
  await assert.rejects(
    () => client.inferCompact(compactRequest("q2")),
    (error) => error.code === "QUEUE_FULL",
  );
  await sleep(10);
  client.worker.respond(0, {
    results: [batchResult("q1")],
    provider_requested: "wasm",
    execution_placement: "wasm",
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    model_bytes: 1,
    batch: 1,
    tensor_prepare_ms: 0,
    run_ms: 1,
    extract_ms: 0,
  });
  await first;
  client.terminate();
});
await check("queued batched inference can be cancelled by request id", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("c1"));
  const second = client.inferCompact(compactRequest("c2"));
  const drained = await client.cancelRequest({ requestId: "c1", reason: "queued cancel" });
  assert.equal(drained.state, "drained");
  await assert.rejects(first, (error) => error.name === "AbortError");
  await sleep(10);
  client.worker.respond(0, {
    results: [batchResult("c2")],
    provider_requested: "wasm",
    execution_placement: "wasm",
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    model_bytes: 1,
    batch: 1,
    tensor_prepare_ms: 0,
    run_ms: 1,
    extract_ms: 0,
  });
  assert.equal((await second).request_id, "c2");
  client.terminate();
});
await check("a failed batch rejects every independent search", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("f1"));
  const second = client.inferCompact(compactRequest("f2"));
  await sleep(10);
  client.worker.respond(0, null, "batch failed", "OUTPUT_INVALID");
  await assert.rejects(first, (error) => error.code === "OUTPUT_INVALID");
  await assert.rejects(second, (error) => error.code === "OUTPUT_INVALID");
  client.terminate();
});

function within(promise, milliseconds = 500) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("test deadline exceeded")), milliseconds),
    ),
  ]);
}

await check("in-flight batch cancellation settles on batch success", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("s1"));
  const second = client.inferCompact(compactRequest("s2"));
  await sleep(10);
  const cancel = client.cancelRequest({ requestId: "s1", reason: "cancel in flight" });
  client.worker.respond(0, {
    results: [batchResult("s1"), batchResult("s2", 0.4)],
    provider_requested: "wasm",
    execution_placement: "wasm",
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    model_bytes: 1,
    batch: 2,
    tensor_prepare_ms: 1,
    run_ms: 2,
    extract_ms: 0,
  });
  await assert.rejects(within(first), (error) => error.name === "AbortError");
  assert.equal((await within(second)).value, 0.4);
  assert.equal((await within(cancel)).state, "drained");
  client.terminate();
});
await check("in-flight batch cancellation settles on batch failure", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("b1"));
  const second = client.inferCompact(compactRequest("b2"));
  await sleep(10);
  const cancel = client.cancelRequest({ requestId: "b1", reason: "cancel in flight" });
  client.worker.respond(0, null, "batch failed", "OUTPUT_INVALID");
  await assert.rejects(within(first), (error) => error.name === "AbortError");
  await assert.rejects(within(second), (error) => error.code === "OUTPUT_INVALID");
  assert.equal((await within(cancel)).outcome, "failed");
  client.terminate();
});
await check("worker crash settles queued and in-flight batches", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("w1"));
  const second = client.inferCompact(compactRequest("w2"));
  await sleep(10);
  client.worker.fail("batch worker crashed");
  await assert.rejects(within(first), (error) => error.name === "WorkerError");
  await assert.rejects(within(second), (error) => error.name === "WorkerError");
  assert.equal(client.batchQueue.length, 0);
  assert.equal(client.batchEntries.size, 0);
  client.terminate();
});
await check("termination and disposal reject outstanding batches", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("t1"));
  const second = client.inferCompact(compactRequest("t2"));
  await sleep(10);
  client.terminate("test termination");
  await assert.rejects(within(first), (error) => error.name === "AbortError");
  await assert.rejects(within(second), (error) => error.name === "AbortError");
  assert.equal(client.batchQueue.length, 0);
  assert.equal(client.batchEntries.size, 0);

  const replacement = batchingClient();
  const pending = replacement.inferCompact(compactRequest("d1"));
  await sleep(10);
  replacement.dispose();
  await assert.rejects(within(pending), (error) => error.name === "AbortError");
});
await check("timeout/abort rejects outstanding batches exactly once", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("x1"));
  const second = client.inferCompact(compactRequest("x2"));
  await sleep(10);
  const timeout = Object.assign(new Error("test timeout"), {
    name: "TimeoutError",
    code: "TimeoutError",
  });
  client.abort(timeout);
  await assert.rejects(within(first), (error) => error.name === "TimeoutError");
  await assert.rejects(within(second), (error) => error.name === "TimeoutError");
  assert.equal(client.batchQueue.length, 0);
  assert.equal(client.batchEntries.size, 0);
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
  };
  const replacement = client.inferCompact(compactRequest("x3"));
  await sleep(10);
  client.worker.respond(0, {
    results: [batchResult("x3")],
    provider_requested: "wasm",
    execution_placement: "wasm",
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    model_bytes: 1,
    batch: 1,
    tensor_prepare_ms: 0,
    run_ms: 1,
    extract_ms: 0,
  });
  assert.equal((await within(replacement)).request_id, "x3");
  client.terminate();
});

await check("unresponsive shared batch fails closed within drain bound", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("u1"));
  const second = client.inferCompact(compactRequest("u2"));
  await sleep(10);
  const firstSettled = assert.rejects(within(first), (error) => error.name === "AbortError");
  const secondSettled = assert.rejects(within(second), (error) => error.name === "AbortError");
  const cancel = client.cancelRequest({
    requestId: "u1",
    reason: "bounded drain",
    drainMs: 30,
  });
  const result = await within(cancel, 500);
  assert.equal(result.state, "terminated");
  assert.equal(result.errorCode, "DRAIN_TIMEOUT");
  await firstSettled;
  await secondSettled;
  assert.equal(client.worker, null);
  assert.equal(client.batchQueue.length, 0);
  assert.equal(client.batchEntries.size, 0);
});
await check("repeated cancellation of one batch member is counted once", async () => {
  const client = batchingClient();
  const first = client.inferCompact(compactRequest("m1"));
  const second = client.inferCompact(compactRequest("m2"));
  await sleep(10);
  const firstSettled = assert.rejects(within(first), (error) => error.name === "AbortError");
  const one = client.cancelRequest({ requestId: "m1", drainMs: 100 });
  const two = client.cancelRequest({ requestId: "m1", drainMs: 100 });
  client.worker.respond(0, {
    results: [batchResult("m1"), batchResult("m2")],
    provider_requested: "wasm",
    execution_placement: "wasm",
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    model_bytes: 1,
    batch: 2,
    tensor_prepare_ms: 0,
    run_ms: 1,
    extract_ms: 0,
  });
  await firstSettled;
  assert.equal((await within(second)).request_id, "m2");
  assert.equal((await within(one)).state, "drained");
  assert.equal((await within(two)).state, "drained");
  assert.equal(client.batchStatistics().rejected, 1);
  client.terminate();
});

await check("constructor batching limits are validated", () => {
  assert.throws(
    () => new NeuralClient({ batching: true, maxBatch: 0 }),
    (error) => error.code === "INPUT_INVALID",
  );
  assert.throws(
    () => new NeuralClient({ batching: true, maxQueue: 0 }),
    (error) => error.code === "INPUT_INVALID",
  );
  assert.throws(
    () => new NeuralClient({ batching: true, batchDelayMs: -1 }),
    (error) => error.code === "INPUT_INVALID",
  );
});


// ---------- pending direct attachment ownership ----------
function pendingDirectClient() {
  globalThis.Worker = FakeWorker;
  const client = new NeuralClient();
  client.worker = new FakeWorker();
  client.workerProvider = "wasm";
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
    provider: "wasm",
  };
  return client;
}

await check("pending direct attachment reserves ownership before acknowledgment", async () => {
  const client = pendingDirectClient();
  const modelBefore = client.modelIdentity();
  const workerBefore = client.worker;
  let fetches = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetches += 1;
    throw new Error("should not fetch");
  };
  const channel = new MessageChannel();
  const attachment = client.attachDirectPort(channel.port2, {
    channelId: "pending-a",
    timeoutMs: 1000,
  });
  assert.equal(client.directChannel.state, "pending");
  await assert.rejects(
    () => client.load("/model.onnx", {
      provider: "wasm",
      size: 7,
      ruleset: "brandubh",
      expectedSha256: "a".repeat(64),
    }),
    (error) => error.code === "DIRECT_BUSY",
  );
  await assert.rejects(
    () => client.load("/model.onnx", {
      provider: "webgpu",
      size: 7,
      ruleset: "brandubh",
      expectedSha256: "a".repeat(64),
    }),
    (error) => error.code === "DIRECT_BUSY",
  );
  assert.throws(
    () => client.attachDirectPort(new MessageChannel().port2, { channelId: "pending-b" }),
    (error) => error.code === "DIRECT_BUSY",
  );
  assert.equal(fetches, 0);
  assert.equal(client.worker, workerBefore);
  assert.deepEqual(client.modelIdentity(), modelBefore);
  const attachMessage = client.worker.messages.find(
    (message) => message.control === "attachDirectPort",
  );
  client.worker.respond(client.worker.messages.indexOf(attachMessage), {
    kind: "direct_port_attached",
  });
  await attachment;
  assert.equal(client.directChannel.state, "attached");
  const detach = client.detachDirectPort({ channelId: "pending-a" });
  const detachMessage = client.worker.messages.at(-1);
  assert.equal(detachMessage.control, "detachDirectPort");
  client.worker.respond(client.worker.messages.length - 1, {
    kind: "direct_port_detached",
    pending: false,
  });
  await detach;
  assert.equal(client.directChannel, null);
  globalThis.fetch = originalFetch;
  client.terminate();
});
function respondControl(client, control, value) {
  const index = client.worker.messages.findLastIndex(
    (message) => message.control === control,
  );
  assert.ok(index >= 0, `${control} was not sent`);
  client.worker.respond(index, value);
  return index;
}

await check("pending attachment timeout enters detaching until detach is confirmed", async () => {
  const client = pendingDirectClient();
  const timed = client.attachDirectPort(new MessageChannel().port2, {
    channelId: "timeout-a",
    timeoutMs: 20,
  });
  await assert.rejects(timed, (error) => error.name === "TimeoutError");
  assert.equal(client.directChannel.state, "detaching");
  assert.ok(
    client.worker.messages.some((message) => message.control === "detachDirectPort"),
  );
  respondControl(client, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: false,
  });
  await sleep(1);
  assert.equal(client.directChannel, null);
  assert.deepEqual(client.modelIdentity(), {
    sha256: "a".repeat(64),
    ruleset: "brandubh",
    size: 7,
  });
  client.terminate();
});

await check("unconfirmed detach invalidates the worker and model", async () => {
  const client = pendingDirectClient();
  const worker = client.worker;
  const timed = client.attachDirectPort(new MessageChannel().port2, {
    channelId: "timeout-fail-a",
    timeoutMs: 20,
  });
  await assert.rejects(timed, (error) => error.name === "TimeoutError");
  await sleep(40);
  assert.equal(worker.terminated, true);
  assert.equal(client.worker, null);
  assert.equal(client.directChannel, null);
  assert.equal(client.model, null);
});

await check("rejected attachment keeps detaching ownership until cleanup", async () => {
  const client = pendingDirectClient();
  const attach = client.attachDirectPort(new MessageChannel().port2, {
    channelId: "reject-a",
    timeoutMs: 100,
  });
  const attachIndex = client.worker.messages.findLastIndex(
    (message) => message.control === "attachDirectPort",
  );
  client.worker.respond(attachIndex, null, "rejected", "DETACH_FAILED");
  await assert.rejects(attach, (error) => error.code === "DETACH_FAILED");
  assert.equal(client.directChannel.state, "detaching");
  respondControl(client, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: false,
  });
  await sleep(1);
  assert.equal(client.directChannel, null);
  assert.deepEqual(client.modelIdentity(), {
    sha256: "a".repeat(64),
    ruleset: "brandubh",
    size: 7,
  });
  client.terminate();
});

await check("normal and repeated detach requests settle consistently", async () => {
  const client = pendingDirectClient();
  const attach = client.attachDirectPort(new MessageChannel().port2, {
    channelId: "normal-a",
    timeoutMs: 100,
  });
  respondControl(client, "attachDirectPort", { kind: "direct_port_attached" });
  await attach;
  assert.equal(client.directChannel.state, "attached");
  const first = client.detachDirectPort({ channelId: "normal-a", timeoutMs: 100 });
  const second = client.detachDirectPort({ channelId: "normal-a", timeoutMs: 100 });
  assert.equal(client.directChannel.state, "detaching");
  respondControl(client, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: false,
  });
  await first;
  assert.equal(client.directChannel, null);
  respondControl(client, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: false,
  });
  await second;
  assert.equal(client.directChannel, null);
  assert.equal(client.worker.terminated, false);
  client.terminate();
});

await check("provider switch is rejected during detaching cleanup", async () => {
  const client = pendingDirectClient();
  const timed = client.attachDirectPort(new MessageChannel().port2, {
    channelId: "switch-a",
    timeoutMs: 20,
  });
  await assert.rejects(timed, (error) => error.name === "TimeoutError");
  assert.equal(client.directChannel.state, "detaching");
  await assert.rejects(
    () => client.load("/model.onnx", {
      provider: "webgpu",
      size: 7,
      ruleset: "brandubh",
      expectedSha256: "a".repeat(64),
    }),
    (error) => error.code === "DIRECT_BUSY",
  );
  assert.equal(client.worker.terminated, false);
  respondControl(client, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: false,
  });
  await sleep(1);
  assert.equal(client.directChannel, null);
  client.terminate();
});

await check("pending attachment abort releases reservations", async () => {
  const client2 = pendingDirectClient();
  const pending = client2.attachDirectPort(new MessageChannel().port2, {
    channelId: "abort-a",
    timeoutMs: 1000,
  });
  client2.terminate("pending abort");
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(client2.directChannel, null);
  assert.equal(client2.model, null);
});
await check("late attachment acknowledgment cannot restore a timed-out reservation", async () => {
  const client = pendingDirectClient();
  const pending = client.attachDirectPort(new MessageChannel().port2, {
    channelId: "late-a",
    timeoutMs: 20,
  });
  await assert.rejects(pending, (error) => error.name === "TimeoutError");
  const message = client.worker.messages.find(
    (item) => item.control === "attachDirectPort",
  );
  client.worker.respond(client.worker.messages.indexOf(message), {
    kind: "direct_port_attached",
  });
  await sleep(1);
  assert.equal(client.directChannel.state, "detaching");
  respondControl(client, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: false,
  });
  await sleep(1);
  assert.equal(client.directChannel, null);
  assert.deepEqual(client.modelIdentity(), {
    sha256: "a".repeat(64),
    ruleset: "brandubh",
    size: 7,
  });
  client.terminate();
});


async function attachedDirectClient(channelId = "detach-a") {
  const client = pendingDirectClient();
  const attach = client.attachDirectPort(new MessageChannel().port2, {
    channelId,
    timeoutMs: 100,
  });
  respondControl(client, "attachDirectPort", { kind: "direct_port_attached" });
  await attach;
  return client;
}

await check("malformed detach response fails closed", async () => {
  const client = await attachedDirectClient("detach-malformed");
  const worker = client.worker;
  const detach = client.detachDirectPort({ channelId: "detach-malformed", timeoutMs: 100 });
  respondControl(client, "detachDirectPort", {});
  const result = await detach;
  assert.equal(result.failed, true);
  assert.equal(result.code, "DETACH_INVALID");
  assert.equal(worker.terminated, true);
  assert.equal(client.worker, null);
  assert.equal(client.directChannel, null);
  assert.equal(client.model, null);
});
await check("explicit detach worker error fails closed", async () => {
  const client = await attachedDirectClient("detach-error");
  const worker = client.worker;
  const detach = client.detachDirectPort({ channelId: "detach-error", timeoutMs: 100 });
  const index = client.worker.messages.findLastIndex(
    (message) => message.control === "detachDirectPort",
  );
  client.worker.respond(index, null, "detach rejected", "DETACH_FAILED");
  const result = await detach;
  assert.equal(result.failed, true);
  assert.equal(result.code, "DETACH_FAILED");
  assert.equal(worker.terminated, true);
  assert.equal(client.worker, null);
  assert.equal(client.model, null);
});
await check("pending and stale detach acknowledgments are distinguished", async () => {
  const pendingClient = await attachedDirectClient("detach-pending");
  const pendingWorker = pendingClient.worker;
  const pendingDetach = pendingClient.detachDirectPort({
    channelId: "detach-pending",
    timeoutMs: 100,
  });
  respondControl(pendingClient, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: true,
  });
  const pendingResult = await pendingDetach;
  assert.equal(pendingResult.pending, true);
  assert.equal(pendingClient.directChannel.state, "detaching");
  assert.equal(pendingWorker.terminated, false);
  pendingClient.abort(new Error("test cleanup"));
  assert.equal(pendingClient.directChannel, null);

  const staleClient = await attachedDirectClient("detach-stale");
  const staleWorker = staleClient.worker;
  const staleDetach = staleClient.detachDirectPort({
    channelId: "detach-stale",
    timeoutMs: 100,
  });
  respondControl(staleClient, "detachDirectPort", {
    kind: "direct_port_detached",
    pending: false,
    stale: true,
  });
  const staleResult = await staleDetach;
  assert.equal(staleResult.stale, true);
  assert.equal(staleClient.directChannel, null);
  assert.equal(staleWorker.terminated, false);
  staleClient.terminate();
});
await check("stale detach response cannot clear a newer reservation", async () => {
  const client = await attachedDirectClient("detach-old");
  const oldWorker = client.worker;
  const oldDetach = client.detachDirectPort({ channelId: "detach-old", timeoutMs: 500 });
  const oldDetachIndex = oldWorker.messages.findLastIndex(
    (message) => message.control === "detachDirectPort",
  );
  // Replace the worker/reservation before the old acknowledgment arrives.
  client.abort(new Error("replacement"));
  client.worker = new FakeWorker();
  client.workerProvider = "wasm";
  client.model = {
    model_sha256: "a".repeat(64),
    model_ruleset: "brandubh",
    size: 7,
  };
  const attach = client.attachDirectPort(new MessageChannel().port2, {
    channelId: "detach-new",
    timeoutMs: 100,
  });
  respondControl(client, "attachDirectPort", { kind: "direct_port_attached" });
  await attach;
  assert.equal(client.directChannel.channelId, "detach-new");
  assert.notEqual(client.worker, oldWorker);
  assert.equal(client.directChannel.state, "attached");
  oldWorker.respond(oldDetachIndex, {
    kind: "direct_port_detached",
    pending: false,
  });
  await oldDetach;
  assert.equal(client.directChannel.channelId, "detach-new");
  assert.equal(client.directChannel.state, "attached");
  client.terminate();
});


await check("contradictory detach success fields fail closed", async () => {
  for (const value of [
    {
      kind: "direct_port_detached",
      pending: false,
      failed: true,
      error: "detach error",
      code: "DETACH_FAILED",
    },
    {
      kind: "direct_port_detached",
      pending: false,
      timedOut: true,
    },
  ]) {
    const client = await attachedDirectClient(`detach-contradict-${Math.random()}`);
    const worker = client.worker;
    const detach = client.detachDirectPort({
      channelId: client.directChannel.channelId,
      timeoutMs: 100,
    });
    respondControl(client, "detachDirectPort", value);
    const result = await detach;
    assert.equal(result.failed, true);
    assert.equal(worker.terminated, true);
    assert.equal(client.worker, null);
    assert.equal(client.model, null);
    assert.equal(client.directChannel, null);
  }
});

console.log(`\n${results.length - failures.length}/${results.length} lifecycle checks passed`);
