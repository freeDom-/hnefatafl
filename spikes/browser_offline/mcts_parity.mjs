// Browser half of the neural MCTS parity suite.
//
// Level A: the staged search worker runs the shared deterministic evaluator, so
// native and browser use the same `mcts_context` generator with identical
// externally supplied evaluations. Exact equality is required.
//
// Level B: actual staged-model inference (browser ORT WASM/WebGPU) drives the
// same generator. Floating-point provider differences can legitimately diverge an
// MCTS trajectory; the comparator reports moves, values, visits and completed
// work rather than pretending unequal trees are equal.
//
// Usage:
//   node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
//     --native /tmp/mcts-native-a.json --level a --output /tmp/mcts-browser-a.json
//   node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
//     --native /tmp/mcts-native-b.json --level b --provider wasm \
//     --require-best-move-parity --output /tmp/mcts-browser-b.json
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
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
const nativePath = option("native", null);
const positionsFile = option("positions-file", null);
const output = option("output", null);
const provider = option("provider", "wasm");
const requireBestMove = flag("require-best-move-parity");
const mobile = flag("mobile");
const python = process.env.SPIKE_PYTHON || resolve(import.meta.dirname, "../../.venv/bin/python");

if (!nativePath) {
  console.error("mcts_parity: --native is required (run native_mcts_parity.py first)");
  process.exit(2);
}
if (!["wasm", "webgpu"].includes(provider)) {
  console.error("mcts_parity: --provider must be wasm or webgpu");
  process.exit(2);
}

function canonicalJson(value) {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalJson(value[key])]),
    );
  return value;
}

function sameJson(left, right) {
  return JSON.stringify(canonicalJson(left)) === JSON.stringify(canonicalJson(right));
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
  kind: "mcts-parity",
  schema: 1,
  level: null,
  runtime: "browser-pyodide",
  generated: new Date().toISOString(),
  browser: null,
  requested_provider: provider,
  engine_identity: null,
  evaluator: null,
  model_meta: null,
  workload_digest: null,
  positions: [],
  results: [],
  failures: [],
  notes: [],
};

function fail(message) {
  report.failures.push(message);
  console.error(`FAIL ${message}`);
}

function runProcess(command, args) {
  return new Promise((done) => {
    const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (error) => done({ code: 1, stdout, stderr: `${stderr}${error.message}` }));
    child.on("close", (code) => done({ code: code ?? 1, stdout, stderr }));
  });
}

