/* Actual-browser direct MessageChannel ownership check.
 *
 * Only one direct inference context may own the shared ORT session at a time.
 * A second direct search must fail before inference, cancellation must release
 * the context, and a later search must work with a fresh channel.
 */
import assert from "node:assert/strict";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const argv = process.argv.slice(2);
const root = resolve(argv.find((arg) => !arg.startsWith("--")) || "work/phase3b/spike");
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const output = option("output", null);
const mime = {
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".html": "text/html",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
};
const report = { kind: "mcts-direct-ownership", schema: 1, checks: [], failures: [] };
let server = null;
let browser = null;
try {
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const testWorker = pathname === "/neural-worker-test.js";
      let path = testWorker
        ? resolve(import.meta.dirname, "neural-worker-test.js")
        : resolve(root, "." + pathname);
      if (!testWorker && path !== root && !path.startsWith(root + sep))
        throw new Error("outside root");
      if ((await stat(path)).isDirectory()) path = resolve(path, "index.html");
      response.setHeader("Content-Type", mime[extname(path)] || "application/octet-stream");
      response.end(await readFile(path));
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ executablePath: process.env.SPIKE_CHROMIUM || undefined });
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.goto(origin + "/neural.html");
  await page.waitForFunction(() => window.neural && window.neural.ready, null, { timeout: 90000 });
  const outcome = await page.evaluate(async () => {
    const staged = await window.neural.meta();
    const client = window.neural.client;
    await client.load("./" + staged.model.file, {
      provider: "wasm",
      size: staged.model.size,
      ruleset: staged.model.ruleset,
      expectedSha256: staged.model.sha256,
    });
    const model = client.modelIdentity();
    const positions = (await window.neural.positions()).filter(
      (entry) => entry.ruleset === model.ruleset && entry.kind !== "terminal",
    );
    const { NeuralSearchCoordinator } = await import("/search-client.js");
    const c1 = new NeuralSearchCoordinator({ neural: client });
    const c2 = new NeuralSearchCoordinator({ neural: client });
    c1.directInference = true;
    c2.directInference = true;
    await Promise.all([c1.load(), c2.load()]);
    const settings = { iterations: 64, time_limit: 10, seed: 7, exploration: 1.4 };
    const first = c1.search({ state: positions[0].state, settings, model });
    // Wait until the first direct inference has actually started.
    for (let index = 0; index < 200 && !c1.active?.inferencePending; index += 1)
      await new Promise((done) => setTimeout(done, 10));
    let secondError = null;
    try {
      await c2.search({ state: positions[1].state, settings, model });
    } catch (error) {
      secondError = { name: error.name, code: error.code, message: error.message };
    }
    await c1.cancel("direct ownership cancellation");
    let firstError = null;
    try {
      await first;
    } catch (error) {
      firstError = { name: error.name, code: error.code };
    }
    await new Promise((done) => setTimeout(done, 300));
    const c3 = new NeuralSearchCoordinator({ neural: client });
    c3.directInference = true;
    await c3.load();
    const replacement = await c3.search({ state: positions[0].state, settings, model });
    const c4 = new NeuralSearchCoordinator({ neural: client });
    c4.directInference = true;
    await c4.load();
    const fourth = c4.search({ state: positions[0].state, settings, model });
    for (let index = 0; index < 200 && !c4.active?.inferencePending; index += 1)
      await new Promise((done) => setTimeout(done, 10));
    client.terminate("direct worker failure test");
    await c4.cancel("direct worker failure");
    let fourthError = null;
    try {
      await fourth;
    } catch (error) {
      fourthError = { name: error.name, code: error.code };
    }
    const modelAfterTermination = client.model;
    const stagedReload = await window.neural.meta();
    await client.load("./" + stagedReload.model.file, {
      provider: "wasm",
      size: stagedReload.model.size,
      ruleset: stagedReload.model.ruleset,
      expectedSha256: stagedReload.model.sha256,
    });
    const c5 = new NeuralSearchCoordinator({ neural: client });
    await c5.load();
    const recovered = await c5.search({ state: positions[0].state, settings, model });
    // Neural attachment succeeds, then search-worker attachment times out. The
    // neural detach must run before the replacement can attach.
    const c6 = new NeuralSearchCoordinator({ neural: client });
    c6.directInference = true;
    await c6.load();
    const realAttachSearch = c6._attachSearchDirect.bind(c6);
    const realAttachNeuralForFailure = client.attachDirectPort.bind(client);
    const realDetachNeuralForFailure = client.detachDirectPort.bind(client);
    let neuralAttachSucceeded = false;
    let neuralDetachAcknowledged = false;
    client.attachDirectPort = async (...args) => {
      const result = await realAttachNeuralForFailure(...args);
      neuralAttachSucceeded = true;
      return result;
    };
    client.detachDirectPort = async (...args) => {
      const result = await realDetachNeuralForFailure(...args);
      neuralDetachAcknowledged = Boolean(result);
      return result;
    };
    c6._attachSearchDirect = async () => {
      const error = new Error("search attach timeout");
      error.name = "TimeoutError";
      throw error;
    };
    let attachFailure = null;
    try {
      await c6.search({ state: positions[0].state, settings, model });
    } catch (error) {
      attachFailure = { name: error.name, code: error.code };
    } finally {
      c6._attachSearchDirect = realAttachSearch;
      client.attachDirectPort = realAttachNeuralForFailure;
      client.detachDirectPort = realDetachNeuralForFailure;
    }
    const c7 = new NeuralSearchCoordinator({ neural: client });
    c7.directInference = true;
    await c7.load();
    const afterAttachFailure = await c7.search({ state: positions[0].state, settings, model });
    // Cancel while the neural attachment is still pending (before any search
    // worker attachment). Cleanup must not leave a stale/blocking channel.
    const c8 = new NeuralSearchCoordinator({ neural: client });
    c8.directInference = true;
    await c8.load();
    const realAttachNeural = client.attachDirectPort.bind(client);
    client.attachDirectPort = () => new Promise(() => {});
    const cancelledSetup = c8.search({ state: positions[0].state, settings, model });
    await new Promise((done) => setTimeout(done, 30));
    await c8.cancel("cancel before neural attach");
    let setupCancel = null;
    try {
      await cancelledSetup;
    } catch (error) {
      setupCancel = { name: error.name, code: error.code };
    } finally {
      client.attachDirectPort = realAttachNeural;
    }
    const c9 = new NeuralSearchCoordinator({ neural: client });
    c9.directInference = true;
    await c9.load();
    const afterSetupCancel = await c9.search({ state: positions[0].state, settings, model });
    const statsBeforeRaw = await client.directStats();
    const rawModel = client.modelIdentity();
    const rawPlanes = btoa(String.fromCharCode(...new Array(6 * rawModel.size * rawModel.size).fill(0)));
    const rawRequest = (id) => ({
      action: "inferCompact",
      planes_base64: rawPlanes,
      size: rawModel.size,
      legal: [0, 1],
      ruleset: rawModel.ruleset,
      request_id: id,
      position_digest: "a".repeat(64),
      model: rawModel,
    });
    const attachRaw = async (channelId) => {
      const channel = new MessageChannel();
      await client.attachDirectPort(channel.port2, { channelId });
      return channel;
    };
    const inferRaw = (channel, id) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("raw direct inference timeout")), 2000);
        channel.port1.onmessage = ({ data }) => {
          if (data?.id !== id) return;
          clearTimeout(timer);
          resolve(data);
        };
        channel.port1.postMessage({ kind: "infer", id, model: rawModel, request: rawRequest(id) });
      });
    const channelA = await attachRaw("raw-channel-a");
    const rawFirst = await inferRaw(channelA, "raw-a1");
    const statsAfterFirst = await client.directStats();
    await client.detachDirectPort({ channelId: "raw-channel-a" });
    try {
      channelA.port1.postMessage({
        kind: "infer",
        id: "raw-a-stale",
        model: rawModel,
        request: rawRequest("raw-a-stale"),
      });
    } catch {
      // The stale port may already be closed; it must never execute either way.
    }
    await new Promise((done) => setTimeout(done, 100));
    const statsAfterStale = await client.directStats();
    const channelB = await attachRaw("raw-channel-b");
    const staleDetach = await client.detachDirectPort({ channelId: "raw-channel-a" });
    const rawSecond = await inferRaw(channelB, "raw-b1");
    const modelBeforeRejectedLoad = client.modelIdentity();
    const workerBeforeRejectedLoad = client.worker;
    let modelLoadError = null;
    let providerSwitchError = null;
    let normalInferError = null;
    let releaseError = null;
    let cacheConfigError = null;
    try {
      await client.load("./" + stagedReload.model.file, {
        provider: "wasm",
        size: stagedReload.model.size,
        ruleset: stagedReload.model.ruleset,
        expectedSha256: stagedReload.model.sha256,
      });
    } catch (error) {
      modelLoadError = error.code ?? error.name;
    }
    try {
      await client.load("./" + stagedReload.model.file, {
        provider: "webgpu",
        size: stagedReload.model.size,
        ruleset: stagedReload.model.ruleset,
        expectedSha256: stagedReload.model.sha256,
      });
    } catch (error) {
      providerSwitchError = error.code ?? error.name;
    }
    const modelAfterRejectedLoad = client.modelIdentity();
    const workerAfterRejectedLoad = client.worker;
    try {
      await client.infer({
        planes: new Array(6 * rawModel.size * rawModel.size).fill(0),
        size: rawModel.size,
        legal: [0, 1],
        ruleset: rawModel.ruleset,
        request_id: "normal-during-direct",
        position_digest: "a".repeat(64),
        model: rawModel,
      });
    } catch (error) {
      normalInferError = error.code ?? error.name;
    }
    try {
      await client.release();
    } catch (error) {
      releaseError = error.code ?? error.name;
    }
    try {
      await client.configureInferenceCache({ enabled: true });
    } catch (error) {
      cacheConfigError = error.code ?? error.name;
    }
    const capabilitiesDuringDirect = await client.capabilities();
    await client.detachDirectPort({ channelId: "raw-channel-b" });
    const { NeuralClient } = await import("/neural-client.js");
    const testClient = new NeuralClient({
      testHooks: true,
      workerUrl: "/neural-worker-test.js",
    });
    let testClientLoadError = null;
    try {
      await testClient.load("./" + stagedReload.model.file, {
        provider: "wasm",
        size: stagedReload.model.size,
        ruleset: stagedReload.model.ruleset,
        expectedSha256: stagedReload.model.sha256,
      });
    } catch (error) {
      testClientLoadError = { name: error.name, code: error.code, message: error.message };
    }
    if (testClientLoadError) throw new Error(`test client load failed: ${JSON.stringify(testClientLoadError)}`);
    const testModel = testClient.modelIdentity();
    testClient.setTestInferenceDelay(500);
    const delayedNormal = testClient.infer({
      planes: new Array(6 * testModel.size * testModel.size).fill(0),
      size: testModel.size,
      legal: [0, 1],
      ruleset: testModel.ruleset,
      request_id: "delayed-normal",
      position_digest: "a".repeat(64),
      model: testModel,
    });
    await new Promise((done) => setTimeout(done, 50));
    let normalAttachError = null;
    try {
      const blocked = await (async () => {
        const channel = new MessageChannel();
        await testClient.attachDirectPort(channel.port2, { channelId: "during-normal" });
        return channel;
      })();
      await testClient.detachDirectPort({ channelId: "during-normal" });
      blocked.port1.close?.();
    } catch (error) {
      normalAttachError = error.code ?? error.name;
    }
    let sustainedError = null;
    try {
      await delayedNormal;
    } catch (error) {
      sustainedError = error.code ?? error.name;
    }
    testClient.setTestInferenceDelay(null);
    const testChannelAfter = new MessageChannel();
    await testClient.attachDirectPort(testChannelAfter.port2, {
      channelId: "after-normal",
    });
    await testClient.detachDirectPort({ channelId: "after-normal" });
    testChannelAfter.port1.close?.();
    testClient.dispose();
    const channelAfter = await attachRaw("after-normal");
    await client.detachDirectPort({ channelId: "after-normal" });
    channelAfter.port1.close?.();
    let finalLoadError = null;
    try {
      await client.load("./" + stagedReload.model.file, {
        provider: "wasm",
        size: stagedReload.model.size,
        ruleset: stagedReload.model.ruleset,
        expectedSha256: stagedReload.model.sha256,
      });
    } catch (error) {
      finalLoadError = { name: error.name, code: error.code, message: error.message };
    }
    const statsAfterRecovery = await client.directStats();
    const failedDetachChannel = await attachRaw("failed-detach");
    const failedDetachWorker = client.worker;
    const realPost = failedDetachWorker.postMessage.bind(failedDetachWorker);
    failedDetachWorker.postMessage = (message, transfer) => {
      if (message?.control === "detachDirectPort") return;
      realPost(message, transfer);
    };
    const failedDetach = await client.detachDirectPort({
      channelId: "failed-detach",
      timeoutMs: 30,
    });
    failedDetachChannel.port1.close?.();
    const failedDetachState = {
      result: failedDetach,
      workerTerminated: failedDetachWorker.terminated === true,
      workerNull: client.worker === null,
      model: client.model,
      directChannel: client.directChannel,
    };
    await client.load("./" + stagedReload.model.file, {
      provider: "wasm",
      size: stagedReload.model.size,
      ruleset: stagedReload.model.ruleset,
      expectedSha256: stagedReload.model.sha256,
    });
    c2.dispose();
    c3.dispose();
    c4.dispose();
    c5.dispose();
    c6.dispose();
    c7.dispose();
    c8.dispose();
    c9.dispose();
    return {
      firstError,
      secondError,
      replacement_completed: replacement.search.completed_simulations,
      replacement_direct: replacement.search.coordinator.direct,
      fourthError,
      modelAfterTermination,
      recovered_completed: recovered.search.completed_simulations,
      attachFailure,
      neuralAttachSucceeded,
      neuralDetachAcknowledged,
      after_attach_failure_completed: afterAttachFailure.search.completed_simulations,
      setupCancel,
      after_setup_cancel_completed: afterSetupCancel.search.completed_simulations,
      rawFirst: rawFirst.kind,
      rawSecond: rawSecond.kind,
      staleDetach,
      modelBeforeRejectedLoad,
      modelAfterRejectedLoad,
      workerBeforeRejectedLoad,
      workerAfterRejectedLoad,
      modelLoadError,
      providerSwitchError,
      statsBeforeRaw,
      statsAfterFirst,
      statsAfterStale,
      normalInferError,
      releaseError,
      cacheConfigError,
      capabilitiesDuringDirect: Boolean(capabilitiesDuringDirect),
      statsAfterRecovery,
      failedDetachState,
      normalAttachError,
      sustainedError,
      testClientLoadError,
      finalLoadError,
    };
  });
  report.checks.push(outcome);
  assert.equal(outcome.secondError?.code, "DIRECT_BUSY");
  assert.equal(outcome.firstError?.name, "AbortError");
  assert.equal(outcome.replacement_completed, 64);
  assert.equal(outcome.replacement_direct, true);
  assert.ok(outcome.fourthError);
  assert.equal(outcome.modelAfterTermination, null);
  assert.equal(outcome.recovered_completed, 64);
  assert.equal(outcome.attachFailure?.name, "TimeoutError");
  assert.equal(outcome.neuralAttachSucceeded, true);
  assert.equal(outcome.neuralDetachAcknowledged, true);
  assert.equal(outcome.after_attach_failure_completed, 64);
  assert.equal(outcome.setupCancel?.name, "AbortError");
  assert.equal(outcome.after_setup_cancel_completed, 64);
  assert.equal(outcome.rawFirst, "infer_result");
  assert.equal(outcome.rawSecond, "infer_result");
  assert.equal(outcome.staleDetach.stale, true);
  assert.equal(
    outcome.statsAfterFirst.accepted,
    outcome.statsBeforeRaw.accepted + 1,
  );
  assert.equal(outcome.statsAfterStale.accepted, outcome.statsAfterFirst.accepted);
  assert.equal(outcome.modelLoadError, "DIRECT_BUSY");
  assert.equal(outcome.providerSwitchError, "DIRECT_BUSY");
  assert.deepEqual(outcome.modelAfterRejectedLoad, outcome.modelBeforeRejectedLoad);
  assert.equal(outcome.workerAfterRejectedLoad, outcome.workerBeforeRejectedLoad);
  assert.equal(outcome.normalInferError, "DIRECT_BUSY");
  assert.equal(outcome.releaseError, "DIRECT_BUSY");
  assert.equal(outcome.cacheConfigError, "DIRECT_BUSY");
  assert.equal(outcome.capabilitiesDuringDirect, true);
  assert.equal(outcome.statsAfterRecovery.accepted, outcome.statsBeforeRaw.accepted + 2);
  assert.equal(outcome.failedDetachState.result.timedOut, true);
  assert.equal(outcome.failedDetachState.workerNull, true);
  assert.equal(outcome.failedDetachState.model, null);
  assert.equal(outcome.failedDetachState.directChannel, null);
  assert.equal(outcome.normalAttachError, "SESSION_BUSY");
  assert.equal(outcome.finalLoadError, null);
  assert.equal(outcome.testClientLoadError, null);
  if (pageErrors.length) report.failures.push(`page errors: ${pageErrors.join("; ")}`);
} catch (error) {
  report.failures.push(String(error?.stack ?? error));
} finally {
  await browser?.close().catch(() => {});
  if (server) await new Promise((done) => server.close(done));
}
report.ok = report.failures.length === 0;
if (output) {
  await mkdir(dirname(resolve(output)), { recursive: true });
  await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify(report, null, 2));
process.exit(report.ok ? 0 : 1);
