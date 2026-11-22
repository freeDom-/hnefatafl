/* Actual-browser independent-search batching check.
 *
 * Two independent cooperative search contexts share one neural client. Each
 * context owns its own search worker; the client batches their legal-only
 * inference requests into one inferManyCompact call. This verifies real inference
 * batching, result association and bounded statistics without changing the
 * foreground single-search coordinator.
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
const provider = option("provider", "wasm");
const simulations = Number(option("simulations", "64"));
const mode = option("mode", "batched");
const mime = {
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".html": "text/html",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
};
const report = {
  kind: "mcts-independent-batching",
  schema: 1,
  generated: new Date().toISOString(),
  browser: null,
  provider,
  simulations,
  mode,
  model: null,
  results: [],
  batching: null,
  failures: [],
};
let server = null;
let browser = null;
try {
  server = createServer(async (request, response) => {
    try {
      let path = resolve(
        root,
        "." + decodeURIComponent(new URL(request.url, "http://localhost").pathname),
      );
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
  report.browser = browser.version();
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.goto(origin + "/neural.html");
  await page.waitForFunction(() => window.neural && window.neural.ready, null, { timeout: 90000 });
  const load = await page.evaluate(
    async (requestedProvider) => {
      const staged = await window.neural.meta();
      return window.neural.client.load("./" + staged.model.file, {
        provider: requestedProvider,
        size: staged.model.size,
        ruleset: staged.model.ruleset,
        expectedSha256: staged.model.sha256,
      });
    },
    provider,
  );
  report.model = {
    sha256: load.model_sha256,
    ruleset: load.model_ruleset,
    size: load.size,
    provider_requested: load.provider_requested,
    execution_placement: load.execution_placement,
  };
  const result = await page.evaluate(
    async (payload) => {
      if (payload.mode === "batched")
        window.neural.client.enableBatching({ maxBatch: 4, maxQueue: 8 });
      else window.neural.client.batching = false;
      const { NeuralSearchCoordinator } = await import("/search-client.js");
      const positions = (await window.neural.positions()).filter(
        (entry) => entry.ruleset === payload.model.ruleset && entry.kind !== "terminal",
      );
      const selected = [positions[0], positions[1]].filter(Boolean);
      if (selected.length < 2) throw new Error("need two non-terminal positions");
      const coordinators = selected.map(
        () => new NeuralSearchCoordinator({ neural: window.neural.client }),
      );
      await Promise.all(coordinators.map((coordinator) => coordinator.load()));
      const settings = {
        iterations: payload.simulations,
        time_limit: 10,
        seed: 7,
        exploration: 1.4,
      };
      const started = performance.now();
      let outcomes;
      if (payload.mode === "sequential") {
        outcomes = [];
        for (let index = 0; index < coordinators.length; index += 1) {
          outcomes.push(
            await coordinators[index].search({
              state: selected[index].state,
              settings,
              model: payload.model,
            }),
          );
        }
      } else {
        outcomes = await Promise.all(
          coordinators.map((coordinator, index) =>
            coordinator.search({
              state: selected[index].state,
              settings,
              model: payload.model,
            }),
          ),
        );
      }
      const elapsedMs = performance.now() - started;
      const results = outcomes.map((outcome, index) => ({
        position: selected[index].name,
        completed: outcome.search.completed_simulations,
        best_move: outcome.analysis.best_move,
        value: outcome.analysis.value,
        model_sha256: outcome.search.model.sha256,
        position_identity: outcome.search.position_identity,
      }));
      const batching = window.neural.client.batchStatistics();
      await Promise.all(coordinators.map((coordinator) => coordinator.dispose()));
      return { results, batching, elapsed_ms: elapsedMs };
    },
    { model: report.model, simulations, mode },
  );
  report.results = result.results;
  report.batching = result.batching;
  report.elapsed_ms = result.elapsed_ms;
  if (pageErrors.length) report.failures.push(`page errors: ${pageErrors.join("; ")}`);
  assert.equal(report.results.length, 2);
  for (const entry of report.results) {
    assert.equal(entry.completed, simulations);
    assert.equal(entry.model_sha256, report.model.sha256);
    assert.ok(entry.best_move);
  }
  if (mode === "batched") {
    if (!(report.batching.batches >= 1))
      report.failures.push("no batched inference call was made");
    if (!(report.batching.observed_max_batch >= 2))
      report.failures.push("batch size never reached two independent contexts");
  }
  if (mode === "batched" && !(report.batching.requests >= 2))
    report.failures.push(`expected at least two queued requests, got ${report.batching.requests}`);
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
