// Sustained browser-analysis workload driver for Phase 3C-B memory profiles.
//
// Runs repeated real browser neural-MCTS searches and optional lifecycle actions
// while emitting identity-bound live telemetry. Measurement tooling only.
import { appendFile, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import {
  exitCode,
  newCounters,
  recordBatch,
  recordFailure,
  recordSuccess,
  selectAnalysisPositions,
  telemetryRecord,
} from "./sustained_workload_core.mjs";

const mime = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
  ".bin": "application/octet-stream",
  ".zip": "application/zip",
};
const MODES = [
  "repeated",
  "cancel-restart",
  "model-reload",
  "model-replacement",
  "worker-replacement",
  "client-disposal",
  "cache-churn",
  "batching",
];
const argv = process.argv.slice(2);
const root = resolve(argv.find((arg) => !arg.startsWith("--")) || "work/phase0/neural");
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const flag = (name) => argv.includes(`--${name}`);
const duration = Number(option("duration", "60"));
const interval = Number(option("interval", "0"));
const mode = option("mode", "repeated");
const session = option("session", null);
const provider = option("provider", "wasm");
const telemetryFile = option("telemetry", null);
const progressFile = option("progress", null);
const readyFile = option("ready", null);
const output = option("output", null);
const iterations = Number(option("iterations", "64"));
const timeLimit = Number(option("time-limit", "2"));
const exploration = Number(option("exploration", "1.4"));
const cache = flag("cache");
const overwrite = flag("overwrite");
const replacementModelFile = option("replacement-model-file", null);
const replacementModelSha = option("replacement-model-sha256", null);
if (!MODES.includes(mode)) {
  console.error(`sustained_workload: unsupported --mode (expected ${MODES.join(", ")})`);
  process.exit(2);
}
if (!session) {
  console.error("sustained_workload: --session is required");
  process.exit(2);
}
if (!Number.isFinite(duration) || duration <= 0 || !Number.isInteger(iterations) || iterations < 1) {
  console.error("sustained_workload: invalid duration or iterations");
  process.exit(2);
}
if (mode === "model-replacement" && (!replacementModelFile || !replacementModelSha)) {
  console.error(
    "sustained_workload: model-replacement requires --replacement-model-file and --replacement-model-sha256",
  );
  process.exit(2);
}

let server = null;
let browser = null;
let context = null;
let page = null;
let model = null;
let positions = [];
let cursor = 0;
const counters = newCounters();
const completionReasons = {};
const placements = new Set();
const modelTransitions = [];

async function loadModel({ file = null, sha256 = null } = {}) {
  const staged = await page.evaluate(async () => window.neural.meta());
  const selectedFile = file ?? staged.model.file;
  model = await page.evaluate(
    async (payload) => {
      const load = await window.neural.client.load("./" + payload.file, {
        provider: payload.provider,
        size: payload.size,
        ruleset: payload.ruleset,
        expectedSha256: payload.sha256,
      });
      return {
        sha256: load.model_sha256,
        ruleset: load.model_ruleset,
        size: load.size,
        file: payload.file,
        provider_requested: load.provider_requested,
        execution_placement: load.execution_placement,
      };
    },
    {
      file: selectedFile,
      sha256: sha256 ?? staged.model.sha256,
      provider,
      size: staged.model.size,
      ruleset: staged.model.ruleset,
    },
  );
  placements.add(`${model.provider_requested}/${model.execution_placement}`);
  if (cache) {
    await page.evaluate(() =>
      window.neural.client.configureInferenceCache({
        enabled: true,
        maxEntries: 8192,
        maxBytes: 64 * 1024 * 1024,
      }),
    );
  }
}

async function nextPosition() {
  const position = positions[cursor % positions.length];
  cursor += 1;
  return position;
}

function settingsFor(seed) {
  return { algorithm: "mcts", iterations, time_limit: timeLimit, exploration, seed };
}

async function search(position) {
  return page.evaluate(
    async (payload) => {
      const outcome = await window.neural.coordinator.search({
        state: payload.state,
        settings: payload.settings,
        model: payload.model,
        trace: false,
      });
      return {
        best_move: outcome.analysis?.best_move,
        search: outcome.search,
        settings: outcome.settings,
      };
    },
    { state: position.state, settings: settingsFor(counters.completed_requests + 7), model },
  );
}

