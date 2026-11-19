// Browser neural inference benchmark for the isolated spike.
//
// Serves a staged neural directory (prepare_neural.py), runs the actual exported
// model through ORT Web WASM/WebGPU and writes a machine-readable report. It
// reports the provider that was *requested*, the session placement that was
// *observed*, and whether GPU execution was *verified*. A WASM/CPU fallback is
// never labelled as GPU acceleration.
//
// Usage:
//   node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
//     --provider wasm --output /tmp/neural-wasm.json
//   node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
//     --provider webgpu --require-webgpu
//
// The benchmark fails (non-zero exit) if model loading, inference or the
// mandatory parity relationship between batched and single inference fails.
// Capability probing alone is not acceptance.
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const argv = process.argv.slice(2);
const root = resolve(argv.find((arg) => !arg.startsWith("--")) || "work/phase0/neural");
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const flag = (name) => argv.includes(`--${name}`);
const provider = option("provider", "wasm");
const output = option("output", null);
const batchSizes = option("batch-sizes", "1,2,4,8,16,32")
  .split(",")
  .map((value) => Number(value));
const reps = Number(option("reps", "5"));
const warmup = Number(option("warmup", "2"));
const switches = Number(option("switches", "3"));
const mobile = flag("mobile");
const requireWebGPU = flag("require-webgpu");

function usageError(message) {
  console.error(`neural_benchmark: ${message}`);
  process.exit(2);
}
if (!["wasm", "webgpu"].includes(provider)) usageError("--provider must be wasm or webgpu");
if (!batchSizes.length || batchSizes.some((batch) => !Number.isInteger(batch) || batch < 1 || batch > 32))
  usageError("--batch-sizes must be integers in 1..32");
if (!Number.isInteger(reps) || reps < 1 || reps > 50) usageError("--reps must be 1..50");
if (!Number.isInteger(warmup) || warmup < 0 || warmup > 20) usageError("--warmup must be 0..20");
if (!Number.isInteger(switches) || switches < 0 || switches > 20) usageError("--switches must be 0..20");

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

const report = {
  kind: "browser-neural-benchmark",
  schema: 2,
  generated: new Date().toISOString(),
  profile: mobile ? "mobile-emulated" : "desktop",
  browser: null,
  ort_version: assets.ort_version,
  requested_provider: provider,
  batch_sizes: batchSizes,
  reps,
  warmup,
  model: assets.model,
  runtime_inventory: assets.ort_inventory ?? [],
  failures: [],
  notes: [],
};

