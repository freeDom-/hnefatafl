/* Collect actual-browser full-logit vs legal-only search observations.
 *
 * This runner deliberately performs no acceptance judgement. It records the raw
 * full/compact results, then invokes path_parity.py, which applies the shared
 * Python engine's legality checks and assigns independent per-case failures.
 *
 * Usage:
 *   node spikes/browser_offline/test_search_path_parity.mjs STAGE [STAGE...] \
 *     --provider wasm --output /tmp/path-parity.json [--fixed-time]
 */
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { basename, dirname, extname, resolve, sep } from "node:path";
import { spawn } from "node:child_process";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const RULESETS = ["brandubh", "tablut", "copenhagen"];
const argv = process.argv.slice(2);
const valueOptions = new Set(["provider", "output", "simulations"]);
const roots = [];
for (let index = 0; index < argv.length; index += 1) {
  const argument = argv[index];
  if (argument.startsWith("--")) {
    if (valueOptions.has(argument.slice(2))) index += 1;
    continue;
  }
  roots.push(argument);
}
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const flag = (name) => argv.includes(`--${name}`);
const provider = option("provider", "wasm");
const output = option("output", null);
const simulations = Number(option("simulations", "64"));
const fixedTime = flag("fixed-time");
const injectPageError = flag("inject-page-error");
const python = process.env.SPIKE_PYTHON || resolve(import.meta.dirname, "../../.venv/bin/python");
const mime = {
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".html": "text/html",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
};
if (!roots.length) {
  console.error("usage: test_search_path_parity.mjs STAGE [STAGE...] [--provider wasm] [--output FILE]");
  process.exit(2);
}
if (!Number.isInteger(simulations) || simulations < 1 || simulations > 1024) {
  console.error("--simulations must be an integer in 1..1024");
  process.exit(2);
}

function sleep(ms) {
  return new Promise((done) => setTimeout(done, ms));
}

function runProcess(command, args) {
  return new Promise((resolveRun) => {
    const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("close", (code) => resolveRun({ code, stdout, stderr }));
  });
}

function addImmediateBrandubh(positions) {
  const size = 7;
  const board = Array(size * size).fill(".");
  board[1] = "K";
  board[3] = "A";
  board[3 * size + 3] = "D";
  board[5 * size + 5] = "A";
  positions.push({
    name: "brandubh-immediate-win",
    ruleset: "brandubh",
    kind: "tactical",
    state: {
      ruleset: "brandubh",
      board,
      turn: "attackers",
      ply: 0,
      winner: null,
      reason: null,
      history: [],
    },
  });
}

const report = {
  kind: "mcts-search-path-parity-observations",
  schema: 1,
  generated: new Date().toISOString(),
  browser: null,
  provider,
  fixed_time: fixedTime,
  mode: fixedTime ? "fixed_time" : "fixed_simulations",
  simulations,
  roots: roots.map((root) => basename(root)),
  expected_rulesets: [],
  models: [],
  expected_cases: [],
  cases: [],
  harness_failures: [],
};
const failures = report.harness_failures;

