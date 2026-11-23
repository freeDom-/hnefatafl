// Actual-browser UI tests for the experimental position-analysis interface.
//
// Covers Jarl without the neural runtime, browser neural MCTS from a clean state,
// budget/elapsed reporting, cancellation ownership, provider/model limits,
// repeated searches without model reload and page lifecycle teardown.
//
//   node spikes/browser_offline/test_mcts_ui.mjs /tmp/neural-spike --output /tmp/mcts-ui.json
import assert from "node:assert/strict";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const argv = process.argv.slice(2);
const root = resolve(argv.find((arg) => !arg.startsWith("--")) || "work/phase0/neural");
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const output = option("output", null);
const mime = {
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".html": "text/html",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
};
const report = {
  kind: "browser-mcts-ui",
  generated: new Date().toISOString(),
  checks: [],
  failures: [],
};
let server = null;
let browser = null;
let context = null;

try {
  const assets = JSON.parse(await readFile(resolve(root, "neural-assets.json"), "utf8"));
  report.model = {
    sha256: assets.model.sha256,
    ruleset: assets.model.ruleset,
    size: assets.model.size,
  };
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      // The dedicated test worker is served from the source tree only by this
      // harness; prepare_neural.py never stages it for the normal page.
      const testWorker = pathname === "/neural-worker-test.js";
      let path = testWorker
        ? resolve(import.meta.dirname, "neural-worker-test.js")
        : resolve(root, "." + pathname);
      if (!testWorker && path !== root && !path.startsWith(root + sep))
        throw new Error("outside root");
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
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const external = [];
  const requested = [];
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    requested.push(url.pathname);
    if (url.origin !== origin) {
      external.push(url.href);
      return route.abort();
    }
    return route.continue();
  });

  // Explicit test-only runtime configuration. It is never a URL/query option and
  // is not part of the analysis UI; the normal page leaves test hooks disabled.
  await context.addInitScript(() => {
    window.__NEURAL_TEST_HOOKS__ = true;
    window.__NEURAL_TEST_WORKER_URL__ = "/neural-worker-test.js";
  });

  async function freshPage(setup = null, { waitPositions = true } = {}) {
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    const startIndex = requested.length;
    if (setup) await setup(page);
    await page.goto(origin + "/neural.html");
    await page.waitForFunction(() => window.neural && window.neural.ready, null, {
      timeout: 90000,
    });
    if (waitPositions) {
      // The interface initializes the small shared position dataset itself; the
      // tests use the public UI, never an internal initialization API.
      await page.waitForFunction(
        () => document.querySelectorAll("#analysis-position option").length > 0,
        null,
        { timeout: 60000 },
      );
    }
    return { page, errors, startIndex, requested };
  }
  async function analysisState(page) {
    return page.textContent("#analysis-state");
  }
  async function waitState(page, expected, timeout = 60000) {
    const targets = Array.isArray(expected) ? expected : [expected];
    await page.waitForFunction(
      (values) => values.includes(document.querySelector("#state")?.textContent),
      targets,
      { timeout },
    );
  }
  async function waitAnalysisState(page, expected, timeout = 120000) {
    const targets = Array.isArray(expected) ? expected : [expected];
    await page.waitForFunction(
      (values) => values.includes(document.querySelector("#analysis-state")?.textContent),
      targets,
      { timeout },
    );
  }
  const delay = (ms) => new Promise((done) => setTimeout(done, ms));

  async function record(name, fn) {
    const entry = { name };
    try {
      await fn();
      entry.ok = true;
      console.log(`ok   ${name}`);
    } catch (error) {
      entry.ok = false;
      entry.error = String(error?.message ?? error);
      report.failures.push(`${name}: ${entry.error}`);
      console.log(`FAIL ${name}: ${entry.error}`);
    }
    report.checks.push(entry);
  }

  await record("Jarl analysis works without the neural runtime", async () => {
    const { page, errors, startIndex, requested } = await freshPage();
    await page.evaluate(() => {
      const analysis = window.neural.analysis;
      analysis.setRuleset("brandubh");
      analysis.setAgent("jarl");
      analysis.setBudget({ time: 1, seed: 7 });
    });
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed");
    const state = await page.evaluate(() => ({
      result: window.neural.analysis.result(),
      neuralWorker: window.neural.client.worker !== null,
      model: window.neural.client.model !== null,
      searchWorker: window.neural.coordinator.info !== null,
    }));
    assert.equal(state.result.agent, "jarl");
    assert.ok(state.result.analysis.best_move);
    assert.equal(state.result.analysis.source, "heuristic");
    assert.equal(state.neuralWorker, false, "ORT worker must not be created for Jarl");
    assert.equal(state.model, false, "no model may be loaded for Jarl");
    assert.equal(state.searchWorker, false, "search worker must not be created for Jarl");
    const newRequests = requested.slice(startIndex);
    assert.equal(
      newRequests.some((path) => path.includes("model/model.onnx") || path.includes("/ort/")),
      false,
      `unexpected neural asset requests: ${newRequests.join(", ")}`,
    );
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Jarl works when neural-only assets are unavailable", async () => {
    const { page, errors } = await freshPage(async (page) => {
      await page.route("**/neural-assets.json", (route) => route.abort());
      await page.route("**/model/reference.json", (route) => route.abort());
      await page.route("**/model/model.onnx", (route) => route.abort());
      await page.route("**/ort/**", (route) => route.abort());
    });
    await page.evaluate(() => {
      const analysis = window.neural.analysis;
      analysis.setRuleset("tablut");
      analysis.setAgent("jarl");
      analysis.setBudget({ time: 1, seed: 7 });
    });
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed");
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.agent, "jarl");
    assert.equal(result.position, "tablut-opening");
    assert.ok(result.analysis.best_move);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Asset loading is lazy and only neural MCTS needs the manifest", async () => {
    const { page, startIndex, requested } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const requests = requested.slice(startIndex);
    assert.ok(
      requests.some((path) => path.includes("neural-assets.json")),
      "neural MCTS loads the model manifest",
    );
    assert.equal(
      requests.some((path) => path.includes("reference.json")),
      false,
      "neural MCTS must not download numerical reference vectors",
    );
    await page.close();
  });

  await record("Neural MCTS analysis completes from a clean state", async () => {
    const { page, errors } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setProvider("wasm");
      analysis.setBudget({ simulations: 128, time: 5, seed: 7, exploration: 1.4 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed");
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.agent, "neural-mcts");
    assert.equal(result.analysis.source, "neural_search");
    assert.equal(result.search.completed_simulations, 128);
    assert.equal(result.search.completion_reason, "simulations");
    assert.equal(result.search.model.sha256, report.model.sha256);
    assert.equal(result.search.provider.requested, "wasm");
    assert.equal(result.search.coordinator.inference_payload_mode, "legal-only");
    assert.equal(result.search.coordinator.response_policy_values, 0);
    assert.ok(result.search.root_visits.length > 0);
    assert.ok(result.analysis.best_move);
    assert.ok(result.analysis.value >= -1 && result.analysis.value <= 1);
    assert.equal(result.authoritative, false);
    const displayed = await page.evaluate(() => ({
      completed: document.querySelector("#analysis-simulations-completed").textContent,
      rate: document.querySelector("#analysis-rate").textContent,
      duration: document.querySelector("#analysis-duration").textContent,
      verification: document.querySelector("#analysis-provider-info").textContent,
      payload: document.querySelector("#analysis-payload").textContent,
      completion: document.querySelector("#analysis-completion").textContent,
    }));
    assert.equal(displayed.completed, "128");
    assert.match(displayed.rate, /\/s$/);
    assert.match(displayed.duration, /ms$/);
    assert.match(displayed.verification, /wasm/);
    assert.match(displayed.payload, /legal-only \(0 logits\)/);
    assert.equal(displayed.completion, "simulations");
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Budget and deadline reporting is meaningful", async () => {
    const { page } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 4096, time: 0.5, seed: 3 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completion_reason, "deadline");
    assert.ok(result.search.completed_simulations > 0);
    assert.ok(
      result.search.completed_simulations < 4096,
      "a deadline cutoff must not claim the full budget",
    );
    assert.equal(result.search.deadline_cutoff, true);
    const displayed = await page.textContent("#analysis-simulations-completed");
    assert.equal(displayed, String(result.search.completed_simulations));
    assert.equal(await page.textContent("#analysis-completion"), "deadline");
    await page.close();
  });

  await record("Controls disable during an active analysis", async () => {
    const { page } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 4096, time: 10 });
    }, report.model.ruleset);
    const started = page.evaluate(() => window.neural.analysis.start());
    await page.waitForFunction(
      () => document.querySelector("#analysis-start")?.disabled === true,
      null,
      { timeout: 5000 },
    );
    assert.equal(await page.isDisabled("#analysis-agent"), true);
    assert.equal(await page.isDisabled("#analysis-simulations"), true);
    assert.equal(await page.isDisabled("#analysis-cancel"), false);
    await started;
    await waitAnalysisState(page, "completed", 180000);
    assert.equal(await page.isDisabled("#analysis-agent"), false);
    await page.close();
  });

  await record("Cancel prevents obsolete publication", async () => {
    const { page } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 4096, time: 10 });
    }, report.model.ruleset);
    const started = page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "running", 180000);
    await page.click("#analysis-cancel");
    assert.equal(await analysisState(page), "cancelled");
    await started;
    await delay(1200);
    assert.equal(await analysisState(page), "cancelled", "late completion must not publish");
    const state = await page.evaluate(() => ({
      result: window.neural.analysis.result(),
      output: document.querySelector("#analysis-output").textContent,
      active: window.neural.coordinator.active !== null,
    }));
    assert.equal(state.result, null);
    assert.equal(state.output, "");
    assert.equal(state.active, false);
    await page.close();
  });

  await record("An unsupported ruleset fails explicitly and recovers", async () => {
    const { page, errors } = await freshPage();
    const other = report.model.ruleset === "brandubh" ? "tablut" : "brandubh";
    await page.evaluate(([ruleset, otherRuleset]) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(otherRuleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 2 });
      analysis.setPosition(`${otherRuleset}-opening`);
    }, [report.model.ruleset, other]);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "failed", 120000);
    assert.match(await page.textContent("#analysis-status"), /staged model covers/i);
    // Recover on the supported ruleset without reloading the page.
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setBudget({ simulations: 128, time: 2 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completed_simulations, 128);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Repeated searches reuse the model session and workers", async () => {
    const { page } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 2 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const first = await page.evaluate(() => ({
      generation: window.neural.coordinator.generation,
      sha: window.neural.client.model?.model_sha256 ?? null,
    }));
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const second = await page.evaluate(() => ({
      generation: window.neural.coordinator.generation,
      sha: window.neural.client.model?.model_sha256 ?? null,
    }));
    assert.equal(second.generation, first.generation, "search worker must be reused");
    assert.equal(second.sha, first.sha, "model session must be reused");
    await page.close();
  });

  await record("pagehide during analysis cancels without publication", async () => {
    const { page } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 4096, time: 10 });
    }, report.model.ruleset);
    const started = page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "running", 180000);
    await page.evaluate(() =>
      window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: false })),
    );
    assert.equal(await analysisState(page), "cancelled");
    await started;
    await delay(800);
    const state = await page.evaluate(() => ({
      result: window.neural.analysis.result(),
      output: document.querySelector("#analysis-output").textContent,
    }));
    assert.equal(state.result, null);
    assert.equal(state.output, "");
    await page.close();
  });

  await record("Exported result is machine readable and explicitly experimental", async () => {
    const { page } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 2 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const text = await page.textContent("#analysis-output");
    const payload = JSON.parse(text);
    assert.equal(payload.authoritative, false);
    assert.equal(payload.agent, "neural-mcts");
    assert.equal(payload.search.completed_simulations, 128);
    assert.ok(payload.search.position_identity);
    await page.close();
  });

  await record("Cancellation during model download allows an immediate retry", async () => {
    let delayed = true;
    const { page } = await freshPage(async (page) => {
      await page.route("**/model/model.onnx", async (route) => {
        if (delayed) {
          delayed = false;
          await delay(1200);
        }
        await route.continue();
      });
    });
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    const started = page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "loading", 120000);
    await page.click("#analysis-cancel");
    assert.equal(await analysisState(page), "cancelled");
    // Restart immediately: admission waits for the bounded cleanup instead of
    // racing a still-terminating model load.
    const replacement = page.evaluate(() => window.neural.analysis.start());
    await Promise.all([started, replacement]);
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completed_simulations, 128);
    assert.equal(result.agent, "neural-mcts");
    await page.close();
  });

  await record("Cancellation during ORT session initialization recovers", async () => {
    let delayed = true;
    const { page, errors } = await freshPage(async (page) => {
      await page.route("**/ort/ort-wasm-simd-threaded.wasm", async (route) => {
        if (delayed) {
          delayed = false;
          await delay(1200);
        }
        await route.continue();
      });
    });
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    const started = page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "loading", 120000);
    await page.click("#analysis-cancel");
    await started;
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completed_simulations, 128);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Cancellation during cold search-worker initialization recovers", async () => {
    let delayed = true;
    const { page, errors } = await freshPage(async (page) => {
      await page.route("**/runtime/pyodide.asm.wasm", async (route) => {
        if (delayed) {
          delayed = false;
          await delay(1500);
        }
        await route.continue();
      });
    });
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    const started = page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "loading", 120000);
    await page.click("#analysis-cancel");
    assert.equal(await analysisState(page), "cancelled");
    await started;
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completed_simulations, 128);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Back-forward restoration installs a usable runtime", async () => {
    const { page } = await freshPage();
    await page.evaluate(() =>
      window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })),
    );
    await page.evaluate(() =>
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })),
    );
    // Jarl works after restoration.
    await page.evaluate(() => {
      const analysis = window.neural.analysis;
      analysis.setRuleset("brandubh");
      analysis.setAgent("jarl");
      analysis.setBudget({ time: 1 });
    });
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 120000);
    assert.ok((await page.evaluate(() => window.neural.analysis.result())).analysis.best_move);
    // A full neural MCTS search works after restoration, not only a probe.
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.agent, "neural-mcts");
    assert.equal(result.search.completed_simulations, 128);
    assert.ok(result.search.search_id);
    await page.close();
  });

  await record("Navigation away and back runs a new neural MCTS search", async () => {
    const { page } = await freshPage();
    await page.goto("about:blank");
    await page.goBack();
    await page.waitForFunction(() => window.neural && window.neural.ready, null, {
      timeout: 60000,
    });
    await page.evaluate(() => window.neural.analysis.loadPositions());
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completed_simulations, 128);
    await page.close();
  });

  await record("Non-default positions are preserved and analyzed exactly", async () => {
    const { page } = await freshPage();
    const positions = await page.evaluate(() => window.neural.analysis.positions());
    const chosen = positions.filter(
      (entry) => entry.ruleset === "brandubh" && entry.kind !== "opening",
    );
    assert.ok(chosen.length >= 2, "expected midgame/endgame/repetition fixtures");
    for (const entry of chosen.slice(0, 3)) {
      await page.evaluate(
        ({ ruleset, name }) => {
          const analysis = window.neural.analysis;
          analysis.setRuleset(ruleset);
          analysis.setPosition(name);
          analysis.setAgent("jarl");
          analysis.setBudget({ time: 1 });
        },
        { ruleset: entry.ruleset, name: entry.name },
      );
      // Reloading an already available position list must not reset selection.
      await page.evaluate(() => window.neural.analysis.loadPositions());
      assert.equal(
        await page.evaluate(() => window.neural.analysis.ui.position.value),
        entry.name,
      );
      await page.evaluate(() => window.neural.analysis.start());
      await waitAnalysisState(page, "completed", 120000);
      const result = await page.evaluate(() => window.neural.analysis.result());
      assert.equal(result.position, entry.name);
      assert.equal(result.ruleset, entry.ruleset);
      assert.ok(result.analysis.best_move, "the selected position produced a move");
    }
    await page.close();
  });

  await record("A selector change cannot redirect an in-flight analysis", async () => {
    const { page } = await freshPage();
    await page.evaluate(() => {
      const analysis = window.neural.analysis;
      analysis.setRuleset("brandubh");
      analysis.setPosition("brandubh-opening");
      analysis.setAgent("jarl");
      analysis.setBudget({ time: 1 });
    });
    const started = page.evaluate(() => window.neural.analysis.start());
    // Change the visible selection while the operation is admitted/running.
    await page.evaluate(() => window.neural.analysis.setPosition("brandubh-endgame"));
    await started;
    await waitAnalysisState(page, "completed", 120000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.position, "brandubh-opening");
    await page.close();
  });

  await record("Immediate restart after cancelling a blocked leaf inference", async () => {
    const { page, errors } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 256, time: 5 });
    }, report.model.ruleset);
    // Deterministic control point: the first leaf inference never settles.
    await page.evaluate(() => {
      const client = window.neural.client;
      const real = client.inferCompact.bind(client);
      let first = true;
      window.__inferBlocked = false;
      client.inferCompact = (request) => {
        if (!first) return real(request);
        first = false;
        window.__inferBlocked = true;
        return new Promise(() => {});
      };
    });
    const started = page.evaluate(() => window.neural.analysis.start());
    await page.waitForFunction(() => window.__inferBlocked === true, null, { timeout: 120000 });
    const before = await page.evaluate(() => ({
      generation: window.neural.coordinator.generation,
      model: window.neural.client.model?.model_sha256 ?? null,
    }));
    await page.click("#analysis-cancel");
    assert.equal(await analysisState(page), "cancelled");
    const replacement = page.evaluate(() => window.neural.analysis.start());
    await Promise.all([started, replacement]);
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completed_simulations, 256);
    const after = await page.evaluate(() => ({
      generation: window.neural.coordinator.generation,
      model: window.neural.client.model?.model_sha256 ?? null,
    }));
    assert.equal(after.generation, before.generation, "healthy workers are reused");
    assert.equal(after.model, before.model, "the model session is reused after cancellation");
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Repeated immediate cancellation and restart stays bounded", async () => {
    const { page, errors } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    for (let index = 0; index < 3; index += 1) {
      await page.evaluate(() => {
        const analysis = window.neural.analysis;
        analysis.setBudget({ simulations: 1024, time: 10 });
      });
      const started = page.evaluate(() => window.neural.analysis.start());
      await waitAnalysisState(page, "running", 120000);
      await page.click("#analysis-cancel");
      await page.evaluate(() => window.neural.analysis.setBudget({ simulations: 128, time: 5 }));
      const replacement = page.evaluate(() => window.neural.analysis.start());
      await Promise.all([started, replacement]);
      await waitAnalysisState(page, "completed", 180000);
      const result = await page.evaluate(() => window.neural.analysis.result());
      assert.equal(result.search.completed_simulations, 128, `cycle ${index}`);
      assert.equal(await page.isDisabled("#analysis-cancel"), true);
    }
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Unhealthy drain recreates the worker and session", async () => {
    const { page, errors } = await freshPage();
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    const before = await page.evaluate(() => {
      window.neural.client.worker.__drainProbe = "before";
      return window.neural.coordinator.generation;
    });
    // Deterministic control point: cancellation reports an unhealthy worker.
    await page.evaluate(() => {
      const client = window.neural.client;
      const real = client.cancelRequest.bind(client);
      window.__restoreCancelRequest = () => {
        client.cancelRequest = real;
      };
      client.cancelRequest = async () => {
        client.terminate("simulated unhealthy drain");
        return {
          state: "terminated",
          sessionRetained: false,
          workerRetained: false,
          outcome: "failed",
          errorCode: "SIMULATED_UNHEALTHY",
        };
      };
      const analysis = window.neural.analysis;
      analysis.setBudget({ simulations: 1024, time: 10 });
    });
    const started = page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "running", 120000);
    await page.click("#analysis-cancel");
    await page.evaluate(() => window.__restoreCancelRequest());
    await page.evaluate(() => window.neural.analysis.setBudget({ simulations: 128, time: 5 }));
    const replacement = page.evaluate(() => window.neural.analysis.start());
    await Promise.all([started, replacement]);
    await waitAnalysisState(page, "completed", 180000);
    const state = await page.evaluate(() => ({
      generation: window.neural.coordinator.generation,
      model: window.neural.client.model?.model_sha256 ?? null,
      probe: window.neural.client.worker?.__drainProbe ?? null,
      result: window.neural.analysis.result(),
    }));
    assert.equal(
      state.generation,
      before,
      "the healthy search worker is reused",
    );
    assert.equal(state.probe, null, "an unhealthy drain replaces the neural worker");
    assert.equal(state.model, report.model.sha256, "the replacement loads a fresh session");
    assert.equal(state.result.search.completed_simulations, 128);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Raw inference and analysis cannot compete for the neural client", async () => {
    let delayed = true;
    const { page, errors } = await freshPage(async (page) => {
      await page.route("**/model/model.onnx", async (route) => {
        if (delayed) {
          delayed = false;
          await delay(1200);
        }
        await route.continue();
      });
    });
    // A raw inference owns the neural client.
    await page.click("#infer");
    await waitState(page, "loading", 120000);
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    assert.equal(await page.isDisabled("#analysis-start"), true);
    await page.evaluate(() => window.neural.analysis.start());
    assert.equal(await analysisState(page), "failed");
    assert.match(await page.textContent("#analysis-status"), /inference operation is running/i);
    await page.click("#cancel");
    await waitState(page, ["cancelled", "completed", "failed"], 120000);
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    // An analysis owns the neural client. Block its first leaf inference so the
    // competition check is deterministic rather than timing-dependent.
    await page.evaluate(() => {
      const client = window.neural.client;
      const real = client.inferCompact.bind(client);
      let first = true;
      window.__inferBlocked = false;
      client.inferCompact = (request) => {
        if (!first) return real(request);
        first = false;
        window.__inferBlocked = true;
        return new Promise(() => {});
      };
      window.neural.analysis.setBudget({ simulations: 1024, time: 10 });
    });
    const analysisStarted = page.evaluate(() => window.neural.analysis.start());
    await page.waitForFunction(() => window.__inferBlocked === true, null, { timeout: 120000 });
    // The shared ownership policy disables incompatible raw controls while an
    // analysis owns the neural client; a direct call still fails explicitly.
    assert.equal(await page.isDisabled("#infer"), true);
    assert.equal(await page.isDisabled("#analysis-cancel"), false);
    await page.click("#analysis-cancel");
    await analysisStarted;
    // After bounded cleanup both controls work again.
    await page.evaluate(() => window.neural.analysis.setBudget({ simulations: 128, time: 5 }));
    await page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "completed", 180000);
    await page.click("#infer");
    await waitState(page, "completed", 120000);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Cancel controls have clear enabled states", async () => {
    const { page } = await freshPage();
    assert.equal(await page.isDisabled("#analysis-cancel"), true);
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 1024, time: 10 });
    }, report.model.ruleset);
    const started = page.evaluate(() => window.neural.analysis.start());
    await waitAnalysisState(page, "running", 120000);
    assert.equal(await page.isDisabled("#analysis-cancel"), false);
    await page.click("#analysis-cancel");
    await started;
    await page.waitForFunction(
      () => document.querySelector("#analysis-cancel")?.disabled === true,
      null,
      { timeout: 10000 },
    );
    assert.equal(await page.isDisabled("#analysis-start"), false);
    await page.close();
  });

  await record("Fresh page Jarl analysis through normal UI controls", async () => {
    const { page, errors, startIndex, requested } = await freshPage();
    await page.selectOption("#analysis-ruleset", "brandubh");
    await page.selectOption("#analysis-agent", "jarl");
    await page.selectOption("#analysis-time", "1");
    assert.equal(await page.isDisabled("#analysis-start"), false);
    await page.click("#analysis-start");
    await waitAnalysisState(page, "completed", 120000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.agent, "jarl");
    assert.equal(result.position, "brandubh-opening");
    assert.ok(result.analysis.best_move);
    const neuralRequests = requested
      .slice(startIndex)
      .filter((path) =>
        /neural-assets\.json|reference\.json|model\.onnx|\/ort\//.test(path),
      );
    assert.deepEqual(neuralRequests, [], "Jarl must not load neural assets");
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Fresh page neural MCTS through normal UI controls", async () => {
    const { page, errors } = await freshPage();
    await page.selectOption("#analysis-ruleset", report.model.ruleset);
    await page.selectOption("#analysis-agent", "neural");
    await page.selectOption("#analysis-provider", "wasm");
    await page.selectOption("#analysis-simulations", "128");
    await page.selectOption("#analysis-time", "5");
    await page.click("#analysis-start");
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.agent, "neural-mcts");
    assert.equal(result.search.completed_simulations, 128);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Another ruleset and non-default position via UI selectors", async () => {
    const { page, errors } = await freshPage();
    await page.selectOption("#analysis-ruleset", "tablut");
    await page.waitForFunction(
      () =>
        Array.from(document.querySelectorAll("#analysis-position option")).some(
          (option) => option.value === "tablut-endgame",
        ),
      null,
      { timeout: 30000 },
    );
    await page.selectOption("#analysis-position", "tablut-endgame");
    await page.selectOption("#analysis-agent", "jarl");
    await page.selectOption("#analysis-time", "1");
    await page.click("#analysis-start");
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.position, "tablut-endgame");
    assert.equal(result.ruleset, "tablut");
    assert.ok(result.analysis.best_move);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Initial position-data failure is recoverable through the UI", async () => {
    let failed = false;
    const { page, errors } = await freshPage(
      async (page) => {
        await page.route("**/mcts-positions.json", async (route) => {
          if (!failed) {
            failed = true;
            return route.fulfill({ status: 503, body: "unavailable" });
          }
          return route.continue();
        });
      },
      { waitPositions: false },
    );
    await waitAnalysisState(page, "failed", 60000);
    assert.match(await page.textContent("#analysis-status"), /positions unavailable/i);
    assert.equal(await page.isDisabled("#analysis-start"), true);
    await page.click("#analysis-reload");
    await page.waitForFunction(
      () => document.querySelectorAll("#analysis-position option").length > 0,
      null,
      { timeout: 60000 },
    );
    assert.equal(await page.isDisabled("#analysis-start"), false);
    await page.click("#analysis-start");
    await waitAnalysisState(page, "completed", 120000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.position, "brandubh-opening");
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Reloading positions preserves the selected position and history", async () => {
    const { page, errors } = await freshPage();
    await page.selectOption("#analysis-ruleset", "brandubh");
    await page.selectOption("#analysis-position", "brandubh-repetition");
    const before = await page.evaluate(() => ({
      value: document.querySelector("#analysis-position").value,
      history: window.neural.analysis
        .positions()
        .find((entry) => entry.name === "brandubh-repetition").state.history,
    }));
    assert.ok(before.history.length > 0, "the repetition fixture carries history");
    await page.click("#analysis-reload");
    await page.waitForFunction(
      () =>
        document.querySelector("#analysis-position")?.value === "brandubh-repetition",
      null,
      { timeout: 60000 },
    );
    await page.selectOption("#analysis-agent", "jarl");
    await page.selectOption("#analysis-time", "1");
    await page.click("#analysis-start");
    await waitAnalysisState(page, "completed", 120000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.position, "brandubh-repetition");
    assert.equal(result.ruleset, "brandubh");
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Pending real inference drains and a replacement reuses the session", async () => {
    const { page, errors } = await freshPage();
    await page.click("#load");
    await waitState(page, "completed", 180000);
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    await page.evaluate(() => {
      window.neural.client.setTestInferenceDelay(1000);
    });
    const started = page.evaluate(() => window.neural.analysis.start());
    await page.waitForFunction(
      () =>
        window.neural.client.testHooksConfigured === true &&
        window.neural.client.pending !== null,
      null,
      { timeout: 120000 },
    );
    const before = await page.evaluate(() => ({
      model: window.neural.client.model?.model_sha256 ?? null,
      generation: window.neural.coordinator.generation,
    }));
    await page.click("#analysis-cancel");
    await page.evaluate(() => {
      window.neural.client.setTestInferenceDelay(null);
    });
    const replacement = page.evaluate(() => window.neural.analysis.start());
    await Promise.all([started, replacement]);
    await waitAnalysisState(page, "completed", 180000);
    const result = await page.evaluate(() => window.neural.analysis.result());
    assert.equal(result.search.completed_simulations, 128);
    const after = await page.evaluate(() => ({
      model: window.neural.client.model?.model_sha256 ?? null,
      generation: window.neural.coordinator.generation,
    }));
    assert.equal(after.model, before.model, "a healthy drain retains the session");
    assert.equal(after.generation, before.generation, "a healthy drain retains the worker");
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("An unresponsive pending inference is replaced before the replacement runs", async () => {
    const { page, errors } = await freshPage();
    await page.click("#load");
    await waitState(page, "completed", 180000);
    await page.evaluate((ruleset) => {
      const analysis = window.neural.analysis;
      analysis.setRuleset(ruleset);
      analysis.setAgent("neural");
      analysis.setBudget({ simulations: 128, time: 5 });
    }, report.model.ruleset);
    await page.evaluate(() => {
      window.neural.client.setTestInferenceDelay(5000);
    });
    const started = page.evaluate(() => window.neural.analysis.start());
    await page.waitForFunction(
      () =>
        window.neural.client.testHooksConfigured === true &&
        window.neural.client.pending !== null,
      null,
      { timeout: 120000 },
    );
    const before = await page.evaluate(() => ({
      generation: window.neural.client.generation,
      model: window.neural.client.model?.model_sha256 ?? null,
    }));
    await page.click("#analysis-cancel");
    await page.evaluate(() => {
      window.neural.client.setTestInferenceDelay(null);
    });
    const replacement = page.evaluate(() => window.neural.analysis.start());
    await Promise.all([started, replacement]);
    await waitAnalysisState(page, "completed", 180000);
    const after = await page.evaluate(() => ({
      generation: window.neural.client.generation,
      model: window.neural.client.model?.model_sha256 ?? null,
      result: window.neural.analysis.result(),
    }));
    assert.ok(after.generation > before.generation, "an unhealthy drain replaces the worker");
    assert.equal(after.model, report.model.sha256, "the replacement loads a fresh session");
    assert.equal(after.result.search.completed_simulations, 128);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  report.external_requests = external;
  if (external.length) report.failures.push(`external requests: ${external.length}`);
} catch (error) {
  report.failures.push(`harness error: ${error?.message ?? String(error)}`);
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
  JSON.stringify({ ok: report.ok, checks: report.checks, failures: report.failures }, null, 2),
);
process.exit(report.ok ? 0 : 1);
