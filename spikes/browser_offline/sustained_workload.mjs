// Sustained browser-analysis workload driver for Phase 3C-B memory profiles.
//
// Runs repeated real browser neural-MCTS searches (and optional cancellation,
// model reload or worker replacement lifecycle actions) while appending live
// telemetry for memory_profile_collect.py. It is measurement tooling, not a
// production integration or a playing-strength claim.
//
// Usage:
//   node spikes/browser_offline/sustained_workload.mjs /tmp/neural-spike \
//     --duration 660 --session phase3cb --telemetry /tmp/telemetry.jsonl \
//     --progress /tmp/progress.txt --mode repeated --cache --output /tmp/summary.json
import { appendFile, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

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
const output = option("output", null);
const iterations = Number(option("iterations", "64"));
const timeLimit = Number(option("time-limit", "2"));
const exploration = Number(option("exploration", "1.4"));
const cache = flag("cache");
if (!["repeated", "cancel-restart", "model-reload", "worker-replacement"].includes(mode)) {
  console.error("sustained_workload: unsupported --mode");
  process.exit(2);
}
if (!session) {
  console.error("sustained_workload: --session is required");
  process.exit(2);
}
if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(iterations) || iterations < 1) {
  console.error("sustained_workload: invalid duration or iterations");
  process.exit(2);
}
if (telemetryFile && !progressFile) {
  console.error("sustained_workload: --telemetry requires --progress");
  process.exit(2);
}

let server = null;
let browser = null;
let context = null;
let page = null;
let model = null;
let positions = [];
let cursor = 0;
let completed = 0;
let cancellations = 0;
let reloads = 0;
let workerReplacements = 0;
let failures = 0;

async function loadModel() {
  const staged = await page.evaluate(async () => window.neural.meta());
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
    { ...staged.model, provider },
  );
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
    {
      state: position.state,
      settings: {
        algorithm: "mcts",
        iterations: payloadIterations(),
        time_limit: timeLimit,
        exploration,
        seed: completed + 7,
      },
      model,
    },
  );
}

function payloadIterations() {
  return iterations;
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
      await new Promise((done) => setTimeout(done, 25));
      const cancellation = await coordinator.cancel("sustained probe cancellation");
      let outcome = null;
      try {
        outcome = await pending;
      } catch {
        outcome = null;
      }
      return { cancellation, outcome };
    },
    {
      state: position.state,
      settings: {
        algorithm: "mcts",
        iterations: Math.min(4096, iterations * 8),
        time_limit: Math.max(timeLimit, 2),
        exploration,
        seed: completed + 11,
      },
      model,
    },
  );
}

async function emitTelemetry() {
  if (!telemetryFile) return;
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
        workers:
          workers === null
            ? null
            : (workers.search_workers ?? 0) + (workers.inference_workers ?? 0),
      };
    });
  } catch {
    live = null;
  }
  const record = {
    timestamp_ms: Date.now(),
    session_id: session,
    model_sha256: model.sha256,
    workload: "sustained-analysis",
    completed_requests: completed,
    events: {
      cancellations,
      reloads,
      worker_replacements: workerReplacements,
      batches: 0,
    },
  };
  if (live) {
    for (const [key, value] of Object.entries(live))
      if (value !== null && value !== undefined) record[key] = value;
  }
  await appendFile(telemetryFile, JSON.stringify(record) + "\n");
  if (progressFile) await appendFile(progressFile, String(completed) + "\n");
}

async function run() {
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
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return url.origin === origin ? route.continue() : route.abort();
  });
  page = await context.newPage();
  await page.goto(origin + "/neural.html");
  await page.waitForFunction(() => window.neural && window.neural.ready, null, { timeout: 60000 });
  await loadModel();
  const stagedPositions = JSON.parse(
    await readFile(resolve(root, "mcts-positions.json"), "utf8"),
  );
  positions = stagedPositions.filter((entry) => entry.ruleset === model.ruleset);
  if (!positions.length) throw new Error("no positions for the staged model ruleset");

  const started = Date.now();
  const deadline = started + duration * 1000;
  while (Date.now() < deadline) {
    try {
      const position = await nextPosition();
      if (mode === "cancel-restart") {
        const result = await cancelRound(position);
        if (result?.cancellation?.cancelled === true) cancellations += 1;
        else if (result?.cancellation?.state && result.cancellation.state !== "idle")
          cancellations += 1;
        await search(position);
        completed += 1;
      } else if (mode === "model-reload") {
        await search(position);
        completed += 1;
        if (completed % 10 === 0) {
          await loadModel();
          reloads += 1;
        }
      } else if (mode === "worker-replacement") {
        await search(position);
        completed += 1;
        if (completed % 15 === 0) {
          await page.evaluate(() => window.neural.shutdown("sustained probe replacement"));
          await page.evaluate(() => window.neural.restoreAfterBfcache());
          await loadModel();
          workerReplacements += 1;
        }
      } else {
        await search(position);
        completed += 1;
      }
      await emitTelemetry();
    } catch (error) {
      failures += 1;
      console.error(`sustained_workload: iteration failed: ${error?.message ?? error}`);
    }
    if (interval > 0) await new Promise((done) => setTimeout(done, interval * 1000));
  }
  const summary = {
    kind: "browser-sustained-workload",
    schema: 1,
    session_id: session,
    mode,
    runtime: "browser-pyodide",
    model,
    provider_requested: provider,
    execution_placement: model?.execution_placement ?? null,
    provider_observed: model !== null,
    model_sha256: model?.sha256 ?? null,
    duration_s: (Date.now() - started) / 1000,
    completed_requests: completed,
    cancellations,
    reloads,
    worker_replacements: workerReplacements,
    failures,
    authoritative: false,
  };
  if (output) await writeFile(resolve(output), JSON.stringify(summary, null, 2) + "\n");
  console.log(JSON.stringify(summary, null, 2));
}

try {
  await run();
} catch (error) {
  console.error(`sustained_workload: ${error?.message ?? error}`);
  process.exitCode = 1;
} finally {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  if (server) await new Promise((done) => server.close(done));
}
