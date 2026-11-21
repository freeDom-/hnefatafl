// Browser neural MCTS benchmark: measures the complete cooperative search, not
// just inference throughput. Uses the same workload matrix as
// native_mcts_benchmark.py and reports actual completed simulations, throughput
// and a defensible timing breakdown with an explicit unattributed remainder.
//
// Usage:
//   node spikes/browser_offline/mcts_benchmark.mjs /tmp/neural-spike \
//     --provider wasm --quick --output /tmp/mcts-browser-quick.json
//   node spikes/browser_offline/mcts_benchmark.mjs /tmp/neural-spike \
//     --provider wasm --extended --simulations 128,256,512,1024 \
//     --native /tmp/mcts-native.json --output /tmp/mcts-browser.json
import { createHash } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep } from "node:path";
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
const nativePath = option("native", null);
const quick = flag("quick");
const extended = flag("extended");
const mobile = flag("mobile");
const requireWebGPU = flag("require-webgpu");
const seed = Number(option("seed", "7"));
const exploration = Number(option("exploration", "1.4"));
const python = process.env.SPIKE_PYTHON || resolve(import.meta.dirname, "../../.venv/bin/python");

const SIMULATION_BUDGETS = [128, 256, 512, 1024, 2048, 4096];
const TIME_BUDGETS = [0.25, 0.5, 1, 2, 5, 10];
const QUICK_PRESET = {
  kinds: ["opening", "tactical"],
  simulations: [128, 256],
  time_budgets: [0.5, 1],
  repetitions: 1,
  warmup: 1,
};
const EXTENDED_PRESET = {
  kinds: ["opening", "midgame", "endgame", "tactical"],
  simulations: SIMULATION_BUDGETS,
  time_budgets: TIME_BUDGETS,
  repetitions: 3,
  warmup: 1,
};
if (!["wasm", "webgpu"].includes(provider)) {
  console.error("mcts_benchmark: --provider must be wasm or webgpu");
  process.exit(2);
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
  kind: "mcts-benchmark",
  schema: 1,
  runtime: "browser-pyodide",
  generated: new Date().toISOString(),
  browser: null,
  requested_provider: provider,
  engine_identity: null,
  workload_digest: null,
  workload: null,
  model: null,
  cold: {},
  memory: {},
  records: [],
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

function positionsDigest(positions) {
  const payload = positions.map((entry) => [entry.name, entry.state]);
  return createHash("sha256")
    .update(JSON.stringify(canonicalJson(payload)))
    .digest("hex");
}

function settingsFor(mode, budget) {
  return mode === "fixed_simulations"
    ? { iterations: budget, time_limit: 10, seed, exploration }
    : { iterations: 4096, time_limit: budget, seed, exploration };
}

function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

let server = null;
let browser = null;
let context = null;
try {
  const assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));
  if (!assets.search?.bundle_id) throw new Error("staged bundle has no search runtime");
  if (!assets.model.validated) throw new Error("model artifact is not validated");
  const preset = extended ? EXTENDED_PRESET : QUICK_PRESET;
  if (!quick && !extended)
    report.notes.push("no preset flag: using the bounded quick workload");
  const modes = option("mode", "both") === "both" ? ["fixed_simulations", "fixed_time"] : [option("mode")];
  const simulations = option("simulations", null)
    ? option("simulations").split(",").map(Number)
    : preset.simulations;
  const timeBudgets = option("time-budgets", null)
    ? option("time-budgets").split(",").map(Number)
    : preset.time_budgets;
  const cases = [];
  for (const mode of modes) {
    const budgets = mode === "fixed_simulations" ? simulations : timeBudgets;
    for (const budget of budgets) cases.push({ mode, budget });
  }
  const positionsRaw = JSON.parse(await readFile(resolve(root, "mcts-positions.json"), "utf8"));
  const positions = positionsRaw.filter(
    (entry) => entry.ruleset === assets.model.ruleset && preset.kinds.includes(entry.kind),
  );
  if (!positions.length) throw new Error("no benchmark positions for the staged model ruleset");
  const normalizedPositions = positions.map((entry) => ({
    name: entry.name,
    ruleset: entry.ruleset,
    kind: entry.kind,
    state: entry.state,
  }));
  report.workload = {
    schema: 1,
    source: {
      kind: "fixtures",
      version: 1,
      fixtures: "fixtures.jarl_positions",
    },
    selection: {
      source: "fixtures.jarl_positions",
      version: 1,
      rulesets: [assets.model.ruleset],
      kinds: preset.kinds,
      limit: null,
    },
    positions: normalizedPositions,
    expected_names: normalizedPositions.map((entry) => entry.name),
    positions_sha256: positionsDigest(normalizedPositions),
    cases,
    seed,
    exploration,
    repetitions: preset.repetitions,
    warmup: preset.warmup,
  };

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
  const navigationStarted = Date.now();
  await page.goto(origin + "/neural.html");
  await page.waitForFunction(() => window.neural && window.neural.ready, null, {
    timeout: 90000,
  });
  report.cold.page_ready_ms = Date.now() - navigationStarted;

  const identity = await page.evaluate(
    async (requestedProvider) => {
      const started = performance.now();
      const info = await window.neural.coordinator.infoAction();
      const staged = await window.neural.meta();
      const load = await window.neural.client.load("./" + staged.model.file, {
        provider: requestedProvider,
        size: staged.model.size,
        ruleset: staged.model.ruleset,
        expectedSha256: staged.model.sha256,
      });
      return {
        engine_identity: info.python.engine_identity,
        search_worker: info.worker,
        model_load_ms: performance.now() - started,
        load: {
          provider_requested: load.provider_requested,
          execution_placement: load.execution_placement,
          gpu_execution_verified: load.gpu_execution_verified,
          model_sha256: load.model_sha256,
          model_ruleset: load.model_ruleset,
          size: load.size,
        },
      };
    },
    provider,
  );
  report.cold.model_load_ms = identity.model_load_ms;
  report.engine_identity = identity.engine_identity;
  report.search_worker = identity.search_worker;
  report.model = {
    onnx_sha256: identity.load.model_sha256,
    ruleset: identity.load.model_ruleset,
    size: identity.load.size,
    provider_requested: identity.load.provider_requested,
    execution_placement: identity.load.execution_placement,
    gpu_execution_verified: identity.load.gpu_execution_verified,
  };
  if (requireWebGPU) {
    const accepted =
      identity.load.provider_requested === "webgpu" &&
      !["wasm", "cpu-fallback"].includes(identity.load.execution_placement);
    if (!accepted) fail("--require-webgpu: WebGPU-requested inference was not accepted");
  }
  const workload = await page.evaluate(
    async (payload) => {
      const envelope = await window.neural.coordinator._request(
        {
          action: "benchmark_workload",
          positions: payload.positions.map((entry) => entry.state),
          cases: payload.cases,
          seed: payload.seed,
          exploration: payload.exploration,
          model_onnx_sha256: payload.modelSha256,
        },
        { timeoutMs: 15000 },
      );
      return envelope.event;
    },
    {
      positions,
      cases,
      seed,
      exploration,
      modelSha256: report.model.onnx_sha256,
    },
  );
  report.workload_digest = workload.digest;
  if (nativePath) {
    const native = JSON.parse(await readFile(resolve(nativePath), "utf8"));
    if (!native.workload?.selection)
      fail("native benchmark report has no reproducible workload selection");
    else {
      const expected = native.workload.positions;
      if (
        JSON.stringify(expected.map((entry) => entry.name)) !==
        JSON.stringify(report.workload.positions.map((entry) => entry.name))
      )
        fail("native benchmark positions do not match the staged selection");
      if (JSON.stringify(native.workload.selection) !== JSON.stringify(report.workload.selection))
        fail("native benchmark selection differs from the browser selection");
      // Adopt the native workload definition so the comparator validates one
      // canonical matrix (positions, settings and repetitions).
      report.workload = native.workload;
    }
    report.workload_check = {
      native_digest: native.workload_digest,
      matches: native.workload_digest === workload.digest,
    };
    if (!report.workload_check.matches)
      fail("benchmark workload digest differs from the native reference report");
  }
  const memoryPage = await page.evaluate(() => ({
    js_heap_used_bytes: performance.memory?.usedJSHeapSize ?? null,
    js_heap_limit_bytes: performance.memory?.jsHeapSizeLimit ?? null,
  }));
  report.memory = {
    page: memoryPage,
    search_worker_wasm_heap_capacity_bytes:
      identity.search_worker?.heap_capacity_bytes ?? null,
    neural_worker_wasm_heap_capacity_bytes: null,
    neural_worker_note: "not exposed by the pinned ORT Web build",
  };

  const capturedModel = {
    sha256: identity.load.model_sha256,
    ruleset: identity.load.model_ruleset,
    size: identity.load.size,
  };
  async function runSearch(position, settings, { warm, repetition, mode, budget }) {
    const record = {
      runtime: "browser-pyodide",
      provider: identity.load.execution_placement,
      ruleset: position.ruleset,
      position: position.name,
      kind: position.kind,
      mode,
      budget,
      repetition,
      warm,
      requested_simulations: settings.iterations,
    };
    const started = Date.now();
    try {
      const result = await page.evaluate(
        async (payload) =>
          window.neural.coordinator.search({
            state: payload.state,
            settings: payload.settings,
            model: payload.model,
          }),
        { state: position.state, settings, model: capturedModel },
      );
      const elapsed = Date.now() - started;
      const search = result.search;
      const timing = search.timing ?? {};
      const coordinator = search.coordinator ?? {};
      const workerMs =
        (timing.legal_generation_ms ?? 0) +
        (timing.encoding_ms ?? 0) +
        (timing.validation_ms ?? 0);
      record.ok = true;
      record.completed_simulations = search.completed_simulations;
      record.completion_reason = search.completion_reason;
      record.deadline_cutoff = search.deadline_cutoff;
      record.neural_requests = search.neural_requests;
      record.prediction_reuses = search.prediction_reuses;
      record.root_children = search.root_children;
      record.elapsed_ms = elapsed;
      record.coordinator_ms = coordinator.end_to_end_ms;
      record.neural_wait_ms = coordinator.neural_wait_ms;
      record.step_ms = coordinator.step_ms;
      record.bridge_overhead_ms = coordinator.bridge_overhead_ms;
      record.python_action_ms = coordinator.python_action_ms;
      record.unattributed_ms = coordinator.unattributed_ms;
      record.tensor_prepare_ms = coordinator.tensor_prepare_ms;
      record.inference_execution_ms = coordinator.inference_execution_ms;
      record.encode_ms = timing.encoding_ms ?? null;
      record.legal_generation_ms = timing.legal_generation_ms ?? null;
      record.state_validation_ms = timing.validation_ms ?? null;
      record.traversal_ms = Math.max(0, (coordinator.python_action_ms ?? 0) - workerMs);
      record.inferences = coordinator.inferences;
      record.peak_pending_inferences = 1; // sequential driver: never more than one
      record.deadline_overshoot_ms =
        mode === "fixed_time" ? Math.max(0, elapsed - budget * 1000) : 0;
      record.simulations_per_second =
        elapsed > 0 ? (search.completed_simulations / elapsed) * 1000 : null;
      record.search_value = result.analysis?.value ?? null;
      record.analysis_source = result.analysis?.source ?? null;
      record.search_id = search.search_id;
    } catch (error) {
      record.ok = false;
      record.error = String(error?.message ?? error);
      record.code = error?.code ?? null;
      record.elapsed_ms = Date.now() - started;
      fail(`${position.name}/${mode}/${budget}: ${record.error}`);
    }
    return record;
  }

  for (const position of positions) {
    for (const item of cases) {
      const settings = settingsFor(item.mode, item.budget);
      for (let warmup = 0; warmup < preset.warmup; warmup += 1)
        await runSearch(position, settings, {
          warm: true,
          repetition: -(warmup + 1),
          mode: item.mode,
          budget: item.budget,
        });
      for (let repetition = 0; repetition < preset.repetitions; repetition += 1)
        report.records.push(
          await runSearch(position, settings, {
            warm: true,
            repetition,
            mode: item.mode,
            budget: item.budget,
          }),
        );
    }
  }

  const fixedWorkRows = report.records.filter(
    (record) => record.mode === "fixed_simulations" && record.ok,
  );
  for (const record of fixedWorkRows)
    if (record.completed_simulations !== record.requested_simulations)
      fail(
        `${record.position}/${record.budget}: completed ${record.completed_simulations} of ${record.requested_simulations} simulations`,
      );
  const rates = fixedWorkRows.map((record) => record.simulations_per_second);
  report.throughput = {
    fixed_simulations_cases: fixedWorkRows.length,
    median_simulations_per_second: median(rates),
    aspirational_target_simulations_per_second: 512,
  };

  if (output) await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
  if (nativePath) {
    if (!output) throw new Error("--native comparison requires --output");
    const comparisonPath = output.replace(/\.json$/, "-comparison.json");
    const args = [
      resolve(import.meta.dirname, "mcts_benchmark.py"),
      resolve(nativePath),
      resolve(output),
    ];
    if (comparisonPath) args.push("--output", resolve(comparisonPath));
    const comparison = await runProcess(python, args);
    report.comparison_exit_code = comparison.code;
    if (comparisonPath)
      report.comparison = await readFile(comparisonPath, "utf8")
        .then((text) => JSON.parse(text))
        .catch(() => ({ failures: ["comparator produced no report"] }));
    report.comparison_failures =
      comparison.code === 0 ? [] : (report.comparison?.failures ?? ["benchmark comparison failed"]);
    report.comparison_ok = comparison.code === 0;
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
      provider: report.model?.execution_placement ?? null,
      records: report.records.length,
      workload_digest_matches: report.workload_check?.matches ?? null,
      median_simulations_per_second: report.throughput?.median_simulations_per_second ?? null,
      acceptance: report.comparison?.acceptance ?? null,
      equal_work_cases: report.comparison?.performance?.equal_work_cases ?? null,
      median_browser_to_native_ratio:
        report.comparison?.performance?.median_browser_to_native_ratio ?? null,
      failures: report.failures,
    },
    null,
    2,
  ),
);
process.exit(report.ok ? 0 : 1);
