// Browser neural numerical-parity gate for the isolated spike.
//
// Serves a staged neural directory (prepare_neural.py), runs the *actual*
// exported model through ORT Web, and compares every observed output against the
// native reference vectors using the canonical Python comparator
// (neural_parity.py). It exits non-zero on any mandatory parity failure.
//
// Usage:
//   node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
//     --provider wasm --output /tmp/neural-parity-wasm.json
//   node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
//     --provider webgpu --require-webgpu
//
// `--simulate-corruption` perturbs one observed logit before comparison so the
// test suite can demonstrate that the gate actually detects a regression.
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep, join } from "node:path";
import { spawn } from "node:child_process";
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
const python = process.env.SPIKE_PYTHON || resolve(import.meta.dirname, "../../.venv/bin/python");
const requireWebGPU = flag("require-webgpu");
const simulateCorruption = flag("simulate-corruption");
if (!["wasm", "webgpu"].includes(provider)) {
  console.error("neural_parity: --provider must be wasm or webgpu");
  process.exit(2);
}

function runWithStdin(command, args, input) {
  return new Promise((done) => {
    const child = spawn(command, args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (error) => done({ code: 1, stdout, stderr: `${stderr}${error.message}` }));
    child.on("close", (code) => done({ code: code ?? 1, stdout, stderr }));
    child.stdin.end(input);
  });
}

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
const reference = JSON.parse(await readFile(resolve(root, assets.model.reference), "utf8"));
const report = {
  kind: "browser-neural-parity",
  generated: new Date().toISOString(),
  browser: null,
  ort_version: assets.ort_version,
  requested_provider: provider,
  model: {
    sha256: assets.model.sha256,
    bytes: assets.model.bytes,
    gzip_bytes: assets.model.gzip_bytes ?? null,
    validated: assets.model.validated ?? false,
    ruleset: assets.model.ruleset,
  },
  failures: [],
  notes: [],
};

let browser = null;
let context = null;
try {
  browser = await chromium.launch({ executablePath: process.env.SPIKE_CHROMIUM || undefined });
  report.browser = browser.version();
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
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

  let load = null;
  try {
    // Call the client directly so a provider/adapter failure propagates with its
    // error name and code instead of being folded into a UI state.
    load = await page.evaluate(
      async (requestedProvider) => {
        const { assets } = await window.neural.assets();
        return window.neural.client.load("./" + assets.model.file, {
          provider: requestedProvider,
          size: assets.model.size,
          ruleset: assets.model.ruleset,
          expectedSha256: assets.model.sha256,
        });
      },
      provider,
    );
    report.load = load;
  } catch (error) {
    report.load_error = String(error?.message ?? error);
    report.load_error_code = error?.code ?? null;
  }

  if (load) {
    const observedOutputs = [];
    for (const entry of reference.cases) {
      if (entry.terminal) {
        observedOutputs.push({ position: entry.position, skipped: true });
        continue;
      }
      const observed = await page.evaluate(
        (payload) => {
          const client = window.neural.client;
          return client.infer(payload.planes, payload.size, payload.legal);
        },
        { planes: entry.planes, size: assets.model.size, legal: entry.legal },
      );
      observedOutputs.push({
        position: entry.position,
        policy_logits: observed.policy_logits,
        value: observed.value,
        policy_shape: observed.policy_shape,
        value_shape: observed.value_shape,
        legal: observed.legal,
        legal_probabilities: observed.legal_probabilities,
        illegal_mass_raw: observed.illegal_mass_raw,
        run_ms: observed.run_ms,
        tensor_prepare_ms: observed.tensor_prepare_ms,
      });
    }
    if (simulateCorruption) {
      const target = observedOutputs.find((row) => row.policy_logits);
      if (target) {
        target.policy_logits[0] += 0.05;
        report.notes.push("simulated corruption applied to the first observed logit");
      }
    }
    const observed = {
      schema: 2,
      provider: `${provider}:${load.execution_placement}`,
      model_sha256: assets.model.sha256,
      outputs: observedOutputs,
    };
    report.observed_summary = {
      cases: observedOutputs.length,
      provider: observed.provider,
      execution_placement: load.execution_placement,
      gpu_execution_verified: load.gpu_execution_verified,
      provider_evidence: load.provider_evidence,
    };
    const comparisonPath = output
      ? resolve(dirname(resolve(output)), "neural-parity-comparison.json")
      : join(tmpdir(), `neural-parity-${process.pid}.json`);
    const comparison = await runWithStdin(
      python,
      [
        resolve(import.meta.dirname, "neural_parity.py"),
        resolve(root, assets.model.reference),
        "-",
        "--output",
        comparisonPath,
      ],
      JSON.stringify(observed),
    );
    report.comparison_exit_code = comparison.code;
    try {
      report.comparison = JSON.parse(await readFile(comparisonPath, "utf8"));
    } catch {
      report.comparison = { stderr: String(comparison.stderr).slice(-2000) };
    }
    if (comparison.code !== 0) {
      report.failures.push(
        ...(report.comparison.failures ?? ["browser parity comparison failed"]),
      );
      if (report.comparison.stderr) report.notes.push(report.comparison.stderr);
    }
    if (requireWebGPU && !load.gpu_execution_verified) {
      report.failures.push(
        `--require-webgpu: GPU execution was not verified (placement ${load.execution_placement})`,
      );
    }
  } else {
    report.failures.push(
      `model load or session creation failed: ${report.load_error ?? "unknown error"}`,
    );
  }
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
      provider: report.observed_summary?.provider ?? null,
      execution_placement: report.observed_summary?.execution_placement ?? null,
      gpu_execution_verified: report.observed_summary?.gpu_execution_verified ?? false,
      cases: report.comparison?.cases ?? 0,
      worst_logit_max_abs_error: report.comparison?.worst_logit_max_abs_error ?? null,
      worst_value_abs_error: report.comparison?.worst_value_abs_error ?? null,
      material_ranking_changes: report.comparison?.material_ranking_changes ?? null,
      failures: report.failures,
    },
    null,
    2,
  ),
);
process.exit(report.ok ? 0 : 1);
