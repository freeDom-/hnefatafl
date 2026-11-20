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
  }
  postMessage(message) {
    this.messages.push(message);
  }
  terminate() {
    this.terminated = true;
  }
  respond(index, value, error = null, code = null) {
    const message = this.messages[index];
    if (this.onmessage)
      this.onmessage({ data: { id: message.id, value, error, code } });
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

const failures = results.filter((row) => !row.ok);
console.log(`\n${results.length - failures.length}/${results.length} lifecycle checks passed`);
if (failures.length) {
  for (const row of failures) console.error(`  - ${row.name}: ${row.error}`);
  process.exit(1);
}