let browser = null;
const servers = [];
try {
  browser = await chromium.launch({ executablePath: process.env.SPIKE_CHROMIUM || undefined });
  report.browser = browser.version();
  let rootIndex = 0;
  for (const argument of roots) {
    const root = resolve(argument);
    rootIndex += 1;
    const assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));
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
    servers.push(server);
    await new Promise((done) => server.listen(0, "127.0.0.1", done));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const page = await browser.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    await page.goto(origin + "/neural.html");
    await page.waitForFunction(() => window.neural && window.neural.ready, null, { timeout: 90000 });
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
    const model = {
      sha256: load.model_sha256,
      ruleset: load.model_ruleset,
      size: load.size,
    };
    report.models.push({
      ruleset: model.ruleset,
      size: model.size,
      sha256: model.sha256,
      onnx_sha256: model.sha256,
      provider_requested: load.provider_requested,
      execution_placement: load.execution_placement,
      gpu_execution_verified: load.gpu_execution_verified === true,
    });
    const stagedPositions = await page.evaluate(() => window.neural.positions());
    const positions = stagedPositions
      .filter(
        (entry) =>
          entry.ruleset === model.ruleset &&
          ["opening", "tactical", "repetition", "terminal"].includes(entry.kind),
      )
      .map((entry) => ({
        name: entry.name,
        ruleset: entry.ruleset,
        kind: entry.kind,
        state: entry.state,
      }));
    if (model.ruleset === "brandubh") addImmediateBrandubh(positions);
    for (const entry of positions)
      report.expected_cases.push({ name: entry.name, ruleset: entry.ruleset });

    async function run(entry, compact, settings) {
      return page.evaluate(
        async (payload) => {
          window.neural.coordinator.compactInference = payload.compact;
          return window.neural.coordinator.search({
            state: payload.state,
            settings: payload.settings,
            model: payload.model,
          });
        },
        { state: entry.state, settings, model, compact },
      );
    }

    for (const entry of positions) {
      const terminal = entry.state.winner !== null && entry.state.winner !== undefined;
      const settings = fixedTime
        ? { iterations: 4096, time_limit: 0.25, seed: 7, exploration: 1.4 }
        : { iterations: simulations, time_limit: 10, seed: 7, exploration: 1.4 };
      const caseReport = {
        ruleset: entry.ruleset,
        position: entry.name,
        kind: entry.kind,
        terminal,
        mode: fixedTime ? "fixed_time" : "fixed_simulations",
        settings,
        state: entry.state,
      };
      try {
        const full = await run(entry, false, settings);
        await sleep(5);
        const compact = await run(entry, true, settings);
        caseReport.full = full;
        caseReport.compact = compact;
        caseReport.full_move = full.analysis?.best_move ?? null;
        caseReport.compact_move = compact.analysis?.best_move ?? null;
      } catch (error) {
        caseReport.execution_error = String(error?.message ?? error);
      }
      caseReport.ok = false; // Provisional; path_parity.py owns acceptance.
      report.cases.push(caseReport);
    }
    if (injectPageError && rootIndex === roots.length) {
      await page.evaluate(() => {
        setTimeout(() => {
          throw new Error("injected page error after final case");
        }, 0);
      });
      await sleep(100);
    }
    if (pageErrors.length) failures.push(`${model.ruleset}: page errors: ${pageErrors.join("; ")}`);
    await page.close();
  }
  report.expected_rulesets =
    roots.length >= 3 ? RULESETS : [...new Set(report.models.map((model) => model.ruleset))];
} catch (error) {
  failures.push(`harness error: ${error?.stack ?? error?.message ?? String(error)}`);
} finally {
  await browser?.close().catch(() => {});
  for (const server of servers) await new Promise((done) => server.close(done));
}

report.raw_ok = failures.length === 0;
const requestedOutput = output ? resolve(output) : null;
const provisional = requestedOutput
  ? requestedOutput.replace(/\.json$/, "-observations.json")
  : resolve(
      `/tmp/mcts-path-parity-${process.pid}-observations.json`,
    );
await mkdir(dirname(provisional), { recursive: true });
await writeFile(provisional, JSON.stringify(report, null, 2) + "\n");
const finalPath = requestedOutput ?? provisional.replace(/-observations\.json$/, ".json");
const validation = await runProcess(python, [
  resolve(import.meta.dirname, "path_parity.py"),
  provisional,
  "--output",
  finalPath,
]);
let validated = null;
try {
  validated = JSON.parse(await readFile(finalPath, "utf8"));
} catch {
  validated = {
    ok: false,
    cases: [],
    failures: [`path_parity.py produced no report: ${validation.stderr.slice(-400)}`],
  };
}
console.log(
  JSON.stringify(
    {
      ok: validated.ok === true && validation.code === 0,
      cases: validated.cases?.length ?? 0,
      failures: validated.failures ?? [],
    },
    null,
    2,
  ),
);
process.exit(validated.ok === true && validation.code === 0 ? 0 : 1);