async function cancelRound(position) {
  return page.evaluate(
    async (payload) => {
      const coordinator = window.neural.coordinator;
      const pending = coordinator.search({
        state: payload.state,
        settings: payload.settings,
        model: payload.model,
        trace: false,
      });
      // Attach rejection handling immediately: a legitimate cancellation may
      // reject the pending search before cancel() resolves.
      pending.catch(() => {});
      await new Promise((done) => setTimeout(done, 25));
      const cancellation = await coordinator.cancel("sustained probe cancellation");
      try {
        await pending;
      } catch {
        // expected for a cancelled search
      }
      return cancellation;
    },
    {
      state: position.state,
      settings: {
        algorithm: "mcts",
        iterations: Math.min(4096, iterations * 8),
        time_limit: Math.max(timeLimit, 2),
        exploration,
        seed: counters.completed_requests + 11,
      },
      model,
    },
  );
}

async function batchRound(positionsForRound) {
  return page.evaluate(
    async (payload) => {
      window.neural.client.enableBatching({ maxBatch: 4, maxQueue: 8 });
      const { NeuralSearchCoordinator } = await import("/search-client.js");
      const coordinators = payload.positions.map(
        () => new NeuralSearchCoordinator({ neural: window.neural.client }),
      );
      await Promise.all(coordinators.map((coordinator) => coordinator.load()));
      const started = performance.now();
      const outcomes = await Promise.all(
        coordinators.map((coordinator, index) =>
          coordinator.search({
            state: payload.positions[index].state,
            settings: payload.settings,
            model: payload.model,
          }),
        ),
      );
      const elapsed_ms = performance.now() - started;
      const batching = window.neural.client.batchStatistics();
      await Promise.all(coordinators.map((coordinator) => coordinator.dispose()));
      return {
        batching,
        elapsed_ms,
        outcomes: outcomes.map((outcome) => ({
          best_move: outcome.analysis?.best_move,
          search: outcome.search,
          settings: outcome.settings,
        })),
      };
    },
    {
      positions: positionsForRound,
      settings: settingsFor(counters.completed_requests + 13),
      model,
    },
  );
}

async function telemetry() {
  let live = null;
  try {
    live = await page.evaluate(async () => {
      const cacheStats = await window.neural.client.inferenceCacheStats().catch(() => null);
      const workers =
        typeof window.neural.coordinator.workerStats === "function"
          ? window.neural.coordinator.workerStats()
          : null;
      return {
        js_heap_used_bytes: performance.memory?.usedJSHeapSize ?? null,
        cache_entries: cacheStats?.entries ?? null,
        cache_bytes: cacheStats?.bytes ?? null,
        cache_evictions: cacheStats?.evictions ?? null,
        workers:
          workers === null
            ? null
            : (workers.search_workers ?? 0) + (workers.inference_workers ?? 0),
      };
    });
  } catch {
    live = null;
  }
  const record = telemetryRecord(
    counters,
    { session, modelSha256: model.sha256, workload: "sustained-analysis" },
    { completion_reasons: { ...completionReasons }, provider_placements: [...placements] },
  );
  if (live) {
    for (const [key, value] of Object.entries(live))
      if (value !== null && value !== undefined) record[key] = value;
  }
  if (modelTransitions.length) record.model_transitions = modelTransitions.slice();
  if (telemetryFile) await appendFile(telemetryFile, JSON.stringify(record) + "\n");
  if (progressFile) await appendFile(progressFile, String(counters.completed_requests) + "\n");
}