let browser = null;
let context = null;
try {
  browser = await chromium.launch({ executablePath: process.env.SPIKE_CHROMIUM || undefined });
  report.browser = browser.version();
  context = await browser.newContext(
    mobile
      ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }
      : { viewport: { width: 1280, height: 800 } },
  );
  const external = [];
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) {
      external.push(url.href);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.goto(origin + "/neural.html");
  await page.waitForFunction(() => window.neural && window.neural.ready, null, { timeout: 30000 });
  report.capabilities = await page.evaluate(() => window.neural.capabilities());

  const pageLoad = (requestedProvider) =>
    page.evaluate(
      async (value) => {
        const { assets: staged } = await window.neural.assets();
        return window.neural.client.load("./" + staged.model.file, {
          provider: value,
          size: staged.model.size,
          ruleset: staged.model.ruleset,
          expectedSha256: staged.model.sha256,
        });
      },
      requestedProvider,
    );

  const initStarted = performance.now();
  try {
    report.load = await pageLoad(provider);
    report.execution_placement = report.load.execution_placement;
    report.gpu_execution_verified = report.load.gpu_execution_verified;
    report.provider_evidence = report.load.provider_evidence;
  } catch (error) {
    report.load_error = String(error?.message ?? error);
    report.failures.push(`model load failed: ${report.load_error}`);
  }
  report.initialization_ms = performance.now() - initStarted;
  report.memory_after_load = await page.evaluate(() => window.neural.client.capabilities().then((c) => c.memory));

  if (report.load) {
    const cases = await page.evaluate(async () => {
      const { assets: staged } = await window.neural.assets();
      const reference = await (await fetch("./" + staged.model.reference)).json();
      return reference.cases
        .filter((entry) => !entry.terminal)
        .map((entry) => ({ planes: entry.planes, legal: entry.legal, position: entry.position }));
    });
    report.reference_positions = cases.length;

    // First inference (cold) vs warm inference, split into tensor prep and run.
    const first = await page.evaluate(
      (payload) => window.neural.client.infer(payload.planes, payload.size, payload.legal),
      { planes: cases[0].planes, size: assets.model.size, legal: cases[0].legal },
    );
    report.first_inference = {
      provider: first.provider_requested,
      execution_placement: first.execution_placement,
      inference_ms: first.inference_ms,
      tensor_prepare_ms: first.tensor_prepare_ms,
      run_ms: first.run_ms,
      value: first.value,
      legal_probability_sum: first.legal_probabilities.reduce((a, b) => a + b, 0),
    };
    if (Math.abs(report.first_inference.legal_probability_sum - 1) > 1e-4)
      report.failures.push("first inference legal probabilities do not sum to 1");

    const warm = [];
    for (let index = 0; index < reps; index += 1) {
      const value = await page.evaluate(
        (payload) => window.neural.client.infer(payload.planes, payload.size, payload.legal),
        { planes: cases[0].planes, size: assets.model.size, legal: cases[0].legal },
      );
      warm.push({ inference_ms: value.inference_ms, run_ms: value.run_ms, tensor_prepare_ms: value.tensor_prepare_ms });
    }
    report.warm_inference = {
      samples: warm.length,
      run_ms_median: median(warm.map((row) => row.run_ms)),
      tensor_prepare_ms_median: median(warm.map((row) => row.tensor_prepare_ms)),
      inference_ms_median: median(warm.map((row) => row.inference_ms)),
    };

    // Batched distinct-position benchmark.
    try {
      report.benchmark = await page.evaluate(
        (payload) =>
          window.neural.client.benchmark(payload.cases, payload.batchSizes, {
            warmup: payload.warmup,
            reps: payload.reps,
          }),
        { cases, batchSizes, warmup, reps },
      );
    } catch (error) {
      report.failures.push(`batched benchmark failed: ${error?.message ?? error}`);
    }

    // Batched-vs-single relationship: each batched row must equal its own
    // single-position inference. This is the genuine batched inference contract.
    const batchCheck = [];
    for (const batch of batchSizes) {
      const filled = Array.from({ length: batch }, (_, index) => cases[index % cases.length]);
      let many;
      try {
        many = await page.evaluate(
          (payload) => window.neural.client.inferMany(payload.cases),
          { cases: filled.map(({ planes, legal }) => ({ planes, legal })) },
        );
      } catch (error) {
        report.failures.push(`inferMany batch ${batch} failed: ${error?.message ?? error}`);
        continue;
      }
      const singles = [];
      for (const entry of filled) {
        singles.push(
          await page.evaluate(
            (payload) => window.neural.client.infer(payload.planes, payload.size, payload.legal),
            { planes: entry.planes, size: assets.model.size, legal: entry.legal },
          ),
        );
      }
      let worstLogit = 0;
      let worstValue = 0;
      let worstProbability = 0;
      many.results.forEach((row, index) => {
        const single = singles[index];
        for (let at = 0; at < row.policy_logits.length; at += 1)
          worstLogit = Math.max(worstLogit, Math.abs(row.policy_logits[at] - single.policy_logits[at]));
        worstValue = Math.max(worstValue, Math.abs(row.value - single.value));
        for (let at = 0; at < row.legal_probabilities.length; at += 1)
          worstProbability = Math.max(
            worstProbability,
            Math.abs(row.legal_probabilities[at] - single.legal_probabilities[at]),
          );
      });
      const pass = worstLogit <= 1e-3 && worstValue <= 1e-4 && worstProbability <= 1e-5;
      batchCheck.push({
        batch,
        distinct_positions: Math.min(batch, cases.length),
        worst_logit_abs_error: worstLogit,
        worst_value_abs_error: worstValue,
        worst_probability_abs_error: worstProbability,
        pass,
      });
      if (!pass) report.failures.push(`batched inference diverged from single inference at batch ${batch}`);
    }
    report.batch_vs_single = batchCheck;

    // Model switching / release behaviour: reload the same model and record
    // observed memory drift. This does not claim to measure process RSS.
    const switchRows = [];
    for (let index = 0; index < switches; index += 1) {
      try {
        await page.evaluate(() => window.neural.client.release({ unloadBundle: false }));
        const loaded = await pageLoad(provider);
        const memory = await page.evaluate(() => window.neural.client.capabilities().then((c) => c.memory));
        switchRows.push({
          iteration: index,
          execution_placement: loaded.execution_placement,
          js_heap_used_bytes: memory?.js_heap_used_bytes ?? null,
        });
      } catch (error) {
        report.failures.push(`model switch ${index} failed: ${error?.message ?? error}`);
        break;
      }
    }
    report.model_switching = switchRows;
    report.memory_after_switches = await page.evaluate(() =>
      window.neural.client.capabilities().then((c) => c.memory),
    );
  } else if (requireWebGPU) {
    report.failures.push("--require-webgpu: no GPU session could be created");
  }

  if (requireWebGPU && report.load && !report.gpu_execution_verified)
    report.failures.push(
      `--require-webgpu: GPU execution was not verified (placement ${report.execution_placement})`,
    );

  report.page_errors = pageErrors;
  report.external_requests = external;
  if (pageErrors.length) report.failures.push(`page errors: ${pageErrors.length}`);
  if (external.length) report.failures.push(`external requests: ${external.length}`);
} catch (error) {
  report.failures.push(`harness error: ${error?.message ?? String(error)}`);
} finally {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  await new Promise((done) => server.close(done));
}

report.ok = report.failures.length === 0;
if (output) {
  await mkdir(dirname(resolve(output)), { recursive: true });
  await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
}
console.log(
  JSON.stringify(
    {
      ok: report.ok,
      requested_provider: report.requested_provider,
      execution_placement: report.execution_placement ?? null,
      gpu_execution_verified: report.gpu_execution_verified ?? false,
      model_bytes: report.model?.bytes ?? null,
      model_gzip_bytes: report.model?.gzip_bytes ?? null,
      initialization_ms: report.initialization_ms ?? null,
      first_inference_ms: report.first_inference?.inference_ms ?? null,
      warm_run_ms_median: report.warm_inference?.run_ms_median ?? null,
      batch_vs_single: report.batch_vs_single ?? null,
      failures: report.failures,
    },
    null,
    2,
  ),
);
process.exit(report.ok ? 0 : 1);

function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}
