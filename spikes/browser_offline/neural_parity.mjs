// Browser neural numerical-parity gate for the isolated spike.
//
// Serves a staged neural directory (prepare_neural.py), runs the *actual*
// exported model through ORT Web, and compares the **actual returned policy**
// (logits, value, legal indices and order, legal-only probabilities, shapes,
// dtypes, model identity) against native reference vectors using the canonical
// Python comparator (neural_parity.py). It exits non-zero on any mandatory
// parity failure.
//
// Usage:
//   node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
//     --provider wasm --output /tmp/neural-parity-wasm.json
//   node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
//     --provider webgpu --require-webgpu
//
// `--simulate-corruption <kind>` perturbs one reported field so the test suite
// can demonstrate that the gate detects a regression. This is never used for
// acceptance evidence.
import { createHash } from "node:crypto";
import { mkdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, extname, join, resolve, sep } from "node:path";
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
const corruption = option("corrupt", null);
const requireWebGPU = flag("require-webgpu");
const mobile = flag("mobile");

function usageError(message) {
  console.error(`neural_parity: ${message}`);
  process.exit(2);
}
if (!["wasm", "webgpu"].includes(provider)) usageError("--provider must be wasm or webgpu");
const CORRUPTIONS = new Set([
  "logit",
  "value",
  "legal",
  "order",
  "probabilities",
  "policy-shape",
  "value-shape",
  "model",
  "batch",
]);
if (corruption !== null && !CORRUPTIONS.has(corruption))
  usageError(`--corrupt must be one of ${[...CORRUPTIONS].join(", ")}`);

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

const report = {
  kind: "browser-neural-parity",
  schema: 3,
  generated: new Date().toISOString(),
  browser: null,
  ort_version: null,
  requested_provider: provider,
  simulated_corruption: simulateCorruptionName(),
  failures: [],
  notes: [],
};

function fail(message) {
  report.failures.push(message);
}

// Validate staged artifacts BEFORE starting any server or browser resource, so
// a missing or malformed manifest fails cleanly without a leaked server.
let assets;
let reference;
try {
  assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));
  const referencePath = resolve(root, assets.model.reference);
  const referenceRaw = await readFile(referencePath);
  report.model = {
    sha256: assets.model.sha256,
    bytes: assets.model.bytes,
    gzip_bytes: assets.model.gzip_bytes ?? null,
    validated: assets.model.validated ?? false,
    ruleset: assets.model.ruleset,
    reference_sha256: assets.model.reference_sha256,
  };
  report.ort_version = assets.ort_version;
  const referenceDigest = createHash("sha256").update(referenceRaw).digest("hex");
  reference = JSON.parse(referenceRaw.toString("utf8"));
  if (assets.model.reference_sha256 !== referenceDigest)
    throw new Error("reference-vector digest does not match the staged manifest");
  if (!assets.model.validated)
    throw new Error("model artifact is not validated; refusing mandatory parity");
  if (reference.model_sha256 !== assets.model.sha256)
    throw new Error("reference vectors were generated for different model bytes");
  if (reference.ruleset !== assets.model.ruleset)
    throw new Error("reference ruleset does not match the staged model");
  if (!Array.isArray(reference.cases) || reference.cases.length === 0)
    throw new Error("reference vectors contain no cases");
  const names = reference.cases.map((entry) => entry.position);
  if (new Set(names).size !== names.length) throw new Error("duplicate reference positions");
} catch (error) {
  report.failures.push(`staged artifact validation failed: ${error?.message ?? error}`);
  report.ok = false;
  if (output) {
    await mkdir(dirname(resolve(output)), { recursive: true });
    await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
  }
  console.log(JSON.stringify({ ok: false, failures: report.failures }, null, 2));
  process.exit(1);
}

let pathCounter = 0;
function uniquePath(label) {
  pathCounter += 1;
  return join(tmpdir(), `${label}-${process.pid}-${Date.now()}-${pathCounter}.json`);
}

