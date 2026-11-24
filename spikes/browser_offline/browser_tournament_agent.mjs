// Line-protocol browser tournament agent for `tournament.py --run`.
//
// Keeps one Playwright browser, page and staged neural session alive for the whole
// tournament. Each request runs the existing browser search coordinator once and
// returns the best move with the matching request id. Transport, identity and
// cancellation remain owned by the established browser instrumentation.
//
// Usage:
//   node spikes/browser_offline/browser_tournament_agent.mjs /tmp/neural-spike \
//     --provider wasm
import { readFile, stat } from "node:fs/promises";
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
const provider = option("provider", "wasm");
if (!["wasm", "webgpu"].includes(provider)) {
  console.error("browser_tournament_agent: --provider must be wasm or webgpu");
  process.exit(2);
}
const PROTOCOL_VERSION = 2;

let server = null;
let browser = null;
let context = null;
let page = null;
let model = null;
let queue = Promise.resolve();

function respond(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

function fail(message) {
  console.error(`browser_tournament_agent: ${message}`);
  process.exit(2);
}

async function readLines(handler) {
  let buffer = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    buffer += chunk;
    let index = buffer.indexOf("\n");
    while (index >= 0) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (line) queue = queue.then(() => handler(line));
      await queue;
      index = buffer.indexOf("\n");
    }
  }
}

async function start() {
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
        provider_requested: load.provider_requested,
        execution_placement: load.execution_placement,
      };
    },
    { ...staged.model, provider },
  );
  const sidecar = JSON.parse(
    await readFile(resolve(root, "model", "model.json"), "utf8"),
  );
  const engine = await page.evaluate(async () => {
    const info = await window.neural.coordinator.infoAction();
    return info.python.engine_identity;
  });
  const handshake = {
    kind: "handshake",
    protocol: PROTOCOL_VERSION,
    agent: {
      protocol: PROTOCOL_VERSION,
      kind: "browser-neural",
      runtime: "browser-pyodide",
      provider: model.provider_requested,
      execution_placement: model.execution_placement,
      algorithm: "mcts",
      engine_sha256: engine?.sha256 ?? null,
      model_sha256: model.sha256,
      weights_sha256: sidecar?.source?.weights_sha256 ?? null,
      ruleset: model.ruleset,
      size: model.size,
      settings_support: { iterations: true, time_limit: true, depth: false },
      modes: ["fixed-work", "fixed-time"],
    },
  };
  process.stdout.write(JSON.stringify(handshake) + "\n");
  console.error(
    `browser_tournament_agent: ready model=${model.sha256.slice(0, 12)} ` +
      `placement=${model.execution_placement}`,
  );
}

async function handle(line) {
  let request;
  try {
    request = JSON.parse(line);
  } catch {
    return fail("request is not valid JSON");
  }
  try {
    if (typeof request.id !== "string" || !request.id) throw new Error("request id is missing");
    if (!Number.isInteger(request.seed) || request.seed < 0)
      throw new Error("request seed is missing");
    const { mode: executionMode, ...requestedSettings } = request.settings ?? {};
    const settings = { ...requestedSettings, seed: request.seed };
    const started = Date.now();
    const result = await page.evaluate(
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
      { state: request.state, settings, model },
    );
    if (!result?.best_move) throw new Error("search produced no best move");
    const search = result.search ?? {};
    const effective = result.settings;
    if (!effective || !Number.isInteger(effective.iterations))
      throw new Error("search returned no effective normalized settings");
    const observedProvider = search.provider ?? null;
    const inferences = Number(search.coordinator?.inferences ?? 0);
    if (inferences > 0 && !observedProvider)
      throw new Error("search performed inference without a provider observation");
    const evidence = {
      settings: {
        mode: executionMode ?? "fixed-work",
        iterations: effective.iterations,
        time_limit: effective.time_limit,
        exploration: effective.exploration,
        seed: effective.seed,
      },
      seed: request.seed,
      algorithm: "mcts",
      provider: observedProvider?.execution_placement ?? null,
      provider_requested: observedProvider?.requested ?? null,
      provider_observed: observedProvider !== null,
      neural_inferences: inferences,
      gpu_execution_verified: observedProvider?.gpu_execution_verified === true,
      model_sha256: model.sha256,
      completed_simulations: search.completed_simulations ?? null,
      depth: null,
      completion_reason: search.completion_reason ?? "completed",
      elapsed_ms: Date.now() - started,
    };
    respond({ id: request.id, move: result.best_move, evidence });
  } catch (error) {
    console.error(`browser_tournament_agent: ${error?.message ?? error}`);
    process.exit(2);
  }
}

async function shutdown() {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  if (server) await new Promise((done) => server.close(done));
}

try {
  await start();
  process.on("SIGTERM", () => {
    shutdown().finally(() => process.exit(0));
  });
  await readLines(handle);
} catch (error) {
  fail(`harness error: ${error?.message ?? error}`);
} finally {
  await shutdown();
}
