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
  recordBatchSuccess,
  recordCleanupFailure,
  recordFailure,
  recordSuccess,
  recordWorkers,
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
const cacheEntries = Number(
  option("cache-entries", mode === "cache-churn" ? "4" : "8192"),
);
const cacheBytes = Number(
  option("cache-bytes", String(mode === "cache-churn" ? 64 * 1024 : 64 * 1024 * 1024)),
);
const overwrite = flag("overwrite");
const failRecoverySearch = flag("fail-recovery-search");
const failCoordinatorDispose = flag("fail-coordinator-dispose");
const heapGcProbe = flag("heap-gc-probe");
const replacementModelFile = option("replacement-model-file", null);
const replacementModelSha = option("replacement-model-sha256", null);
const replacementModelFile2 = option("replacement-model-file-2", null);
const replacementModelSha2 = option("replacement-model-sha256-2", null);
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
if (!["wasm", "webgpu"].includes(provider)) {
  console.error("sustained_workload: --provider must be wasm or webgpu");
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
let cdp = null;
let model = null;
let modelDescriptor = null;
let originalDescriptor = null;
let positions = [];
let cursor = 0;
let lastBatchStats = null;
let pendingTransition = null;
const replacementTargets = [];
const counters = newCounters();
const completionReasons = {};
const searchPlacements = new Set();
const modelPlacements = new Set();
const modelTransitions = [];
const lifecycleEvents = [];

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
  modelPlacements.add(`${model.provider_requested}/${model.execution_placement}`);
  const workers = await page.evaluate(() => {
    const stats = window.neural.coordinator.workerStats();
    return {
      search_workers: stats.search_workers ?? 0,
      inference_workers: window.neural.client.worker ? 1 : 0,
    };
  });
  recordWorkers(counters, workers);
  if (cache) {
    await page.evaluate(
      ({ entries, bytes }) =>
        window.neural.client.configureInferenceCache({
          enabled: true,
          maxEntries: entries,
          maxBytes: bytes,
        }),
      { entries: cacheEntries, bytes: cacheBytes },
    );
  }
  modelDescriptor = {
    file: selectedFile,
    sha256: model.sha256,
    ruleset: model.ruleset,
    size: model.size,
  };
  if (!originalDescriptor) originalDescriptor = { ...modelDescriptor };
  if (!replacementTargets.length) {
    replacementTargets.push({ ...originalDescriptor });
    replacementTargets.push({ file: replacementModelFile, sha256: replacementModelSha });
    if (replacementModelFile2 && replacementModelSha2)
      replacementTargets.push({ file: replacementModelFile2, sha256: replacementModelSha2 });
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

function recordOutcome(outcome) {
  const counted = recordSuccess(counters, outcome, {
    provider,
    settings: outcome.requested_settings,
  });
  if (counted.provider) {
    searchPlacements.add(
      `${counted.provider.requested}/${counted.provider.execution_placement}`,
    );
  }
  completionReasons[counted.reason] = (completionReasons[counted.reason] ?? 0) + 1;
  return counted;
}

async function search(position) {
  const requestedSettings = settingsFor(counters.completed_requests + 7);
  return page.evaluate(
    async (payload) => {
      const outcome = await window.neural.coordinator.search({
        state: payload.state,
        settings: payload.settings,
        model: payload.model,
        provider: payload.provider,
        trace: false,
      });
      return {
        best_move: outcome.analysis?.best_move,
        search: outcome.search,
        settings: outcome.settings,
        requested_settings: payload.settings,
      };
    },
    {
      state: position.state,
      settings: requestedSettings,
      provider: { requested: provider },
      model,
      failCoordinatorDispose,
    },
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
      const coordinators = [];
      const cleanupFailures = [];
      let result = null;
      try {
        for (const _ of payload.positions) {
          const coordinator = new NeuralSearchCoordinator({ neural: window.neural.client });
          coordinators.push(coordinator);
        }
        await Promise.all(coordinators.map((coordinator) => coordinator.load()));
        const primary = window.neural.coordinator.workerStats();
        const workers = {
          search_workers:
            coordinators.filter((coordinator) => coordinator.worker).length +
            (primary.search_workers ?? 0),
          inference_workers: window.neural.client.worker ? 1 : 0,
        };
        const started = performance.now();
        const outcomes = await Promise.all(
          coordinators.map(async (coordinator, index) => {
            const searchStarted = performance.now();
            const outcome = await coordinator.search({
              state: payload.positions[index].state,
              settings: payload.settings,
              model: payload.model,
              provider: payload.provider,
            });
            return {
              best_move: outcome.analysis?.best_move,
              search: outcome.search,
              settings: outcome.settings,
              requested_settings: payload.settings,
              elapsed_ms: performance.now() - searchStarted,
            };
          }),
        );
        const elapsed_ms = performance.now() - started;
        result = {
          batching: window.neural.client.batchStatistics(),
          workers,
          elapsed_ms,
          outcomes,
        };
      } finally {
        for (const [index, coordinator] of coordinators.entries()) {
          try {
            if (payload.failCoordinatorDispose) {
              throw new Error("injected coordinator disposal failure");
            }
            await coordinator.dispose?.();
          } catch (error) {
            cleanupFailures.push(`coordinator ${index}: ${error?.message ?? error}`);
          }
        }
        if (!coordinators.length) {
          // No temporary coordinators were created; nothing to dispose.
        }
      }
      return { ...result, cleanup_failures: cleanupFailures };
    },
    {
      positions: positionsForRound,
      settings: settingsFor(counters.completed_requests + 13),
      provider: { requested: provider },
      model,
      failCoordinatorDispose,
    },
  );
}

async function replaceModel(position) {
  let target = replacementTargets.length
    ? replacementTargets[(counters.model_replacements + 1) % replacementTargets.length]
    : null;
  if (!target || target.sha256 === model.sha256) return false;
  const previous = { ...modelDescriptor };
  const previousSha = model.sha256;
  await loadModel(target);
  if (
    model.ruleset !== originalDescriptor.ruleset ||
    model.size !== originalDescriptor.size
  ) {
    await loadModel(previous);
    throw new Error("replacement model ruleset changed unexpectedly");
  }
  if (cache) {
    const stats = await page.evaluate(() => window.neural.client.inferenceCacheStats());
    if ((stats?.entries ?? 0) > 0) {
      counters.cache_invalidation_failures += 1;
      await loadModel(previous);
      throw new Error("inference cache was not invalidated by the model replacement");
    }
  }
  let recovery;
  try {
    recovery = await search(position);
    if (recovery?.best_move === undefined) throw new Error("replacement recovery has no move");
    recordOutcome(recovery);
  } catch (error) {
    await loadModel(previous).catch(() => {});
    throw error;
  }
  counters.model_replacements += 1;
  const transition = {
    sequence: counters.model_replacements,
    from: previousSha,
    to: model.sha256,
    timestamp_ms: Date.now(),
    recovered: true,
    cache_invalidated: cache ? true : null,
  };
  modelTransitions.push(transition);
  if (modelTransitions.length > 64) modelTransitions.shift();
  pendingTransition = transition;
  return true;
}

async function telemetry(final = false, cleanupComplete = false) {
  let live = null;
  try {
    if (heapGcProbe && cdp) {
      await cdp.send("HeapProfiler.enable");
      await cdp.send("HeapProfiler.collectGarbage");
    }
    live = await page.evaluate(async (payload) => {
      const cacheStats = await window.neural.client.inferenceCacheStats().catch(() => null);
      const workers =
        typeof window.neural.coordinator.workerStats === "function"
          ? window.neural.coordinator.workerStats()
          : null;
      return {
        js_heap_used_bytes: performance.memory?.usedJSHeapSize ?? null,
        js_heap_after_gc_bytes: payload.gcProbe
          ? (performance.memory?.usedJSHeapSize ?? null)
          : null,
        heap_gc_probe: payload.gcProbe,
        cache_entries: cacheStats?.entries ?? null,
        cache_bytes: cacheStats?.bytes ?? null,
        cache_evictions: cacheStats?.evictions ?? null,
        search_workers: workers === null ? null : (workers.search_workers ?? 0),
        inference_workers: workers === null ? null : (workers.inference_workers ?? 0),
        workers:
          workers === null
            ? null
            : (workers.search_workers ?? 0) + (workers.inference_workers ?? 0),
      };
    }, { gcProbe: heapGcProbe });
  } catch {
    live = null;
  }
  if (live && Number.isInteger(live.cache_evictions))
    counters.cache_evictions = Math.max(counters.cache_evictions, live.cache_evictions);
  if (
    live &&
    Number.isInteger(live.search_workers) &&
    Number.isInteger(live.inference_workers)
  ) {
    recordWorkers(counters, {
      search_workers: live.search_workers,
      inference_workers: live.inference_workers,
    });
  }
  const extra = {
    completion_reasons: { ...completionReasons },
    provider_placements: [...searchPlacements],
    model_provider_placements: [...modelPlacements],
    lifecycle_events: lifecycleEvents.slice(-64),
    workload_complete: final,
  };
  if (final) {
    extra.workload_exit_status = exitCode(counters);
    extra.cleanup_complete = cleanupComplete;
  }
  if (pendingTransition) {
    extra.model_transition = { ...pendingTransition };
    pendingTransition = null;
  }
  const record = telemetryRecord(
    counters,
    { session, modelSha256: model.sha256, workload: "sustained-analysis" },
    extra,
  );
  if (live) {
    for (const [key, value] of Object.entries(live))
      if (value !== null && value !== undefined) record[key] = value;
  }
  if (modelTransitions.length) record.model_transitions = modelTransitions.slice();
  if (telemetryFile) await appendFile(telemetryFile, JSON.stringify(record) + "\n");
  if (progressFile) await appendFile(progressFile, String(counters.completed_requests) + "\n");
}

async function recoverySearch(position, label) {
  if (failRecoverySearch) {
    throw new Error(`injected recovery failure after ${label}`);
  }
  const outcome = await search(position);
  recordOutcome(outcome);
  return outcome;
}

async function replaceSearchWorker(position) {
  const generations = await page.evaluate(() => {
    const coordinator = window.neural.coordinator;
    const before = coordinator.generation;
    coordinator._replaceWorker(new Error("sustained probe worker replacement"));
    return { before, after: coordinator.generation, disposed: coordinator.disposed };
  });
  if (generations.disposed || generations.after <= generations.before) {
    throw new Error("search worker replacement did not create a new generation");
  }
  await recoverySearch(position, "worker replacement");
  counters.worker_replacements += 1;
  lifecycleEvents.push({
    kind: "worker-replacement",
    worker_generation_before: generations.before,
    worker_generation_after: generations.after,
    recovered: true,
  });
}

async function disposeClient(position) {
  const evidence = await page.evaluate(async () => {
    const previousCoordinator = window.neural.coordinator;
    window.neural.shutdown("sustained probe client disposal");
    const disposed = previousCoordinator.disposed === true;
    window.neural.restoreAfterBfcache();
    return {
      disposed,
      recreated: window.neural.coordinator !== previousCoordinator,
    };
  });
  if (!evidence.disposed || !evidence.recreated) {
    throw new Error("client disposal did not dispose and recreate the runtime");
  }
  await loadModel();
  await recoverySearch(position, "client disposal");
  counters.client_disposals += 1;
  lifecycleEvents.push({
    kind: "client-disposal",
    disposed: evidence.disposed,
    recreated: evidence.recreated,
    recovered: true,
  });
}

async function reloadModel(position) {
  const before = model.sha256;
  await loadModel();
  if (model.sha256 !== before) throw new Error("model reload changed model identity");
  await recoverySearch(position, "model reload");
  counters.reloads += 1;
  lifecycleEvents.push({ kind: "model-reload", recovered: true });
}

async function cancelAndRecover(position) {
  const cancellation = await cancelRound(position);
  if (cancellation?.cancelled !== true && cancellation?.state !== "drained") {
    throw new Error("cancellation was not observed");
  }
  await recoverySearch(position, "cancellation recovery");
  counters.cancellations += 1;
  lifecycleEvents.push({ kind: "cancel-restart", recovered: true });
}

async function runIteration() {
  const position = await nextPosition();
  if (mode === "cancel-restart") {
    const outcome = await search(position);
    recordOutcome(outcome);
    if (counters.completed_requests % 5 === 0) await cancelAndRecover(position);
  } else if (mode === "model-reload") {
    const outcome = await search(position);
    recordOutcome(outcome);
    if (counters.completed_requests % 5 === 0) await reloadModel(position);
  } else if (mode === "model-replacement") {
    const outcome = await search(position);
    recordOutcome(outcome);
    if (counters.completed_requests % 5 === 0) await replaceModel(position);
  } else if (mode === "worker-replacement") {
    const outcome = await search(position);
    recordOutcome(outcome);
    if (counters.completed_requests % 5 === 0) await replaceSearchWorker(position);
  } else if (mode === "client-disposal") {
    const outcome = await search(position);
    recordOutcome(outcome);
    if (counters.completed_requests % 5 === 0) await disposeClient(position);
  } else if (mode === "cache-churn") {
    const outcome = await search(position);
    recordOutcome(outcome);
  } else if (mode === "batching") {
    const first = position;
    const second = await nextPosition();
    const result = await batchRound([first, second]);
    recordBatch(counters, result.batching, lastBatchStats, result.elapsed_ms);
    lastBatchStats = {
      batches: result.batching.batches,
      multi_request_batches: result.batching.multi_request_batches ?? 0,
      observed_max_batch: result.batching.observed_max_batch,
    };
    recordWorkers(counters, result.workers);
    for (const failure of result.cleanup_failures ?? []) {
      console.error(`sustained_workload: coordinator cleanup failed: ${failure}`);
      recordCleanupFailure(counters);
    }
    let valid = true;
    for (const outcome of result.outcomes ?? []) {
      try {
        recordOutcome(outcome);
      } catch (error) {
        console.error(`sustained_workload: batched search failed: ${error?.message ?? error}`);
        recordFailure(counters);
        valid = false;
      }
    }
    if (valid && result.outcomes?.length) {
      const latencies = result.outcomes
        .map((outcome) => outcome.elapsed_ms)
        .filter((value) => Number.isFinite(value));
      if (latencies.length) recordBatchSuccess(counters, Math.max(...latencies));
    }
  } else {
    const outcome = await search(position);
    recordOutcome(outcome);
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
  if (heapGcProbe) cdp = await context.newCDPSession(page);
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
        mode,
        workload: "sustained-analysis",
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
  const cleanupFailures = [];
  try {
    await context?.close();
  } catch (error) {
    cleanupFailures.push(`browser context cleanup failed: ${error?.message ?? error}`);
  }
  context = null;
  try {
    await browser?.close();
  } catch (error) {
    cleanupFailures.push(`browser cleanup failed: ${error?.message ?? error}`);
  }
  browser = null;
  if (server) {
    await new Promise((done) => server.close(done));
    server = null;
  }
  for (const failure of cleanupFailures) {
    console.error(`sustained_workload: cleanup failed: ${failure}`);
    recordCleanupFailure(counters);
  }
  await telemetry(true, cleanupFailures.length === 0);
  const summary = {
    kind: "browser-sustained-workload",
    schema: 2,
    session_id: session,
    mode,
    runtime: "browser-pyodide",
    model,
    provider_requested: provider,
    provider_placements: [...searchPlacements],
    model_provider_placements: [...modelPlacements],
    provider_observed: model !== null,
    model_sha256: model?.sha256 ?? null,
    model_transitions: modelTransitions,
    duration_s: (Date.now() - started) / 1000,
    workload: "sustained-analysis",
    workload_complete: true,
    cleanup_complete: cleanupFailures.length === 0,
    cleanup_failures: cleanupFailures,
    counters,
    completion_reasons: completionReasons,
    lifecycle_events: lifecycleEvents,
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