let server = null;
let browser = null;
let context = null;
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
  context = await browser.newContext(
    mobile
      ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }
      : { viewport: { width: 1280, height: 900 } },
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

  let load = null;
  try {
    load = await page.evaluate(
      async (requestedProvider) => {
        const { assets: staged } = await window.neural.assets();
        return window.neural.client.load("./" + staged.model.file, {
          provider: requestedProvider,
          size: staged.model.size,
          ruleset: staged.model.ruleset,
          expectedSha256: staged.model.sha256,
        });
      },
      provider,
    );
    report.load = load;
  } catch (error) {
    report.load_error = String(error?.message ?? error);
    fail(`model load or session creation failed: ${report.load_error}`);
  }

  if (load) {
    const observedOutputs = [];
    for (const entry of reference.cases) {
      if (entry.terminal) {
        observedOutputs.push({ position: entry.position, skipped: true });
        continue;
      }
      const observed = await page.evaluate(
        (payload) =>
          window.neural.client.infer(
            payload.planes,
            payload.size,
            payload.legal,
            payload.ruleset,
          ),
        {
          planes: entry.planes,
          size: assets.model.size,
          legal: entry.legal,
          ruleset: assets.model.ruleset,
        },
      );
      observedOutputs.push(observedOutput(entry, observed, load));
    }

    applyCorruption(observedOutputs);
    const observed = {
      schema: 3,
      provider: `${provider}:${load.execution_placement}`,
      model_sha256: assets.model.sha256,
      outputs: observedOutputs,
    };
    report.observed_summary = {
      cases: observedOutputs.length,
      execution_placement: load.execution_placement,
      gpu_execution_verified: load.gpu_execution_verified,
      provider_evidence: load.provider_evidence,
    };

    const comparisonPath = uniquePath("neural-parity");
    const comparison = await runComparator(reference, observed, comparisonPath);
    report.comparison_exit_code = comparison.code;
    report.comparison = comparison.report;
    if (comparison.code !== 0) {
      fail(...(comparison.report.failures ?? ["browser parity comparison failed"]));
      if (comparison.stderr) report.notes.push(comparison.stderr);
    }

    // Distinct-position batched inference: every batched output must match its
    // own single-position reference, in order, for batch sizes 1..32.
    const nonTerminal = reference.cases.filter((entry) => !entry.terminal);
    const batchResults = [];
    for (const batch of [1, 2, 4, 8, 16, 32]) {
      const selected = Array.from(
        { length: batch },
        (_, index) => nonTerminal[index % nonTerminal.length],
      );
      let many;
      try {
        many = await page.evaluate(
          (payload) => window.neural.client.inferMany(payload.cases),
          {
            cases: selected.map((entry) => ({ planes: entry.planes, legal: entry.legal })),
          },
        );
      } catch (error) {
        fail(`batched inference (batch ${batch}) failed: ${error?.message ?? error}`);
        batchResults.push({ batch, ok: false });
        continue;
      }
      const subsetReference = {
        model_sha256: reference.model_sha256,
        tolerances: reference.tolerances,
        cases: selected,
      };
      const outputs = many.results.map((row, index) =>
        observedOutput(
          selected[index],
          {
            ...row,
            model_sha256: many.model_sha256,
            policy_shape: many.policy_shape,
            value_shape: many.value_shape,
            policy_dtype: many.policy_dtype,
            value_dtype: many.value_dtype,
          },
          load,
          index,
        ),
      );
      if (corruption === "batch") outputs.reverse();
      const batchedObserved = {
        schema: 3,
        provider: `${provider}:${load.execution_placement}`,
        model_sha256: assets.model.sha256,
        batch_count: corruption === "batch" ? batch + 1 : batch,
        outputs,
      };
      const batchedPath = uniquePath("neural-parity-batch");
      const comparisonBatched = await runComparator(
        subsetReference,
        batchedObserved,
        batchedPath,
      );
      const ok = comparisonBatched.code === 0;
      batchResults.push({ batch, ok });
      if (!ok) {
        fail(`batched parity failed at batch ${batch}`);
        if (batchResults.filter((row) => !row.ok).length > 1) break;
      }
    }
    report.batched = batchResults;
    if (requireWebGPU && !load.gpu_execution_verified) {
      fail(
        `--require-webgpu: GPU execution was not verified ` +
          `(placement ${load.execution_placement}; evidence ${JSON.stringify(
            load.provider_evidence,
          )})`,
      );
    }
  }

  report.page_errors = pageErrors;
  report.external_requests = external;
  if (pageErrors.length) fail(`page errors: ${pageErrors.length}`);
  if (external.length) fail(`external requests: ${external.length}`);
} catch (error) {
  fail(`harness error: ${error?.message ?? String(error)}`);
} finally {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  if (server) await new Promise((done) => server.close(done));
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
      provider: report.observed_summary?.execution_placement ?? null,
      gpu_execution_verified: report.observed_summary?.gpu_execution_verified ?? false,
      simulated_corruption: report.simulated_corruption,
      cases: report.comparison?.cases ?? 0,
      worst_logit_max_abs_error: report.comparison?.worst_logit_max_abs_error ?? null,
      worst_postprocess_abs_error: report.comparison?.worst_postprocess_abs_error ?? null,
      batched: report.batched ?? null,
      failures: report.failures,
    },
    null,
    2,
  ),
);
process.exit(report.ok ? 0 : 1);

