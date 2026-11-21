/* End-to-end browser search parity: full-logit vs legal-only inference.
 *
 * Runs the real coordinator and neural worker for the same model, position,
 * seed, exploration, budget and provider. The only difference is
 * coordinator.compactInference. Fixed-work cases require exact search equivalence
 * after removing timing/transport diagnostics; fixed-time cases only require
 * valid, deadline-consistent, legal results because their completed work differs.
 *
 * Usage:
 *   node spikes/browser_offline/test_search_path_parity.mjs STAGE [STAGE...] \
 *     --provider wasm --output /tmp/path-parity.json [--fixed-time]
 */
import assert from "node:assert/strict";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

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

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
      .join(",")}}`;
  return JSON.stringify(value);
}

function searchProjection(result) {
  const search = { ...(result.search ?? {}) };
  // Timing and transport diagnostics legitimately differ between payload modes.
  for (const field of ["elapsed_ms", "timing", "hard_deadline", "coordinator", "search_id"])
    delete search[field];
  return { analysis: result.analysis, search };
}

function sleep(ms) {
  return new Promise((done) => setTimeout(done, ms));
}

const report = {
  kind: "mcts-search-path-parity",
  schema: 1,
  generated: new Date().toISOString(),
  browser: null,
  provider,
  fixed_time: fixedTime,
  simulations,
  models: [],
  cases: [],
  failures: [],
};
const failures = report.failures;
const fail = (message) => failures.push(message);

function addImmediateBrandubh(positions) {
  // Same immediate-win fixture used by the semantic comparator tests.
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

let browser = null;
const servers = [];
try {
  browser = await chromium.launch({ executablePath: process.env.SPIKE_CHROMIUM || undefined });
  report.browser = browser.version();
  for (const argument of roots) {
    const root = resolve(argument);
    const assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));
    const server = createServer(async (request, response) => {
      try {
        let path = resolve(root, "." + decodeURIComponent(new URL(request.url, "http://localhost").pathname));
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
    const modelReport = {
      ruleset: model.ruleset,
      size: model.size,
      onnx_sha256: model.sha256,
      provider_requested: load.provider_requested,
      execution_placement: load.execution_placement,
      gpu_execution_verified: load.gpu_execution_verified === true,
    };
    report.models.push(modelReport);
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
    if (!positions.some((entry) => entry.kind === "repetition"))
      fail(`${model.ruleset}: no repetition-sensitive position is staged`);
    if (!positions.some((entry) => entry.kind === "terminal"))
      fail(`${model.ruleset}: no terminal position is staged`);

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
        budget: fixedTime ? settings.time_limit : settings.iterations,
      };
      try {
        const full = await run(entry, false, settings);
        await sleep(5);
        const compact = await run(entry, true, settings);
        const fullCoordinator = full.search?.coordinator ?? {};
        const compactCoordinator = compact.search?.coordinator ?? {};
        caseReport.full_simulations = full.search?.completed_simulations;
        caseReport.compact_simulations = compact.search?.completed_simulations;
        caseReport.full_payload = fullCoordinator.inference_payload_mode;
        caseReport.compact_payload = compactCoordinator.inference_payload_mode;
        caseReport.state = entry.state;
        caseReport.full_move = full.analysis?.best_move ?? null;
        caseReport.compact_move = compact.analysis?.best_move ?? null;
        caseReport.full_reason = full.search?.completion_reason;
        caseReport.compact_reason = compact.search?.completion_reason;
        caseReport.full_cutoff = full.search?.deadline_cutoff;
        caseReport.compact_cutoff = compact.search?.deadline_cutoff;
        const immediate = full.search?.completion_reason === "immediate_win";
        if (!terminal && !immediate) {
          if (fullCoordinator.inference_payload_mode !== "full-logits")
            fail(`${entry.name}: full path did not report full-logits`);
          if (compactCoordinator.inference_payload_mode !== "legal-only")
            fail(`${entry.name}: compact path did not report legal-only`);
          if (compactCoordinator.response_policy_values !== 0)
            fail(`${entry.name}: compact path transferred policy logits`);
        }
        if (full.search?.model?.sha256 !== model.sha256 || compact.search?.model?.sha256 !== model.sha256)
          fail(`${entry.name}: model identity changed between paths`);
        if (full.search?.model?.ruleset !== entry.ruleset || compact.search?.model?.ruleset !== entry.ruleset)
          fail(`${entry.name}: model ruleset does not match the position`);
        if (full.search?.position_identity !== compact.search?.position_identity)
          fail(`${entry.name}: position identity changed between paths`);
        if (fixedTime) {
          for (const [label, result] of [["full", full], ["compact", compact]]) {
            const search = result.search ?? {};
            const completed = search.completed_simulations;
            if (!Number.isInteger(completed) || completed < 0)
              fail(`${entry.name}/${label}: invalid completed work`);
            const immediateWin = search.completion_reason === "immediate_win";
            if (!terminal && !immediateWin) {
              if (!search.deadline_cutoff && completed !== settings.iterations)
                fail(`${entry.name}/${label}: fixed-time run did not report a deadline cutoff`);
              if (search.completion_reason !== "deadline" && completed !== settings.iterations)
                fail(`${entry.name}/${label}: fixed-time completion reason is inconsistent`);
            }
            const move = result.analysis?.best_move;
            if (!terminal) {
              if (!move || !Number.isInteger(move.source) || !Number.isInteger(move.target))
                fail(`${entry.name}/${label}: fixed-time run has no legal best move`);
            } else if (move !== null && move !== undefined) {
              fail(`${entry.name}/${label}: terminal position reported a move`);
            }
          }
          caseReport.ok = true;
        } else {
          if (!terminal && !immediate) {
            if (full.search?.completed_simulations !== settings.iterations)
              fail(`${entry.name}: full path did not complete fixed work`);
            if (compact.search?.completed_simulations !== settings.iterations)
              fail(`${entry.name}: compact path did not complete fixed work`);
          }
          for (const field of [
            "neural_requests",
            "prediction_reuses",
            "completion_reason",
            "deadline_cutoff",
            "position_identity",
          ]) {
            if (canonical(full.search?.[field]) !== canonical(compact.search?.[field]))
              fail(`${entry.name}: ${field} differs between paths`);
          }
          if (canonical(full.search?.root_visits) !== canonical(compact.search?.root_visits))
            fail(`${entry.name}: root visit distribution differs between paths`);
          if (canonical(full.analysis?.best_move) !== canonical(compact.analysis?.best_move))
            fail(`${entry.name}: best move differs between paths`);
          const delta = Math.abs(Number(full.analysis?.value) - Number(compact.analysis?.value));
          if (!Number.isFinite(delta) || delta > 1e-12)
            fail(`${entry.name}: defender-relative value differs (${delta})`);
          if (canonical(searchProjection(full)) !== canonical(searchProjection(compact)))
            fail(`${entry.name}: search result payload differs between paths`);
          caseReport.ok = true;
        }
      } catch (error) {
        caseReport.ok = false;
        caseReport.error = String(error?.message ?? error);
        fail(`${entry.name}: ${caseReport.error}`);
      }
      report.cases.push(caseReport);
    }
    if (pageErrors.length) fail(`${model.ruleset}: page errors: ${pageErrors.join("; ")}`);
    await page.close();
  }
} catch (error) {
  fail(`harness error: ${error?.stack ?? error?.message ?? String(error)}`);
} finally {
  await browser?.close().catch(() => {});
  for (const server of servers) await new Promise((done) => server.close(done));
}
report.ok = failures.length === 0;
if (output) {
  await mkdir(dirname(resolve(output)), { recursive: true });
  await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify({ ok: report.ok, cases: report.cases.length, failures }, null, 2));
process.exit(report.ok ? 0 : 1);
