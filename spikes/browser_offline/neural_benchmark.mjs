// Browser neural inference benchmark for the isolated spike. Serves a staged
// neural directory (see prepare_neural.py), runs the actual exported model
// through ORT Web and writes a machine-readable report. It never claims GPU
// acceleration for a WASM session.
//
// Usage:
//   node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
//     --provider wasm --output /tmp/neural-wasm.json
//   node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
//     --provider webgpu --require-webgpu
import assert from "node:assert/strict";
import { readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { resolve, sep, extname } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const argv = process.argv.slice(2);
const root = resolve(
  argv.find((arg) => !arg.startsWith("--")) || "work/phase0/neural",
);
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const provider = option("provider", "wasm");
const output = option("output", null);
const batchSizes = option("batch-sizes", "1,2,4,8,16,32")
  .split(",")
  .map((value) => Number(value));
const reps = Number(option("reps", "5"));
const mobile = argv.includes("--mobile");
const requireWebGPU = argv.includes("--require-webgpu");

const mime = {
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".html": "text/html",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
};
const server = createServer(async (request, response) => {
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
const assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));

const browser = await chromium.launch({
  executablePath: process.env.SPIKE_CHROMIUM || undefined,
});
try {
  const context = await browser.newContext(
    mobile
      ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }
      : { viewport: { width: 1280, height: 800 } },
  );
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.goto(origin + "/neural.html");
  await page.waitForFunction(() => window.neural && window.neural.ready);
  const capabilities = await page.evaluate(() => window.neural.capabilities());

  const report = {
    kind: "browser-neural-benchmark",
    generated: new Date().toISOString(),
    profile: mobile ? "mobile-emulated" : "desktop",
    browser: browser.version(),
    ort_version: assets.ort_version,
    model: assets.model,
    requested_provider: provider,
    capabilities,
    page_errors: pageErrors,
  };

  let load = null;
  try {
    load = await page.evaluate((value) => window.neural.load(value), provider);
    report.load = load;
    report.execution_provider = load.provider;
    report.provider_is_gpu = load.provider === "webgpu";
  } catch (error) {
    report.load_error = String(error.message || error);
    report.execution_provider = null;
    report.provider_is_gpu = false;
  }

  if (load) {
    const infer = await page.evaluate(() => window.neural.infer());
    report.inference = {
      provider: infer.provider,
      inference_ms: infer.inference_ms,
      value: infer.value,
      legal_count: infer.legal.length,
      legal_probability_sum: infer.legal_probabilities.reduce((a, b) => a + b, 0),
    };
    assert.ok(
      Math.abs(report.inference.legal_probability_sum - 1) < 1e-4,
      "legal-only probabilities must normalise to 1",
    );
    report.benchmark = await page.evaluate(
      (payload) => window.neural.benchmark(payload.batchSizes, payload.reps),
      { batchSizes, reps },
    );
  }

  if (requireWebGPU) {
    assert.equal(
      report.provider_is_gpu,
      true,
      "WebGPU was required but the session did not use a GPU provider",
    );
  }
  if (output) await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
  await context.close();
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