function simulateCorruptionName() {
  return corruption;
}

function observedOutput(entry, observed, load, index = null) {
  return {
    position: entry.position,
    index,
    position_digest: entry.position_digest,
    model_sha256: observed.model_sha256 ?? load.model_sha256,
    policy_logits: observed.policy_logits,
    value: observed.value,
    policy_shape: observed.policy_shape,
    value_shape: observed.value_shape,
    policy_dtype: observed.policy_dtype,
    value_dtype: observed.value_dtype,
    legal: observed.legal,
    legal_probabilities: observed.legal_probabilities,
    illegal_mass_raw: observed.illegal_mass_raw,
  };
}

function applyCorruption(outputs) {
  if (!corruption) return;
  const target = outputs.find((row) => row.policy_logits);
  if (corruption === "batch" || !target) return;
  switch (corruption) {
    case "logit":
      target.policy_logits[0] += 0.05;
      break;
    case "value":
      target.value = (target.value ?? 0) + 0.05;
      break;
    case "legal":
      target.legal = [...target.legal.slice(1), target.legal[0]];
      break;
    case "order":
      target.legal = [...target.legal].reverse();
      target.legal_probabilities = [...target.legal_probabilities].reverse();
      break;
    case "probabilities":
      target.legal_probabilities = target.legal_probabilities.map((value, index) =>
        index === 0 ? value + 0.02 : value,
      );
      break;
    case "policy-shape":
      target.policy_shape = [1, 1];
      break;
    case "value-shape":
      target.value_shape = [2];
      break;
    case "model":
      target.model_sha256 = "0".repeat(64);
      break;
  }
}

async function runComparator(referenceVectors, observed, path) {
  const referencePath = uniquePath("reference");
  const observedPath = uniquePath("observed");
  await unlink(path).catch(() => {});
  await writeFile(referencePath, JSON.stringify(referenceVectors));
  await writeFile(observedPath, JSON.stringify(observed));
  const result = await runWithStdin(
    python,
    [
      resolve(import.meta.dirname, "neural_parity.py"),
      referencePath,
      observedPath,
      "--output",
      path,
    ],
    "",
  );
  await unlink(referencePath).catch(() => {});
  await unlink(observedPath).catch(() => {});
  let parsed;
  try {
    parsed = JSON.parse(await readFile(path, "utf8"));
  } catch {
    parsed = {
      failures: [
        `comparator produced no report (exit ${result.code}): ${String(result.stderr).slice(-500)}`,
      ],
    };
  }
  return { code: result.code, report: parsed, stderr: result.stderr };
}
