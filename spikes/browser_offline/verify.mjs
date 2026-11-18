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
async function exerciseStartupRecovery(context, origin) {
  const page = await context.newPage();
  try {
    await page.goto(origin);
    await page.waitForFunction(() => window.spike);
    // Cancel the initial game while its worker is still starting.
    await page.evaluate(() => window.spike.cancel());
    const cancelled_status = await page.textContent("#status");
    // Startup must still resolve even though the initial game was cancelled.
    await page.evaluate(() => window.spike.ready);
    const board_after_cancel = await page.evaluate(
      () => document.querySelector("#board").textContent,
    );
    // Rapid New Game requests: the newest wins and controls recover.
    const rapid = await page.evaluate(async () => {
      const settled = await Promise.allSettled([
        window.spike.newGame("brandubh"),
        window.spike.newGame("tablut"),
        window.spike.newGame("copenhagen"),
      ]);
      const last = settled[settled.length - 1];
      return {
        fulfilled: settled.filter((entry) => entry.status === "fulfilled")
          .length,
        ruleset:
          last.status === "fulfilled" && last.value
            ? last.value.state.ruleset
            : null,
        move_enabled: !document.querySelector("#move").disabled,
      };
    });
    // Offline infrastructure must be active and controlling the scope.
    await page.waitForFunction(() => navigator.serviceWorker.controller);
    // Offline reload must still work after recovery.
    await context.setOffline(true);
    await page.reload();
    await page.evaluate(() => window.spike.ready);
    const offline_board = await page.evaluate(
      () => document.querySelector("#board").textContent.length,
    );
    return {
      cancelled_status,
      board_after_cancel,
      rapid,
      offline_board,
      recovered: rapid.ruleset === "copenhagen" && offline_board > 0,
    };
  } finally {
    await context.setOffline(false).catch(() => {});
    await page.close();
  }
}
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
    const processSession = await browser.newBrowserCDPSession();
    const cpuBefore = await processSession.send("SystemInfo.getProcessInfo");
    const startupRecovery = shellOnly
      ? null
      : await exerciseStartupRecovery(context, origin);
    if (startupRecovery) {
      assert.equal(startupRecovery.cancelled_status, "Local work cancelled");
      assert.equal(startupRecovery.board_after_cancel, "");
      assert.equal(startupRecovery.rapid.fulfilled, 3);
      assert.equal(startupRecovery.rapid.ruleset, "copenhagen");
      assert.equal(startupRecovery.rapid.move_enabled, true);
      assert(startupRecovery.offline_board > 0);
      assert.equal(startupRecovery.recovered, true);
    }
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    if (profile.cpuRate)
      await cdp.send("Emulation.setCPUThrottlingRate", {
        rate: profile.cpuRate,
      });
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
    assert.equal(cancellation.hard_timeout, "TimeoutError");
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
    // UTF-8 byte sizing: multibyte payloads whose UTF-16 length is within
    // 512 KiB but whose serialized UTF-8 bytes exceed it must be rejected.
    const sizeValidation = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const encoder = new TextEncoder();
      const overhead = encoder.encode(
        JSON.stringify({ action: "info", pad: "" }),
      ).length;
      const ascii = (bytes) => ({
        action: "info",
        pad: "x".repeat(bytes - overhead),
      });
      const multibyte = (bytes) => ({
        action: "info",
        pad: "\u00e9".repeat(Math.floor((bytes - overhead) / 2)),
      });
      const unicodeOverLimit = multibyte(512 * 1024 + 2);
      const unicodeSerialized = JSON.stringify(unicodeOverLimit);
      const exactLimit = ascii(512 * 1024);
      const rejects = (client, payload) =>
        client.request(payload).then(
          () => false,
          (error) => error.message === "Input exceeds 512 KiB",
        );
      return {
        ascii_rejected: await rejects(
          new LocalAnalysisClient(),
          ascii(512 * 1024 + 1),
        ),
        unicode_rejected: await rejects(
          new LocalAnalysisClient(),
          unicodeOverLimit,
        ),
        unicode_utf16_within: unicodeSerialized.length <= 512 * 1024,
        unicode_utf8_over:
          encoder.encode(unicodeSerialized).length > 512 * 1024,
        exact_bytes: encoder.encode(JSON.stringify(exactLimit)).length,
        exact_accepted: await window.spike.raw(exactLimit).then(
          (value) => value.strategy === "baseline",
          () => false,
        ),
      };
    });
    assert.equal(sizeValidation.ascii_rejected, true);
    assert.equal(sizeValidation.unicode_rejected, true);
    assert.equal(sizeValidation.unicode_utf16_within, true);
    assert.equal(sizeValidation.unicode_utf8_over, true);
    assert.equal(sizeValidation.exact_bytes, 512 * 1024);
    assert.equal(sizeValidation.exact_accepted, true);
    // Synchronous postMessage failures must reset the transport and recover.
    const postFailure = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const original = window.Worker;
      let calls = 0;
      class FlakyPostWorker {
        constructor() {
          this.onmessage = null;
          this.onerror = null;
          this.messages = [];
          this.fail = ++calls === 1;
        }
        postMessage(message) {
          if (this.fail) throw new Error("post failed");
          this.messages.push(message);
        }
        terminate() {}
      }
      window.Worker = FlakyPostWorker;
      try {
        const client = new LocalAnalysisClient();
        const first = client
          .request({ action: "info" }, { timeoutMs: 1000 })
          .then(
            () => "resolved",
            (error) => error.message,
          );
        client.worker.onmessage({ data: { id: 0, value: {} } });
        await new Promise((resolve) => setTimeout(resolve, 0));
        const firstResult = await first;
        const failedTransport = {
          pending: client.pending,
          busy: client.busy,
          worker: client.worker,
        };
        // The failed transport must be released; a fresh worker serves the next request.
        const second = client
          .request({ action: "info" }, { timeoutMs: 1000 })
          .then(
            (value) => value,
            (error) => error.message,
          );
        const secondWorker = client.worker;
        secondWorker.onmessage({ data: { id: 0, value: {} } });
        await new Promise((resolve) => setTimeout(resolve, 0));
        secondWorker.onmessage({
          data: { id: secondWorker.messages[0].id, value: "recovered" },
        });
        const secondResult = await second;
        return {
          firstResult,
          firstPending: failedTransport.pending,
          firstBusy: failedTransport.busy,
          firstWorkerReset: failedTransport.worker === null,
          workerReplaced: secondWorker !== failedTransport.worker,
          secondResult,
          finalBusy: client.busy,
        };
      } finally {
        window.Worker = original;
      }
    });
    assert.equal(postFailure.firstResult, "post failed");
    assert.equal(postFailure.firstPending, null);
    assert.equal(postFailure.firstBusy, false);
    assert.equal(postFailure.firstWorkerReset, true);
    assert.equal(postFailure.workerReplaced, true);
    assert.equal(postFailure.secondResult, "recovered");
    assert.equal(postFailure.finalBusy, false);
    // A worker startup failure must not poison the client; the next request recovers.
    const startupFailure = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const original = window.Worker;
      let calls = 0;
      class FlakyStartupWorker {
        constructor() {
          this.onmessage = null;
          this.onerror = null;
          this.messages = [];
          this.calls = ++calls;
        }
        postMessage(message) {
          this.messages.push(message);
        }
        terminate() {}
      }
      window.Worker = FlakyStartupWorker;
      try {
        const client = new LocalAnalysisClient();
        const failed = client
          .request({ action: "info" }, { timeoutMs: 1000 })
          .then(
            () => "resolved",
            (error) => error.message,
          );
        client.worker.onmessage({ data: { id: 0, error: "runtime exploded" } });
        const failedResult = await failed;
        const busyAfterFailure = client.busy;
        const resetWorker = client.worker === null;
        const recovered = client
          .request({ action: "info" }, { timeoutMs: 1000 })
          .then(
            (value) => value,
            (error) => error.message,
          );
        const recoveredWorker = client.worker;
        recoveredWorker.onmessage({
          data: { id: 0, value: { strategy: "baseline" } },
        });
        await new Promise((resolve) => setTimeout(resolve, 0));
        recoveredWorker.onmessage({
          data: {
            id: recoveredWorker.messages[0].id,
            value: { strategy: "baseline" },
          },
        });
        return {
          failedResult,
          busyAfterFailure,
          resetWorker,
          freshWorker: recoveredWorker.calls === 2,
          recovered: await recovered,
        };
      } finally {
        window.Worker = original;
      }
    });
    assert.equal(startupFailure.failedResult, "runtime exploded");
    assert.equal(startupFailure.busyAfterFailure, false);
    assert.equal(startupFailure.resetWorker, true);
    assert.equal(startupFailure.freshWorker, true);
    assert.deepEqual(startupFailure.recovered, { strategy: "baseline" });
    // Offline infrastructure registration is idempotent and scoped to the spike.
    const offlineRegistration = await page.evaluate(async (expected) => {
      const first = window.spike.offlineReady();
      const second = window.spike.offlineReady();
      const scope = await first;
      return {
        same_promise: first === second,
        scope,
        scoped: scope === expected,
      };
    }, `${origin}/`);
    assert.equal(offlineRegistration.same_promise, true);
    assert.equal(offlineRegistration.scope, `${origin}/`);
    assert.equal(offlineRegistration.scoped, true);
    // Obsolete worker responses and post-cancellation replies must be ignored.
    const lateResponses = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const original = window.Worker;
      const posted = [];
      class FakeWorker {
        constructor() {
          this.onmessage = null;
          this.onerror = null;
        }
        postMessage(message) {
          posted.push(message);
        }
        terminate() {}
      }
      window.Worker = FakeWorker;
      try {
        const client = new LocalAnalysisClient();
        const first = client
          .request({ action: "info" }, { timeoutMs: 1000 })
          .then(
            (value) => ({ ok: true, value }),
            (error) => ({ ok: false, name: error.name }),
          );
        const firstWorker = client.worker;
        firstWorker.onmessage({ data: { id: 0, value: {} } });
        await new Promise((resolve) => setTimeout(resolve, 0));
        firstWorker.onmessage({ data: { id: 999, value: "stale" } });
        firstWorker.onmessage({ data: { id: posted[0].id, value: "fresh" } });
        const firstResult = await first;
        const second = new LocalAnalysisClient();
        const secondOutcome = second
          .request({ action: "info" }, { timeoutMs: 1000 })
          .then(
            () => "resolved",
            (error) => error.name,
          );
        const secondWorker = second.worker;
        secondWorker.onmessage({ data: { id: 0, value: {} } });
        await new Promise((resolve) => setTimeout(resolve, 0));
        second.cancel();
        secondWorker.onmessage({ data: { id: posted[1].id, value: "late" } });
        return { first: firstResult, second: await secondOutcome };
      } finally {
        window.Worker = original;
      }
    });
    assert.deepEqual(lateResponses.first, { ok: true, value: "fresh" });
    assert.equal(lateResponses.second, "AbortError");
    // An error from a replaced worker must not affect its replacement.
    const obsoleteWorkerError = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const original = window.Worker;
      class FakeWorker {
        constructor() {
          this.onmessage = null;
          this.onerror = null;
          this.messages = [];
          this.terminated = false;
        }
        postMessage(message) {
          this.messages.push(message);
        }
        terminate() {
          this.terminated = true;
        }
      }
      window.Worker = FakeWorker;
      const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
      try {
        const client = new LocalAnalysisClient();
        // Worker A starts a request, then is cancelled and replaced.
        const first = client.request({ action: "info" }, { timeoutMs: 1000 });
        const workerA = client.worker;
        workerA.onmessage({ data: { id: 0, value: {} } });
        await tick();
        client.cancel("replaced");
        const firstOutcome = await first.then(
          () => "resolved",
          (error) => error.name,
        );
        // Worker B starts another request and boots.
        const second = client.request({ action: "info" }, { timeoutMs: 1000 });
        const workerB = client.worker;
        workerB.onmessage({ data: { id: 0, value: {} } });
        await tick();
        // A late error from Worker A must be ignored.
        workerA.onerror({ message: "obsolete failure" });
        const workerBAlive = client.worker === workerB && !workerB.terminated;
        workerB.onmessage({
          data: { id: workerB.messages[0].id, value: "worker-b-ok" },
        });
        const secondResult = await second;
        // An actual error from the active worker still fails fast and resets.
        const third = client.request({ action: "info" }, { timeoutMs: 1000 });
        await tick();
        workerB.onerror({ message: "real failure" });
        const thirdOutcome = await third.then(
          () => "resolved",
          (error) => error.message,
        );
        return {
          first_outcome: firstOutcome,
          worker_b_alive: workerBAlive,
          second_result: secondResult,
          third_outcome: thirdOutcome,
          reset: client.worker === null,
          busy: client.busy,
        };
      } finally {
        window.Worker = original;
      }
    });
    assert.equal(obsoleteWorkerError.first_outcome, "AbortError");
    assert.equal(obsoleteWorkerError.worker_b_alive, true);
    assert.equal(obsoleteWorkerError.second_result, "worker-b-ok");
    assert.equal(obsoleteWorkerError.third_outcome, "real failure");
    assert.equal(obsoleteWorkerError.reset, true);
    assert.equal(obsoleteWorkerError.busy, false);
    // The same guard covers a worker replaced while it is still starting up.
    const obsoleteStartupError = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const original = window.Worker;
      class FakeWorker {
        constructor() {
          this.onmessage = null;
          this.onerror = null;
          this.messages = [];
          this.terminated = false;
        }
        postMessage(message) {
          this.messages.push(message);
        }
        terminate() {
          this.terminated = true;
        }
      }
      window.Worker = FakeWorker;
      const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
      try {
        const client = new LocalAnalysisClient();
        // Worker A starts loading but is replaced before it reports startup.
        const first = client.request({ action: "info" }, { timeoutMs: 1000 });
        const workerA = client.worker;
        client.cancel("replaced during startup");
        const firstOutcome = await first.then(
          () => "resolved",
          (error) => error.name,
        );
        // Worker B boots and completes.
        const second = client.request({ action: "info" }, { timeoutMs: 1000 });
        const workerB = client.worker;
        workerB.onmessage({ data: { id: 0, value: {} } });
        await tick();
        // A late startup error from Worker A must be ignored.
        workerA.onerror({ message: "obsolete startup failure" });
        const workerBAlive = client.worker === workerB && !workerB.terminated;
        workerB.onmessage({
          data: { id: workerB.messages[0].id, value: "worker-b-booted" },
        });
        const secondResult = await second;
        // An active startup error on a fresh worker still fails fast.
        const fresh = new LocalAnalysisClient();
        const third = fresh.request({ action: "info" }, { timeoutMs: 1000 });
        const workerC = fresh.worker;
        workerC.onerror({ message: "real startup failure" });
        const thirdOutcome = await third.then(
          () => "resolved",
          (error) => error.message,
        );
        return {
          first_outcome: firstOutcome,
          worker_b_alive: workerBAlive,
          second_result: secondResult,
          third_outcome: thirdOutcome,
          reset: fresh.worker === null,
          busy: fresh.busy,
        };
      } finally {
        window.Worker = original;
      }
    });
    assert.equal(obsoleteStartupError.first_outcome, "AbortError");
    assert.equal(obsoleteStartupError.worker_b_alive, true);
    assert.equal(obsoleteStartupError.second_result, "worker-b-booted");
    assert.equal(obsoleteStartupError.third_outcome, "real startup failure");
    assert.equal(obsoleteStartupError.reset, true);
    assert.equal(obsoleteStartupError.busy, false);
    // Lifecycle: displayed analysis is bound to the exact position and settings.
    const opening = vectors.find((v) => v.name === "brandubh-opening-0").before
      .state;
    const other = vectors.find((v) => v.name === "tablut-opening-0").before
      .state;
    const completedCleared = await page.evaluate(
      async ({ first, second }) => {
        await window.spike.restore(first);
        await window.spike.analyze({
          algorithm: "baseline",
          time_limit: 0.01,
        });
        const displayed =
          document.querySelector("#analysis").textContent.length;
        await window.spike.restore(second);
        return {
          displayed,
          afterChange: document.querySelector("#analysis").textContent,
        };
      },
      { first: opening, second: other },
    );
    assert(completedCleared.displayed > 0, "completed analysis must display");
    assert.equal(completedCleared.afterChange, "");
    // Changing position while analysis is still running cancels and clears it.
    const supersededDisplay = await page.evaluate(
      async ({ first, second }) => {
        await window.spike.restore(first);
        const pending = window.spike.analyze({
          algorithm: "alpha_beta",
          depth: 5,
          time_limit: 0.5,
        });
        await new Promise((resolve) => setTimeout(resolve, 40));
        const analyzing = document.querySelector("#analysis").textContent;
        await window.spike.restore(second);
        await pending.catch(() => {});
        return {
          analyzing,
          afterChange: document.querySelector("#analysis").textContent,
        };
      },
      { first: opening, second: other },
    );
    assert.equal(supersededDisplay.analyzing, "Analyzing…");
    assert.equal(supersededDisplay.afterChange, "");
    // A human move supersedes background analysis without a manual Cancel.
    const moveSupersedes = await page.evaluate(async (state) => {
      const snapshot = await window.spike.restore(state);
      const analysis = window.spike.analyze({
        algorithm: "alpha_beta",
        depth: 5,
        time_limit: 0.5,
      });
      await new Promise((resolve) => setTimeout(resolve, 40));
      const moved = await window.spike.move(snapshot.legal_moves[0]);
      await analysis.catch(() => {});
      return {
        ply: moved.state.ply,
        analysisText: document.querySelector("#analysis").textContent,
      };
    }, opening);
    assert.equal(moveSupersedes.ply, opening.ply + 1);
    assert.equal(moveSupersedes.analysisText, "");
    // Replacing the game supersedes background analysis.
    const newGameSupersedes = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      const analysis = window.spike.analyze({
        algorithm: "alpha_beta",
        depth: 5,
        time_limit: 0.5,
      });
      await new Promise((resolve) => setTimeout(resolve, 40));
      const game = await window.spike.newGame("tablut");
      await analysis.catch(() => {});
      return {
        ruleset: game.state.ruleset,
        ply: game.state.ply,
        analysisText: document.querySelector("#analysis").textContent,
      };
    }, opening);
    assert.equal(newGameSupersedes.ruleset, "tablut");
    assert.equal(newGameSupersedes.ply, 0);
    assert.equal(newGameSupersedes.analysisText, "");
    // Repeated cancellation stays safe and the worker restarts reliably.
    const repeatedCancel = await page.evaluate(async () => {
      for (let attempt = 0; attempt < 4; attempt++) window.spike.cancel();
      const info = await window.spike.info();
      return Boolean(info && info.strategy);
    });
    assert.equal(repeatedCancel, true);
    // Concurrent analyses: newest wins, obsolete result is discarded.
    const concurrent = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      const config = { algorithm: "alpha_beta", depth: 4, time_limit: 0.4 };
      const first = window.spike.analyze(config);
      const second = window.spike.analyze(config);
      const [a, b] = await Promise.all([first, second]);
      return {
        obsolete_discarded: a === undefined,
        latest_published: Boolean(b && b.analysis),
        displayed: document.querySelector("#analysis").textContent.length > 0,
      };
    }, opening);
    assert.equal(concurrent.obsolete_discarded, true);
    assert.equal(concurrent.latest_published, true);
    assert.equal(concurrent.displayed, true);
    // Cache-hit preemption: a warm entry cancels a slower in-flight search.
    const cachePreemption = await page.evaluate(
      async ({ state }) => {
        const { LocalAnalysisClient } = await import("./analysis-client.js");
        const client = new LocalAnalysisClient();
        const warmConfig = { algorithm: "baseline", time_limit: 0.01 };
        const slowConfig = {
          algorithm: "alpha_beta",
          depth: 5,
          time_limit: 0.5,
        };
        const warm = await client.analyze(state, warmConfig);
        const slow = client.analyze(state, slowConfig);
        await new Promise((resolve) => setTimeout(resolve, 40));
        const hit = await client.analyze(state, warmConfig);
        const slowOutcome = await slow.then(
          () => "resolved",
          (error) => error.name,
        );
        const busyAfterHit = client.busy;
        const info = await client.request({ action: "info" });
        return {
          warm_cached: warm.cached,
          hit_cached: hit.cached,
          hit_key_matches: hit.position_key === warm.position_key,
          slow_cancelled: slowOutcome === "AbortError",
          busy_after_hit: busyAfterHit,
          worker_valid: Boolean(info && info.strategy),
          cache_size: client.cache.size,
        };
      },
      { state: opening },
    );
    assert.equal(cachePreemption.warm_cached, false);
    assert.equal(cachePreemption.hit_cached, true);
    assert.equal(cachePreemption.hit_key_matches, true);
    assert.equal(cachePreemption.slow_cancelled, true);
    assert.equal(cachePreemption.busy_after_hit, false);
    assert.equal(cachePreemption.worker_valid, true);
    assert.equal(cachePreemption.cache_size, 1);
    // The cached result, not the superseded slow search, stays displayed.
    const cacheDisplay = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      const warmConfig = { algorithm: "baseline", time_limit: 0.01 };
      const warm = await window.spike.analyze(warmConfig);
      const slow = window.spike.analyze({
        algorithm: "alpha_beta",
        depth: 5,
        time_limit: 0.5,
      });
      await new Promise((resolve) => setTimeout(resolve, 40));
      const hit = await window.spike.analyze(warmConfig);
      await slow.catch(() => {});
      const displayed = document.querySelector("#analysis").textContent;
      return {
        hit_cached: hit.cached,
        hit_key_matches: hit.position_key === warm.position_key,
        displayed_matches_hit: displayed.includes(hit.position_key),
      };
    }, opening);
    assert.equal(cacheDisplay.hit_cached, true);
    assert.equal(cacheDisplay.hit_key_matches, true);
    assert.equal(cacheDisplay.displayed_matches_hit, true);
    // Manual Cancel clears the analyzing indicator immediately and is idempotent.
    const cancelFeedback = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      const analysis = window.spike.analyze({
        algorithm: "alpha_beta",
        depth: 5,
        time_limit: 0.5,
      });
      await new Promise((resolve) => setTimeout(resolve, 40));
      const before = document.querySelector("#analysis").textContent;
      window.spike.cancel();
      const after = document.querySelector("#analysis").textContent;
      const cancelStatus = document.querySelector("#status").textContent;
      await analysis.catch(() => {});
      window.spike.cancel();
      window.spike.cancel();
      const stillCancelled = document.querySelector("#status").textContent;
      const info = await window.spike.info();
      return {
        before,
        after,
        cancel_status: cancelStatus,
        still_cancelled: stillCancelled,
        recovered: Boolean(info && info.strategy),
      };
    }, opening);
    assert.equal(cancelFeedback.before, "Analyzing…");
    assert.equal(cancelFeedback.after, "");
    assert.equal(cancelFeedback.cancel_status, "Local work cancelled");
    assert.equal(cancelFeedback.still_cancelled, "Local work cancelled");
    assert.equal(cancelFeedback.recovered, true);
    // Cancellation, hard timeout and failure have distinct status feedback.
    const errorClassification = await page.evaluate(() => {
      const results = {};
      window.spike.handleError(new DOMException("x", "AbortError"));
      results.abort = document.querySelector("#status").textContent;
      window.spike.handleError(new DOMException("x", "TimeoutError"));
      results.timeout = document.querySelector("#status").textContent;
      window.spike.handleError(new Error("boom"));
      results.failure = document.querySelector("#status").textContent;
      return results;
    });
    assert.deepEqual(errorClassification, {
      abort: "Local work cancelled",
      timeout: "Local analysis timed out",
      failure: "Spike error: boom",
    });
    // Obsolete analysis errors must not overwrite a newer status message.
    const staleStatus = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      const analysis = window.spike.analyze({
        algorithm: "alpha_beta",
        depth: 5,
        time_limit: 0.5,
      });
      await new Promise((resolve) => setTimeout(resolve, 40));
      const game = await window.spike.newGame("tablut");
      await analysis.catch(() => {});
      return {
        ruleset: game.state.ruleset,
        status: document.querySelector("#status").textContent,
      };
    }, opening);
    assert.equal(staleStatus.ruleset, "tablut");
    assert.equal(staleStatus.status, "defenders to move");
    // Terminal positions disable Move and AI but keep analysis available.
    const terminalStates = {};
    for (const ruleset of ["brandubh", "tablut", "copenhagen"])
      terminalStates[ruleset] = vectors.find(
        (v) => v.name === `${ruleset}-king-escape`,
      ).after.state;
    const terminalControls = await page.evaluate(async (states) => {
      const results = {};
      for (const [ruleset, state] of Object.entries(states)) {
        const snapshot = await window.spike.restore(state);
        results[ruleset] = {
          winner: snapshot.state.winner,
          legal_moves: snapshot.legal_moves.length,
          move_disabled: document.querySelector("#move").disabled,
          ai_disabled: document.querySelector("#ai").disabled,
          analyze_disabled: document.querySelector("#analyze").disabled,
          status: document.querySelector("#status").textContent,
        };
      }
      // Invalid and empty moves are rejected rather than throwing.
      results.invalid_move = await window.spike.move({}).then(
        () => "resolved",
        (error) => error.message,
      );
      results.no_move = await window.spike.move(null).then(
        () => "resolved",
        (error) => error.message,
      );
      return results;
    }, terminalStates);
    for (const ruleset of ["brandubh", "tablut", "copenhagen"]) {
      const entry = terminalControls[ruleset];
      assert.equal(entry.winner, "defenders");
      assert.equal(entry.legal_moves, 0);
      assert.equal(entry.move_disabled, true);
      assert.equal(entry.ai_disabled, true);
      assert.equal(entry.analyze_disabled, false);
      assert(entry.status.startsWith("defenders:"));
    }
    assert.equal(terminalControls.invalid_move, "Invalid move");
    assert.equal(terminalControls.no_move, "Invalid move");
    // A playable position restores normal controls.
    const restoredControls = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      return {
        move_disabled: document.querySelector("#move").disabled,
        ai_disabled: document.querySelector("#ai").disabled,
      };
    }, opening);
    assert.equal(restoredControls.move_disabled, false);
    assert.equal(restoredControls.ai_disabled, false);
    // Foreground priority: background analysis never preempts a foreground job.
    const priorityScheduling = await page.evaluate(
      async ({ state, config }) => {
        const { LocalAnalysisClient } = await import("./analysis-client.js");
        const original = window.Worker;
        class DelayedWorker {
          constructor() {
            this.onmessage = null;
            this.onerror = null;
            this.messages = [];
            this.terminated = false;
          }
          postMessage(message) {
            this.messages.push(message);
          }
          terminate() {
            this.terminated = true;
          }
        }
        window.Worker = DelayedWorker;
        const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
        try {
          const client = new LocalAnalysisClient();
          // Warm one cache entry through a completed background analysis.
          const warm = client.analyze(state, config, { preempt: true });
          client.worker.onmessage({ data: { id: 0, value: {} } });
          await tick();
          const worker = client.worker;
          worker.onmessage({
            data: { id: worker.messages[0].id, value: { cached: false } },
          });
          const warmResult = await warm;
          // Start a foreground job and keep it pending.
          const beforeForeground = worker.messages.length;
          const foreground = client.request(
            { action: "new", ruleset: "tablut" },
            { preempt: true, foreground: true },
          );
          await tick();
          const foregroundId = worker.messages[beforeForeground].id;
          // A background cache miss must not preempt the foreground job.
          const blocked = await client
            .analyze({ ...state, ply: state.ply + 1 }, config, {
              preempt: true,
            })
            .then(
              () => "resolved",
              (error) => error.name,
            );
          const foregroundAlive = !worker.terminated;
          // A background cache hit must not terminate the foreground either.
          const hit = await client.analyze(state, config, { preempt: true });
          const foregroundAliveAfterHit = !worker.terminated;
          // The foreground job still completes normally.
          worker.onmessage({
            data: {
              id: foregroundId,
              value: { state: { ruleset: "tablut" }, legal_moves: [] },
            },
          });
          const foregroundResult = await foreground;
          return {
            warm_cached: warmResult.cached,
            blocked,
            foreground_alive: foregroundAlive,
            foreground_alive_after_hit: foregroundAliveAfterHit,
            hit_cached: hit.cached,
            foreground_ruleset: foregroundResult.state.ruleset,
          };
        } finally {
          window.Worker = original;
        }
      },
      { state: opening, config: { algorithm: "baseline", time_limit: 0.01 } },
    );
    assert.equal(priorityScheduling.warm_cached, false);
    assert.equal(priorityScheduling.blocked, "ForegroundPriorityError");
    assert.equal(priorityScheduling.foreground_alive, true);
    assert.equal(priorityScheduling.foreground_alive_after_hit, true);
    assert.equal(priorityScheduling.hit_cached, true);
    assert.equal(priorityScheduling.foreground_ruleset, "tablut");
    // App-level foreground priority with the real delayed worker action.
    const foregroundPriority = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      const foreground = window.spike.raw(
        { action: "probe" },
        { preempt: true, foreground: true },
      );
      await new Promise((resolve) => setTimeout(resolve, 30));
      const analysis = await window.spike
        .analyze({ algorithm: "alpha_beta", depth: 3, time_limit: 0.3 })
        .then(
          () => "resolved",
          (error) => error.name,
        );
      const probe = await foreground.then(
        (value) => value.worker_busy_ms > 0,
        (error) => error.name,
      );
      return {
        analysis,
        probe,
        status: document.querySelector("#status").textContent,
        analysis_text: document.querySelector("#analysis").textContent,
      };
    }, opening);
    assert.equal(foregroundPriority.analysis, "resolved");
    assert.equal(foregroundPriority.probe, true);
    assert.equal(foregroundPriority.analysis_text, "");
    assert.equal(foregroundPriority.status, "attackers to move");
    // Delayed fixture loading must not preempt or overwrite a newer game.
    const fixtureRace = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      const originalFetch = window.fetch;
      window.fetch = async (...args) => {
        await gate;
        return originalFetch(...args);
      };
      try {
        const fixtures = window.spike.fixtures();
        await new Promise((resolve) => setTimeout(resolve, 20));
        const game = await window.spike.newGame("tablut");
        release();
        const outcome = await fixtures.then(
          () => "resolved",
          (error) => error.name,
        );
        return {
          ruleset: game.state.ruleset,
          outcome,
          status: document.querySelector("#status").textContent,
        };
      } finally {
        window.fetch = originalFetch;
      }
    }, opening);
    assert.equal(fixtureRace.ruleset, "tablut");
    assert.equal(fixtureRace.outcome, "resolved");
    assert.equal(fixtureRace.status, "defenders to move");
    // Invalid analysis settings clear the pending indicator and report failure.
    const invalidAnalysis = await page.evaluate(async () => {
      const outcome = await window.spike.analyze({ algorithm: "neural" }).then(
        () => ({ ok: true }),
        (error) => {
          window.spike.handleError(error);
          return { ok: false, message: error.message };
        },
      );
      return {
        ok: outcome.ok,
        message: outcome.message,
        analysis: document.querySelector("#analysis").textContent,
        status: document.querySelector("#status").textContent,
      };
    });
    assert.equal(invalidAnalysis.ok, false);
    assert(invalidAnalysis.message.includes("classical"));
    assert.equal(invalidAnalysis.analysis, "");
    assert(invalidAnalysis.status.startsWith("Spike error:"));
    // Simulated worker transport failure clears the indicator and recovers.
    const workerFailureAnalysis = await page.evaluate(async (state) => {
      await window.spike.restore(state);
      const original = Worker.prototype.postMessage;
      Worker.prototype.postMessage = function () {
        throw new Error("simulated worker transport failure");
      };
      try {
        const outcome = await window.spike
          .analyze({ algorithm: "baseline", time_limit: 0.02, seed: 424242 })
          .then(
            () => "resolved",
            (error) => {
              window.spike.handleError(error);
              return error.message;
            },
          );
        const analysis = document.querySelector("#analysis").textContent;
        const status = document.querySelector("#status").textContent;
        Worker.prototype.postMessage = original;
        const recovered = await window.spike.info();
        return {
          outcome,
          analysis,
          status,
          recovered: Boolean(recovered && recovered.strategy),
        };
      } finally {
        Worker.prototype.postMessage = original;
      }
    }, opening);
    assert(workerFailureAnalysis.outcome.includes("simulated"));
    assert.equal(workerFailureAnalysis.analysis, "");
    assert(workerFailureAnalysis.status.startsWith("Spike error:"));
    assert.equal(workerFailureAnalysis.recovered, true);
    // Repeated idle cancellation must not terminate or replace the worker.
    const idleCancel = await page.evaluate(async () => {
      const { LocalAnalysisClient } = await import("./analysis-client.js");
      const original = window.Worker;
      let instances = 0;
      class CountingWorker {
        constructor() {
          this.onmessage = null;
          this.onerror = null;
          this.terminated = false;
          instances++;
        }
        postMessage() {}
        terminate() {
          this.terminated = true;
        }
      }
      window.Worker = CountingWorker;
      const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
      try {
        const client = new LocalAnalysisClient();
        const first = client.request({ action: "info" }, { timeoutMs: 1000 });
        client.worker.onmessage({ data: { id: 0, value: {} } });
        await tick();
        client.worker.onmessage({ data: { id: 1, value: "ready" } });
        await first;
        const afterInit = instances;
        for (let attempt = 0; attempt < 5; attempt++) client.cancel();
        const afterIdleCancel = instances;
        const workerPreserved = client.worker !== null;
        const second = client.request({ action: "info" }, { timeoutMs: 1000 });
        await tick();
        client.worker.onmessage({ data: { id: 2, value: "again" } });
        const secondResult = await second;
        // Active cancellation still terminates and recovers.
        const active = client.request({ action: "info" }, { timeoutMs: 1000 });
        await tick();
        const activeWorker = client.worker;
        const beforeActiveCancel = instances;
        client.cancel();
        const activeOutcome = await active.then(
          () => "resolved",
          (error) => error.name,
        );
        return {
          after_init: afterInit,
          after_idle_cancel: afterIdleCancel,
          worker_preserved: workerPreserved,
          after_reuse: instances,
          second_result: secondResult,
          active_terminated: activeWorker.terminated,
          active_outcome: activeOutcome,
          active_instances: beforeActiveCancel,
        };
      } finally {
        window.Worker = original;
      }
    });
    assert.equal(idleCancel.after_idle_cancel, idleCancel.after_init);
    assert.equal(idleCancel.worker_preserved, true);
    assert.equal(idleCancel.after_reuse, idleCancel.after_init);
    assert.equal(idleCancel.second_result, "again");
    assert.equal(idleCancel.active_terminated, true);
    assert.equal(idleCancel.active_outcome, "AbortError");
    assert.equal(idleCancel.active_instances, idleCancel.after_init);
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
      size_validation: sizeValidation,
      post_failure_cleanup: postFailure,
      startup_failure_recovery: startupFailure,
      late_responses: lateResponses,
      obsolete_worker_error: obsoleteWorkerError,
      obsolete_startup_error: obsoleteStartupError,
      cache_preemption: cachePreemption,
      cache_display: cacheDisplay,
      cancel_feedback: cancelFeedback,
      error_classification: errorClassification,
      stale_status: staleStatus,
      terminal_controls: terminalControls,
      restored_controls: restoredControls,
      offline_registration: offlineRegistration,
      startup_recovery: startupRecovery,
      priority_scheduling: priorityScheduling,
      foreground_priority: foregroundPriority,
      fixture_race: fixtureRace,
      invalid_analysis: invalidAnalysis,
      worker_failure_analysis: workerFailureAnalysis,
      idle_cancel: idleCancel,
      lifecycle: {
        completed_cleared: completedCleared.afterChange === "",
        superseded_display_cleared: supersededDisplay.afterChange === "",
        move_superseded: moveSupersedes.analysisText === "",
        new_game_superseded: newGameSupersedes.analysisText === "",
        repeated_cancel_restart: repeatedCancel,
        concurrent_latest_wins: concurrent.latest_published,
      },
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
