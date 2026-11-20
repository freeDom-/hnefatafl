// Browser neural parity gate for the isolated spike (revision 4 contract).
//
// Validates the complete inference response: model/ruleset identity, request and
// position identity, policy/value tensors (data, shape, dtype), legal indices and
// order, legal-only probabilities, and independent batch count/index/order.
// Exits non-zero on any mandatory failure.
//
// Usage:
//   node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
//     --provider wasm --output /tmp/neural-parity-wasm.json
//   node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
//     --provider webgpu --require-webgpu
//   node ... --corrupt reorder   # deterministic negative check
//
// Corruption runs exit 1 when the deliberate corruption was detected (as
// intended) and exit 3 when it was NOT detected or rejected with the wrong
// error classification. ``rejection`` records the exact boundary, expected code
// and observed code for request-identity corruption, so a nonzero exit is never
// accepted on its own.
//
// CLI acceptance semantics:
//   --require-webgpu        require an obtained adapter, a WebGPU-requested
//                           session and successful numerical inference. Session
//                           placement is reported but strict GPU-only execution
//                           is NOT claimed.
//   --require-gpu-execution require independently verified actual GPU execution.
//                           The pinned ORT Web build exposes no per-operator
//                           placement API, so this currently fails explicitly.
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
const requireGpuExecution = flag("require-gpu-execution");
const mobile = flag("mobile");

const CORRUPTIONS = new Set([
  "logit",
  "value",
  "legal",
  "order",
  "probabilities",
  "policy-shape",
  "value-shape",
  "model",
  "batch-count",
  "missing-result",
  "extra-result",
  "duplicate-index",
  "missing-index",
  "wrong-index",
  "reorder",
  "position",
  "request-model",
  "request-ruleset",
  "request-size",
  "request-model-worker",
  "request-model-batch",
  "request-model-batch-worker",
]);
const SINGLE_CORRUPTIONS = new Set([
  "logit",
  "value",
  "legal",
  "order",
  "probabilities",
  "policy-shape",
  "value-shape",
  "model",
  "position",
]);
// Request-identity corruption kinds each target exactly one execution
// boundary, so a batch-only kind can never fail the preceding single-position
// assertion and leave the intended boundary unexercised.
const IDENTITY_BOUNDARIES = {
  "request-model": "single-client",
  "request-ruleset": "single-client",
  "request-size": "single-client",
  "request-model-worker": "single-worker",
  "request-model-batch": "batch-client",
  "request-model-batch-worker": "batch-worker",
};
const IDENTITY_ERROR_CODES = {
  "request-model": "MODEL_IDENTITY_MISMATCH",
  "request-ruleset": "RULESET_MISMATCH",
  "request-size": "BOARD_SIZE_MISMATCH",
  "request-model-worker": "MODEL_IDENTITY_MISMATCH",
  "request-model-batch": "MODEL_IDENTITY_MISMATCH",
  "request-model-batch-worker": "MODEL_IDENTITY_MISMATCH",
};
const REQUEST_CORRUPTIONS = new Set(Object.keys(IDENTITY_BOUNDARIES));
const BATCH_CORRUPTIONS = new Set([
  "batch-count",
  "missing-result",
  "extra-result",
  "duplicate-index",
  "missing-index",
  "wrong-index",
  "reorder",
  "position",
]);

function usageError(message) {
  console.error(`neural_parity: ${message}`);
  process.exit(2);
}
if (!["wasm", "webgpu"].includes(provider)) usageError("--provider must be wasm or webgpu");
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

function uniquePath(label) {
  return join(
    tmpdir(),
    `${label}-${process.pid}-${Date.now()}-${Math.floor(Math.random() * 1e6)}.json`,
  );
}

const report = {
  kind: "browser-neural-parity",
  schema: 4,
  generated: new Date().toISOString(),
  browser: null,
  ort_version: null,
  requested_provider: provider,
  simulated_corruption: corruption,
  mode: corruption === null ? "parity" : "corruption",
  rejection: null,
  failures: [],
  notes: [],
};