let native;
let assets;
try {
  native = JSON.parse(await readFile(resolve(nativePath), "utf8"));
  if (native.kind !== "mcts-parity") throw new Error("native report kind is not mcts-parity");
  assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));
  if (!assets.search?.bundle_id) throw new Error("staged bundle has no search runtime");
  report.level = native.level;
  report.evaluator = native.evaluator;
  report.model_meta = native.model_meta;
  report.workload_digest = native.workload_digest;
  report.positions = native.positions;
  report.settings = native.settings;
  report.workload = native.workload;
  if (native.level === "b" && assets.model.sha256 !== native.model_meta?.onnx_sha256)
    throw new Error("staged model digest differs from the native reference model");
  if (native.level === "b" && !assets.model.validated)
    throw new Error("model artifact is not validated");
} catch (error) {
  fail(`staged/native validation failed: ${error?.message ?? error}`);
  report.ok = false;
  if (output) {
    await mkdir(dirname(resolve(output)), { recursive: true });
    await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
  }
  console.log(JSON.stringify({ ok: false, failures: report.failures }, null, 2));
  process.exit(1);
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
  await page.waitForFunction(() => window.neural && window.neural.ready, null, {
    timeout: 60000,
  });

  // The worker exposes its source identity and the canonical workload digest.
  const identity = await page.evaluate(async () => {
    const info = await window.neural.coordinator.infoAction();
    return info.python.engine_identity;
  });
  report.engine_identity = identity;
  // Independently validate the declared workload source: built-in fixtures are
  // reconstructed from the staged fixture file; custom workloads require and are
  // verified against the original serialized position file.
  const workloadSource = native.workload?.source;
  if (!workloadSource || !["fixtures", "custom"].includes(workloadSource.kind))
    throw new Error("native report has no supported workload source");
  const nativeNames = native.positions.map((entry) => entry.name);
  const samePositions = sameJson;
  if (workloadSource.kind === "fixtures") {
    const selection = native.workload.selection;
    if (!selection || selection.source !== "fixtures.jarl_positions")
      throw new Error("native fixture workload has no reproducible selection");
    const stagedPositions = JSON.parse(
      await readFile(resolve(root, "mcts-positions.json"), "utf8"),
    );
    const selected = [];
    const counted = {};
    for (const entry of stagedPositions) {
      if (!selection.rulesets.includes(entry.ruleset)) continue;
      if (selection.kinds && !selection.kinds.includes(entry.kind)) continue;
      if (selection.limit) {
        counted[entry.ruleset] = counted[entry.ruleset] ?? 0;
        if (counted[entry.ruleset] >= selection.limit) continue;
        counted[entry.ruleset] += 1;
      }
      selected.push(entry);
    }
    if (!sameJson(selected.map((entry) => entry.name), nativeNames))
      fail("native workload selection does not match the staged position fixtures");
    const projected = selected.map((entry) => ({
      name: entry.name,
      ruleset: entry.ruleset,
      kind: entry.kind,
      state: entry.state,
    }));
    if (!samePositions(projected, native.positions))
      fail("native fixture positions or repetition histories differ from the staged fixtures");
    if (!samePositions(native.workload.expected_names, nativeNames))
      fail("native workload expected names do not match its positions");
  } else {
    if (!positionsFile)
      throw new Error("a custom workload requires --positions-file for verification");
    const raw = await readFile(resolve(positionsFile));
    const { createHash } = await import("node:crypto");
    const digest = createHash("sha256").update(raw).digest("hex");
    if (typeof workloadSource.file_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(workloadSource.file_sha256))
      fail("custom workload does not declare a mandatory file digest");
    else if (workloadSource.file_sha256 !== digest)
      fail("custom position file digest does not match the native report");
    const filePositions = JSON.parse(raw.toString("utf8"));
    const normalized = filePositions.map((entry) => ({
      name: entry.name,
      ruleset: entry.ruleset ?? entry.state?.ruleset,
      kind: entry.kind ?? "custom",
      state: entry.state,
    }));
    if (!samePositions(normalized, native.positions))
      fail("native custom positions differ from the supplied position file");
    if (!samePositions(native.workload.expected_names, nativeNames))
      fail("custom workload expected names do not match its positions");
  }
  const settings = report.settings;
  const workload = await page.evaluate(
    async (payload) => {
      const envelope = await window.neural.coordinator._request(
        {
          action: "workload",
          level: payload.level,
          evaluator: payload.evaluator,
          settings: payload.settings,
          positions: payload.positions.map((entry) => entry.state),
          model_onnx_sha256: payload.modelSha256,
        },
        { timeoutMs: 15000 },
      );
      return envelope.event;
    },
    {
      level: report.level,
      evaluator: report.evaluator,
      settings,
      positions: report.positions,
      modelSha256: report.model_meta?.onnx_sha256 ?? null,
    },
  );
  report.workload_check = {
    worker_digest: workload.digest,
    native_digest: report.workload_digest,
    matches: workload.digest === report.workload_digest,
  };
  if (!report.workload_check.matches)
    fail("search worker computed a different workload digest than the native run");

  let capturedModel = null;
  if (report.level === "b") {
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
    report.load = {
      provider_requested: load.provider_requested,
      execution_placement: load.execution_placement,
      gpu_execution_verified: load.gpu_execution_verified,
      model_sha256: load.model_sha256,
      model_ruleset: load.model_ruleset,
    };
    capturedModel = {
      sha256: load.model_sha256,
      ruleset: load.model_ruleset,
      size: load.size,
    };
    if (capturedModel.sha256 !== native.model_meta?.onnx_sha256)
      fail("loaded model digest differs from the native reference model");
    const expectedRuleset = native.positions[0]?.ruleset;
    if (expectedRuleset && capturedModel.ruleset !== expectedRuleset)
      fail(
        `staged model ruleset ${capturedModel.ruleset} does not match the workload ${expectedRuleset}`,
      );
  }

  for (const position of report.positions) {
    const record = {
      name: position.name,
      ruleset: position.ruleset,
      kind: position.kind,
      terminal: position.state?.winner !== null && position.state?.winner !== undefined,
    };
    const started = Date.now();
    try {
      if (report.level === "a") {
        const event = await page.evaluate(
          async (payload) => {
            const result = await window.neural.coordinator.parity({
              state: payload.state,
              settings: payload.settings,
              trace: true,
            });
            const { python: _python, ...rest } = result;
            return rest;
          },
          { state: position.state, settings },
        );
        record.elapsed_ms = Date.now() - started;
        if (event.kind !== "complete") throw new Error(`event kind ${event.kind}`);
        record.ok = true;
        record.kind_event = event.kind;
        record.analysis = event.analysis;
        record.search = event.search;
        delete record.search?.trace;
        record.path = event.search?.position_identity ?? null;
      } else {
        const result = await page.evaluate(
          async (payload) => {
            const outcome = await window.neural.coordinator.search({
              state: payload.state,
              settings: payload.settings,
              model: payload.model,
              trace: true,
            });
            return outcome;
          },
          { state: position.state, settings, model: capturedModel },
        );
        record.elapsed_ms = Date.now() - started;
        record.ok = true;
        record.kind_event = "complete";
        record.analysis = result.analysis;
        record.search = result.search;
        delete record.search?.trace;
        record.path = result.search?.position_identity ?? null;
        record.coordinator = result.search?.coordinator ?? null;
      }
    } catch (error) {
      record.ok = false;
      record.error = String(error?.message ?? error);
      record.code = error?.code ?? null;
      fail(`${position.name}: ${record.error}`);
    }
    report.results.push(record);
  }

  // Compare with the single shared comparator implementation.
  const browserReportPath = resolve(output ?? join(tmpdir(), `mcts-browser-${process.pid}.json`));
  await mkdir(dirname(browserReportPath), { recursive: true });
  await writeFile(browserReportPath, JSON.stringify(report, null, 2) + "\n");
  const comparisonPath = browserReportPath.replace(/\.json$/, "-comparison.json");
  const args = [resolve(import.meta.dirname, "mcts_compare.py"), resolve(nativePath), browserReportPath, "--output", comparisonPath];
  if (requireBestMove) args.push("--require-best-move-parity");
  if (positionsFile) args.push("--positions-file", resolve(positionsFile));
  const comparison = await runProcess(python, args);
  report.comparison = await readFile(comparisonPath, "utf8")
    .then((text) => JSON.parse(text))
    .catch(() => ({ failures: [`comparator produced no report: ${comparison.stderr.slice(-400)}`] }));
  report.comparison_exit_code = comparison.code;
  report.comparison_failures =
    comparison.code === 0 ? [] : (report.comparison.failures ?? ["parity comparison failed"]);
  report.comparison_ok = comparison.code === 0;

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

report.ok =
  report.failures.length === 0 && report.comparison_ok !== false;
if (output) {
  await mkdir(dirname(resolve(output)), { recursive: true });
  await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
}
console.log(
  JSON.stringify(
    {
      ok: report.ok,
      level: report.level,
      provider: report.load?.execution_placement ?? null,
      positions: report.results.length,
      workload_digest_matches: report.workload_check?.matches ?? false,
      acceptance: report.comparison?.acceptance ?? null,
      execution_validity: report.comparison?.execution_validity?.ok ?? null,
      deterministic_parity: report.comparison?.deterministic_parity?.ok ?? null,
      exact_cases: report.comparison?.deterministic_parity?.exact_cases ?? null,
      best_move_parity: report.comparison?.numerical_comparison?.best_move_parity ?? null,
      best_move_matches: report.comparison?.numerical_comparison?.best_move_matches ?? null,
      workload_complete: report.comparison?.workload_completion?.complete ?? null,
      failures: [...report.failures, ...(report.comparison_failures ?? [])],
      notes: report.comparison?.notes ?? [],
    },
    null,
    2,
  ),
);
process.exit(report.ok ? 0 : 1);
