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
      let path = resolve(root, "." + decodeURIComponent(new URL(request.url, "http://localhost").pathname));
      if (path !== root && !path.startsWith(root + sep)) throw new Error("outside root");
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
    c2.dispose();
    c3.dispose();
    c4.dispose();
    c5.dispose();
    return {
      firstError,
      secondError,
      replacement_completed: replacement.search.completed_simulations,
      replacement_direct: replacement.search.coordinator.direct,
      fourthError,
      recovered_completed: recovered.search.completed_simulations,
    };
  });
  report.checks.push(outcome);
  assert.equal(outcome.secondError?.code, "DIRECT_BUSY");
  assert.equal(outcome.firstError?.name, "AbortError");
  assert.equal(outcome.replacement_completed, 64);
  assert.equal(outcome.replacement_direct, true);
  assert.ok(outcome.fourthError);
  assert.equal(outcome.recovered_completed, 64);
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