function fail(...messages) {
  report.failures.push(...messages);
}

// Validate staged artifacts before starting any server or browser resource.
let assets;
let reference;
try {
  assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));
  const referenceRaw = await readFile(resolve(root, assets.model.reference));
  const referenceDigest = createHash("sha256").update(referenceRaw).digest("hex");
  reference = JSON.parse(referenceRaw.toString("utf8"));
  report.model = {
    sha256: assets.model.sha256,
    bytes: assets.model.bytes,
    gzip_bytes: assets.model.gzip_bytes ?? null,
    validated: assets.model.validated ?? false,
    ruleset: assets.model.ruleset,
    reference_sha256: assets.model.reference_sha256,
  };
  report.ort_version = assets.ort_version;
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
  fail(`staged artifact validation failed: ${error?.message ?? error}`);
  report.ok = false;
  if (output) {
    await mkdir(dirname(resolve(output)), { recursive: true });
    await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
  }
  console.log(JSON.stringify({ ok: false, failures: report.failures }, null, 2));
  process.exit(1);
}

const nonTerminal = reference.cases.filter((entry) => !entry.terminal);

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
    // Capture the exact model identity when the workload is prepared.
    const capturedModel = {
      sha256: load.model_sha256,
      ruleset: load.model_ruleset,
      size: load.size,
    };
    if (!capturedModel.sha256 || !capturedModel.ruleset || !Number.isInteger(capturedModel.size))
      fail("loaded model did not return a complete identity");

    if (IDENTITY_BOUNDARIES[corruption]) {
      // Identity corruptions are asserted at one exact boundary with an exact
      // expected error classification; the control request proves that the same
      // boundary succeeds with the correct identity.
      const boundary = IDENTITY_BOUNDARIES[corruption];
      const batch = boundary.startsWith("batch");
      const viaWorker = boundary.endsWith("worker");
      const expectedCode = IDENTITY_ERROR_CODES[corruption];
      const selected = Array.from(
        { length: 2 },
        (_, index) => nonTerminal[index % nonTerminal.length],
      );
      const attempt = async (model) =>
        page.evaluate(
          async (payload) => {
            try {
              const client = window.neural.client;
              if (payload.batch) {
                const request = {
                  action: "inferMany",
                  cases: payload.cases.map((entry) => ({
                    planes: entry.planes,
                    legal: entry.legal,
                    request_id: entry.position,
                    position_digest: entry.position_digest,
                  })),
                  model: payload.model,
                };
                if (payload.viaWorker) await client.request(request, { timeoutMs: 30000 });
                else await client.inferMany(request);
              } else {
                const entry = payload.cases[0];
                const request = {
                  action: "infer",
                  planes: entry.planes,
                  size: payload.model.size,
                  legal: entry.legal,
                  ruleset: payload.model.ruleset,
                  request_id: entry.position,
                  position_digest: entry.position_digest,
                  model: payload.model,
                };
                if (payload.viaWorker) await client.request(request, { timeoutMs: 30000 });
                else await client.infer(request);
              }
              return { rejected: false, code: null };
            } catch (error) {
              return { rejected: true, code: error?.code ?? null, message: String(error?.message ?? error) };
            }
          },
          { batch, viaWorker, cases: selected, model },
        );
      try {
        const bad = await attempt(corruptRequestModel(capturedModel, corruption));
        const control = await attempt(capturedModel);
        report.rejection = {
          corruption,
          boundary,
          expected_code: expectedCode,
          observed_code: bad.code,
          rejected: bad.rejected,
          control_accepted: control.rejected === false,
        };
        if (!bad.rejected) fail(`${corruption}: corrupted identity was accepted at ${boundary}`);
        else if (bad.code !== expectedCode)
          fail(
            `${corruption}: ${boundary} rejected with ${bad.code}, expected ${expectedCode}`,
          );
        if (control.rejected)
          fail(
            `${corruption}: control request with the correct identity failed at ${boundary}: ${control.code}`,
          );
      } catch (error) {
        fail(`identity corruption harness failure: ${error?.message ?? error}`);
      }
    }

    const observedOutputs = [];
    for (const entry of reference.cases) {
      if (entry.terminal) {
        // Harness-generated skip: no inference was requested for this position.
        observedOutputs.push({
          position: entry.position,
          skipped: true,
          harness_generated: true,
        });
        continue;
      }
      const observed = await page.evaluate(
        (payload) => window.neural.client.infer(payload.request),
        {
          request: {
            planes: entry.planes,
            size: capturedModel.size,
            legal: entry.legal,
            ruleset: capturedModel.ruleset,
            request_id: entry.position,
            position_digest: entry.position_digest,
            model: capturedModel,
          },
        },
      );
      if (SINGLE_CORRUPTIONS.has(corruption) && observedOutputs.length === 0)
        corruptSingle(observed, corruption);
      observedOutputs.push(observedOutput(observed));
    }

    const observed = {
      schema: 4,
      provider: `${provider}:${load.execution_placement}`,
      model_sha256: load.model_sha256,
      model_ruleset: load.model_ruleset,
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

    // Independent batch contract, including index/order/association.
    const batchResults = [];
    for (const batch of [1, 2, 4, 8, 16, 32]) {
      const selected = Array.from(
        { length: batch },
        (_, index) => nonTerminal[index % nonTerminal.length],
      );
      let many;
      try {
        many = await page.evaluate(
          (payload) => window.neural.client.inferMany(payload.request),
          {
            request: {
              cases: selected.map((entry) => ({
                planes: entry.planes,
                legal: entry.legal,
                request_id: entry.position,
                position_digest: entry.position_digest,
              })),
              model: capturedModel,
            },
          },
        );
      } catch (error) {
        fail(`batched inference (batch ${batch}) failed: ${error?.message ?? error}`);
        batchResults.push({ batch, ok: false });
        continue;
      }
      const subsetReference = {
        model_sha256: reference.model_sha256,
        ruleset: reference.ruleset,
        tolerances: reference.tolerances,
        cases: selected,
      };
      const batchedObserved = {
        schema: 4,
        provider: `${provider}:${load.execution_placement}`,
        model_sha256: many.model_sha256,
        model_ruleset: many.model_ruleset,
        batch_count: many.batch,
        results_count: many.results.length,
        outputs: many.results.map((row) => batchedOutput(row, many)),
      };
      if (BATCH_CORRUPTIONS.has(corruption) && batch === 2)
        corruptBatch(batchedObserved, corruption);
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

    const webgpuAccepted =
      load.provider_requested === "webgpu" &&
      !["wasm", "cpu-fallback"].includes(load.execution_placement) &&
      report.comparison?.ok === true;
    if (requireWebGPU && !webgpuAccepted) {
      fail(
        `--require-webgpu: WebGPU-requested numerical inference not accepted ` +
          `(placement ${load.execution_placement})`,
      );
    }
    if (requireGpuExecution && load.gpu_execution_verified !== true) {
      fail(
        `--require-gpu-execution: actual GPU execution was not independently verified ` +
          `(placement ${load.execution_placement}; the pinned ORT Web build exposes no ` +
          `per-operator placement API)`,
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
// A corruption run intentionally exits 1 when detection succeeded. The exit code
// is only meaningful together with the recorded verdict/rejection: request
// identity corruptions must be rejected with the exact expected code at the
// intended boundary, and every other corruption must be caught by validation.
let exitCode = report.ok ? 0 : 1;
if (corruption !== null) {
  const identityBoundary = corruption in IDENTITY_BOUNDARIES;
  const detected = identityBoundary
    ? Boolean(
        report.rejection &&
          report.rejection.rejected &&
          report.rejection.observed_code === report.rejection.expected_code &&
          report.rejection.control_accepted &&
          report.rejection.boundary === IDENTITY_BOUNDARIES[corruption],
      )
    : report.failures.length > 0;
  report.corruption_detected = detected;
  report.verdict = detected ? "corruption-detected" : "corruption-not-detected";
  exitCode = detected ? 1 : 3;
} else {
  report.verdict = report.ok ? "pass" : "fail";
}
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
      verdict: report.verdict,
      rejection: report.rejection,
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
process.exit(exitCode);

function observedOutput(observed) {
  // Copy only fields actually returned by the worker; never substitute expected
  // values for missing observed metadata.
  return {
    position: observed.request_id,
    index: observed.index,
    request_id: observed.request_id,
    position_digest: observed.position_digest,
    model_sha256: observed.model_sha256,
    model_ruleset: observed.model_ruleset,
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

function batchedOutput(row, many) {
  return {
    position: row.request_id,
    index: row.index,
    request_id: row.request_id,
    position_digest: row.position_digest,
    model_sha256: many.model_sha256,
    model_ruleset: many.model_ruleset,
    policy_logits: row.policy_logits,
    value: row.value,
    policy_shape: many.policy_shape,
    value_shape: many.value_shape,
    policy_dtype: many.policy_dtype,
    value_dtype: many.value_dtype,
    legal: row.legal,
    legal_probabilities: row.legal_probabilities,
    illegal_mass_raw: row.illegal_mass_raw,
  };
}

function corruptRequestModel(model, kind) {
  if (kind.startsWith("request-model")) return { ...model, sha256: "0".repeat(64) };
  if (kind === "request-ruleset") return { ...model, ruleset: "tablut", size: 9 };
  if (kind === "request-size") return { ...model, size: model.size === 7 ? 9 : 7 };
  return model;
}

function corruptSingle(observed, kind) {
  switch (kind) {
    case "logit":
      observed.policy_logits[0] += 0.05;
      break;
    case "value":
      observed.value = (observed.value ?? 0) + 0.05;
      break;
    case "legal":
      observed.legal = [...observed.legal.slice(1), observed.legal[0]];
      break;
    case "order":
      observed.legal = [...observed.legal].reverse();
      observed.legal_probabilities = [...observed.legal_probabilities].reverse();
      break;
    case "probabilities":
      observed.legal_probabilities = observed.legal_probabilities.map((value, index) =>
        index === 0 ? value + 0.02 : value,
      );
      break;
    case "policy-shape":
      observed.policy_shape = [1, 1];
      break;
    case "value-shape":
      observed.value_shape = [2];
      break;
    case "model":
      observed.model_sha256 = "0".repeat(64);
      break;
    case "position":
      observed.position_digest = "0".repeat(64);
      break;
  }
}

function corruptBatch(observed, kind) {
  switch (kind) {
    case "batch-count":
      observed.batch_count += 1;
      break;
    case "missing-result":
      observed.outputs.pop();
      observed.results_count = observed.outputs.length;
      break;
    case "extra-result":
      observed.outputs.push({
        ...observed.outputs[0],
        index: observed.outputs.length,
        request_id: "extra-result",
      });
      observed.results_count = observed.outputs.length;
      break;
    case "duplicate-index":
      observed.outputs[1].index = observed.outputs[0].index;
      break;
    case "missing-index":
      delete observed.outputs[0].index;
      break;
    case "wrong-index":
      observed.outputs[0].index = 1;
      break;
    case "reorder":
      observed.outputs.reverse();
      break;
    case "position": {
      const left = observed.outputs[0];
      const right = observed.outputs[1] ?? observed.outputs[0];
      [left.request_id, right.request_id] = [right.request_id, left.request_id];
      [left.position_digest, right.position_digest] = [
        right.position_digest,
        left.position_digest,
      ];
      break;
    }
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