async function runIteration() {
  const position = await nextPosition();
  if (mode === "cancel-restart") {
    const cancellation = await cancelRound(position);
    if (cancellation?.cancelled === true || cancellation?.state === "drained")
      counters.cancellations += 1;
    const outcome = await search(position);
    const result = recordSuccess(counters, outcome);
    completionReasons[result.reason] = (completionReasons[result.reason] ?? 0) + 1;
  } else if (mode === "model-reload") {
    const outcome = await search(position);
    const result = recordSuccess(counters, outcome);
    completionReasons[result.reason] = (completionReasons[result.reason] ?? 0) + 1;
    if (counters.completed_requests % 5 === 0) {
      await loadModel();
      counters.reloads += 1;
    }
  } else if (mode === "model-replacement") {
    const outcome = await search(position);
    const result = recordSuccess(counters, outcome);
    completionReasons[result.reason] = (completionReasons[result.reason] ?? 0) + 1;
    if (counters.completed_requests % 5 === 0) {
      const previous = model.sha256;
      await loadModel({ file: replacementModelFile, sha256: replacementModelSha });
      counters.model_replacements += 1;
      modelTransitions.push({ from: previous, to: model.sha256, at: Date.now() });
      if (cache) {
        const stats = await page.evaluate(() =>
          window.neural.client.inferenceCacheStats(),
        );
        if ((stats?.entries ?? 0) > 0) counters.cache_invalidation_failures += 1;
      }
    }
  } else if (mode === "worker-replacement" || mode === "client-disposal") {
    const outcome = await search(position);
    const result = recordSuccess(counters, outcome);
    completionReasons[result.reason] = (completionReasons[result.reason] ?? 0) + 1;
    if (counters.completed_requests % 5 === 0) {
      await page.evaluate(() => window.neural.shutdown("sustained probe replacement"));
      await page.evaluate(() => window.neural.restoreAfterBfcache());
      await loadModel();
      counters.worker_replacements += 1;
    }
  } else if (mode === "cache-churn") {
    const outcome = await search(position);
    const result = recordSuccess(counters, outcome);
    completionReasons[result.reason] = (completionReasons[result.reason] ?? 0) + 1;
    if (counters.completed_requests % 3 === 0) {
      await page.evaluate(() => window.neural.client.clearInferenceCache());
      counters.cache_clears += 1;
    }
  } else if (mode === "batching") {
    const first = position;
    const second = await nextPosition();
    const result = await batchRound([first, second]);
    for (const outcome of result.outcomes) {
      const counted = recordSuccess(counters, outcome);
      completionReasons[counted.reason] = (completionReasons[counted.reason] ?? 0) + 1;
    }
    recordBatch(counters, result.batching, result.elapsed_ms);
  } else {
    const outcome = await search(position);
    const result = recordSuccess(counters, outcome);
    completionReasons[result.reason] = (completionReasons[result.reason] ?? 0) + 1;
  }
}

async function run() {
  if (overwrite) {
    if (telemetryFile) await writeFile(telemetryFile, "");
    if (progressFile) await writeFile(progressFile, "");
    if (readyFile) await writeFile(readyFile, "");
  }
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
  const { chromium } = await import("../../frontend/node_modules/playwright/index.mjs");
  browser = await chromium.launch({ executablePath: process.env.SPIKE_CHROMIUM || undefined });
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return url.origin === origin ? route.continue() : route.abort();
  });
  page = await context.newPage();
  await page.goto(origin + "/neural.html");
  await page.waitForFunction(() => window.neural && window.neural.ready, null, { timeout: 60000 });
  await loadModel();
  if (mode === "model-replacement" && replacementModelSha !== model.sha256) {
    // The replacement identity is validated when first loaded; no event yet.
  }
  const stagedPositions = JSON.parse(
    await readFile(resolve(root, "mcts-positions.json"), "utf8"),
  );
  positions = selectAnalysisPositions(stagedPositions, model.ruleset);
  if (!positions.length)
    throw new Error("no nonterminal analysis positions for the staged ruleset");
  if (readyFile) {
    await writeFile(
      readyFile,
      JSON.stringify({
        ready: true,
        session,
        model_sha256: model.sha256,
        ruleset: model.ruleset,
        provider_requested: provider,
        execution_placement: model.execution_placement,
        positions: positions.map((entry) => ({ name: entry.name, kind: entry.kind })),
        modes: MODES,
      }) + "\n",
    );
  }
  const started = Date.now();
  const deadline = started + duration * 1000;
  while (Date.now() < deadline) {
    try {
      await runIteration();
    } catch (error) {
      recordFailure(counters);
      console.error(`sustained_workload: iteration failed: ${error?.message ?? error}`);
    }
    await telemetry();
    if (interval > 0) await new Promise((done) => setTimeout(done, interval * 1000));
  }
  const summary = {
    kind: "browser-sustained-workload",
    schema: 2,
    session_id: session,
    mode,
    runtime: "browser-pyodide",
    model,
    provider_requested: provider,
    provider_placements: [...placements],
    provider_observed: model !== null,
    model_sha256: model?.sha256 ?? null,
    model_transitions: modelTransitions,
    duration_s: (Date.now() - started) / 1000,
    workload: "sustained-analysis",
    counters,
    completion_reasons: completionReasons,
    positions: positions.map((entry) => ({ name: entry.name, kind: entry.kind })),
    authoritative: false,
  };
  if (output) await writeFile(resolve(output), JSON.stringify(summary, null, 2) + "\n");
  console.log(JSON.stringify(summary, null, 2));
  return exitCode(counters);
}

try {
  process.exitCode = await run();
} catch (error) {
  console.error(`sustained_workload: ${error?.message ?? error}`);
  process.exitCode = 1;
} finally {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  if (server) await new Promise((done) => server.close(done));
}
