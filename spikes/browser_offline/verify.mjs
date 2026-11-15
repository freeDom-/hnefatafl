// Standalone real-browser gate; no production app, API or installed model is used.
import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { resolve, sep, extname } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
const shellOnly = process.argv.includes("--shell-only");
const root = resolve(
  process.argv.find((arg, i) => i > 1 && !arg.startsWith("--")) ||
    "work/phase0/offline",
);
const mime = {
  ".js": "text/javascript",
  ".html": "text/html",
  ".json": "application/json",
  ".wasm": "application/wasm",
};
const server = createServer(async (request, response) => {
  try {
    let path = resolve(
      root,
      "." +
        decodeURIComponent(new URL(request.url, "http://localhost").pathname),
    );
    if (path !== root && !path.startsWith(root + sep))
      throw new Error("outside root");
    if ((await stat(path)).isDirectory()) path = resolve(path, "index.html");
    response.setHeader(
      "Content-Type",
      mime[extname(path)] || "application/octet-stream",
    );
    response.end(await readFile(path));
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const origin = `http://127.0.0.1:${server.address().port}`;
const legal = (moves, move) =>
  moves.some(
    (candidate) =>
      candidate.source === move?.source && candidate.target === move?.target,
  );
let browser;
try {
  browser = await chromium.launch({
    executablePath: process.env.SPIKE_CHROMIUM || undefined,
  });
  const profiles = [];
  for (const profile of shellOnly
    ? [{ name: "shell-only" }]
    : [
        {
          name: "desktop",
          viewport: { width: 1920, height: 1080 },
          cpuRate: 1,
        },
        {
          name: "mobile-emulated-4x",
          viewport: { width: 390, height: 844 },
          cpuRate: 4,
          isMobile: true,
          hasTouch: true,
        },
      ]) {
    const context = await browser.newContext({
      viewport: profile.viewport,
      isMobile: profile.isMobile,
      hasTouch: profile.hasTouch,
    });
    const external = [];
    context.on("request", (request) => {
      if (!request.url().startsWith(origin + "/")) external.push(request.url());
      assert(
        !request.url().includes("/api/"),
        "Spike must not contact application APIs",
      );
    });
    await context.route("**/*", (route) =>
      route
        .request()
        .url()
        .startsWith(origin + "/")
        ? route.continue()
        : route.abort(),
    );
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    if (profile.cpuRate)
      await cdp.send("Emulation.setCPUThrottlingRate", {
        rate: profile.cpuRate,
      });
    const processSession = await browser.newBrowserCDPSession();
    const cpuBefore = await processSession.send("SystemInfo.getProcessInfo");
    await page.goto(origin);
    if (shellOnly) {
      await page.waitForFunction(() =>
        document
          .querySelector("#status")
          .textContent.startsWith("Spike error:"),
      );
      await page.click("#pulse");
      assert.equal(await page.textContent("#pulses"), "1");
      profiles.push({
        profile: profile.name,
        ui_responded: true,
        engine_execution_verified: false,
        error: await page.textContent("#status"),
        external_requests: external,
      });
      await context.close();
      continue;
    }
    const manifest = JSON.parse(await readFile(resolve(root, "assets.json")));
    assert(manifest.runtime_included, "Stage pinned runtime first");
    await page.evaluate(() => window.spike.ready);
    const startup_ms = await page.evaluate(
      () => window.spike.metrics.startup_ms,
    );
    const runtime = await page.evaluate(() => window.spike.info());
    const vectors = JSON.parse(await readFile(resolve(root, "fixtures.json")));
    const references = JSON.parse(
      await readFile(resolve(root, "search-fixtures.json")),
    );
    const results = [];
    async function exercise(offline) {
      assert.equal(
        (await page.evaluate(() => window.spike.fixtures())).passed,
        34,
      );
      for (const ruleset of ["brandubh", "tablut", "copenhagen"]) {
        const initial = await page.evaluate(
          (rule) => window.spike.newGame(rule),
          ruleset,
        );
        assert(initial.legal_moves.length > 0);
        const human = await page.evaluate(
          (move) => window.spike.move(move),
          initial.legal_moves[0],
        );
        assert.equal(human.state.ply, 1);
        const ai = await page.evaluate(() => window.spike.ai());
        assert(legal(human.legal_moves, ai.move));
        assert.equal(ai.state.ply, 2);
        if (offline) continue;
        for (const algorithm of ["baseline", "alpha_beta", "mcts"]) {
          const config = {
            algorithm,
            depth: 2,
            time_limit: 0.2,
            iterations: 32,
            rollout_depth: 4,
          };
          const analysis = await page.evaluate(
            ({ state, config }) => window.spike.analyzeState(state, config),
            { state: human.state, config },
          );
          assert.equal(analysis.authoritative, false);
          assert.equal(analysis.schema, 1);
          assert(legal(human.legal_moves, analysis.analysis.best_move));
          assert(analysis.analysis.value >= -1 && analysis.analysis.value <= 1);
          const cached = await page.evaluate(
            ({ state, config }) => window.spike.analyzeState(state, config),
            { state: human.state, config },
          );
          assert.equal(cached.cached, true);
          assert.equal(cached.position_key, analysis.position_key);
          results.push({
            ruleset,
            algorithm,
            elapsed_ms: analysis.elapsed_ms,
            analysis: analysis.analysis,
            heap_capacity_bytes: analysis.heap_capacity_bytes,
          });
        }
      }
    }
    await exercise(false);
    const tactical = [];
    for (const reference of references) {
      const local = await page.evaluate(
        ({ state, config }) => window.spike.analyzeState(state, config),
        reference,
      );
      assert.equal(
        local.analysis.completed_depth,
        1,
        "Tactical parity requires a completed depth, not a deadline fallback",
      );
      assert.deepEqual(local.analysis.best_move, reference.analysis.best_move);
      assert.equal(local.analysis.value, reference.analysis.value);
      const moved = await page.evaluate(
        ({ state, move }) => window.spike.raw({ action: "move", state, move }),
        { state: reference.state, move: local.analysis.best_move },
      );
      assert.equal(moved.state.winner, "defenders");
      tactical.push({
        name: reference.name,
        native_match: true,
        immediate_win: true,
        elapsed_ms: local.elapsed_ms,
      });
    }
    for (const terminal of vectors.filter(
      (v) => v.name.endsWith("-repetition") || v.name.endsWith("-king-escape"),
    )) {
      const report = await page.evaluate(
        (state) =>
          window.spike.analyzeState(state, {
            algorithm: "alpha_beta",
            depth: 2,
            time_limit: 0.2,
          }),
        terminal.after.state,
      );
      assert.equal(report.analysis.source, "terminal");
      assert.equal(report.analysis.best_move, null);
      assert.equal(
        report.analysis.value,
        terminal.after.state.winner === "draw"
          ? 0
          : terminal.after.state.winner === "defenders"
            ? 1
            : -1,
      );
    }
    const current = vectors.find((v) => v.name === "brandubh-opening-0").before
      .state;
    const historyDifferent = vectors.find(
      (v) => v.name === "brandubh-repetition",
    ).before.state;
    const historyKeys = await page.evaluate(
      async ({ current, historyDifferent }) => {
        const first = await window.spike.analyzeState(current, {});
        const second = await window.spike.analyzeState(historyDifferent, {});
        for (let seed = 0; seed < 10; seed++)
          await window.spike.analyzeState(current, { seed, time_limit: 0.01 });
        return {
          first: first.position_key,
          second: second.position_key,
          second_cached: second.cached,
          size: window.spike.cacheSize(),
        };
      },
      { current, historyDifferent },
    );
    assert.notEqual(historyKeys.first, historyKeys.second);
    assert.equal(historyKeys.second_cached, false);
    assert.equal(historyKeys.size, 8);
    // Real bounded Copenhagen alpha-beta, not a main-thread synthetic delay.
    let searchFinished = false;
    const search = page
      .evaluate(
        (state) =>
          window.spike.analyzeState(state, {
            algorithm: "alpha_beta",
            depth: 5,
            time_limit: 0.5,
          }),
        vectors.find((v) => v.name === "copenhagen-opening-0").before.state,
      )
      .then((value) => {
        searchFinished = true;
        return value;
      });
    const interactionStarted = performance.now();
    await page.click("#pulse", { force: true });
    assert(!searchFinished, "UI must respond while actual alpha-beta is busy");
    const ui_response_ms = performance.now() - interactionStarted;
    const busySearch = await search;
    // Cursor replacement terminates a genuinely busy worker; stale work cannot publish.
    const cancellation = await page.evaluate(async (state) => {
      const pending = window.spike.probe().then(
        () => "unexpected completion",
        (error) => error.name,
      );
      await new Promise((resolve) => setTimeout(resolve, 30));
      const start = performance.now();
      const replacement = window.spike.restore(state);
      const outcome = await pending;
      const cancelled_ms = performance.now() - start;
      await replacement;
      const timeout = await window.spike
        .raw({ action: "probe" }, { timeoutMs: 25 })
        .then(
          () => "unexpected completion",
          (error) => error.name,
        );
      const restartStarted = performance.now();
      await window.spike.info();
      return {
        outcome,
        cancelled_ms,
        hard_timeout: timeout,
        restart_ms: performance.now() - restartStarted,
      };
    }, current);
    assert.equal(cancellation.outcome, "AbortError");
    assert.equal(cancellation.hard_timeout, "AbortError");
    const unsupported = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const original = window.Worker;
      try {
        window.Worker = undefined;
        return await new LocalAnalysisClient().request({ action: "info" }).then(
          () => false,
          (error) => error.message.includes("unsupported"),
        );
      } finally {
        window.Worker = original;
      }
    });
    assert.equal(unsupported, true);
    const deadlineRejected = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      return new LocalAnalysisClient()
        .request({ action: "info" }, { timeoutMs: 6000 })
        .then(
          () => false,
          (error) => error.message.includes("deadline"),
        );
    });
    assert.equal(deadlineRejected, true);
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.click("#cancel");
    await page.evaluate(() => window.spike.info());
    assert.deepEqual(pageErrors, []);
    await context.setOffline(true);
    await page.reload();
    await page.evaluate(() => window.spike.ready);
    const offline_startup_ms = await page.evaluate(
      () => window.spike.metrics.startup_ms,
    );
    await exercise(true);
    const offlineAnalysis = await page.evaluate(
      (reference) =>
        window.spike.analyzeState(reference.state, reference.config),
      references[0],
    );
    assert.equal(offlineAnalysis.analysis.completed_depth, 1);
    assert.deepEqual(
      offlineAnalysis.analysis.best_move,
      references[0].analysis.best_move,
    );
    assert.equal(external.length, 0);
    const cpuAfter = await processSession.send("SystemInfo.getProcessInfo");
    const before = new Map(
      cpuBefore.processInfo.map((entry) => [entry.id, entry.cpuTime]),
    );
    const browser_process_cpu_seconds_lower_bound = cpuAfter.processInfo.reduce(
      (sum, entry) =>
        sum + Math.max(0, entry.cpuTime - (before.get(entry.id) || 0)),
      0,
    );
    profiles.push({
      profile: profile.name,
      viewport: profile.viewport,
      cpu_throttle_requested: profile.cpuRate,
      runtime,
      startup_ms,
      offline_startup_ms,
      fixture_comparisons: 68,
      tactical,
      results,
      ui_response_ms,
      busy_search_ms: busySearch.elapsed_ms,
      cancellation,
      unsupported_worker_feedback: unsupported,
      history_cache_isolated: true,
      cache_capacity: 8,
      browser_process_cpu_seconds_lower_bound,
      external_requests: external,
    });
    await processSession.detach();
    await context.close();
  }
  const manifest = JSON.parse(await readFile(resolve(root, "assets.json")));
  console.log(
    JSON.stringify(
      {
        browser: browser.version(),
        pyodide: manifest.pyodide,
        shell_only: shellOnly,
        profiles,
        inventory: manifest.inventory,
      },
      null,
      2,
    ),
  );
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
