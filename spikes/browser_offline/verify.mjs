// Run from the repository root; uses the existing frontend Playwright dependency.
import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { resolve, sep, extname } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const shellOnly = process.argv.includes("--shell-only");
const root = resolve(
  process.argv.find((s, i) => i > 1 && !s.startsWith("--")) ||
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
let browser;
try {
  browser = await chromium.launch({
    executablePath: process.env.SPIKE_CHROMIUM || undefined,
  });
  const context = await browser.newContext();
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
  await page.goto(origin);
  if (shellOnly) {
    await page.waitForFunction(() =>
      document.querySelector("#status").textContent.startsWith("Spike error:"),
    );
    await page.click("#pulse");
    assert.equal(await page.textContent("#pulses"), "1");
    console.log(
      JSON.stringify(
        {
          browser: browser.version(),
          mode: "shell-only",
          error: await page.textContent("#status"),
          ui_responded: true,
          engine_execution_verified: false,
          external_requests: external,
        },
        null,
        2,
      ),
    );
  } else {
    const manifest = JSON.parse(await readFile(resolve(root, "assets.json")));
    assert(
      manifest.runtime_included,
      "Run prepare.py with runtime assets first",
    );
    await page.evaluate(() => window.spike.ready);
    const runtime = await page.evaluate(() => window.spike.info());
    let probeFinished = false;
    const probe = page
      .evaluate(() => window.spike.probe())
      .then((value) => {
        probeFinished = true;
        return value;
      });
    await page.click("#pulse");
    assert(!probeFinished, "UI must respond while the worker is still busy");
    const worker_probe = await probe;
    const results = [];
    async function exercise(offline) {
      assert.equal(
        (await page.evaluate(() => window.spike.fixtures())).passed,
        34,
      );
      for (const ruleset of ["brandubh", "tablut", "copenhagen"]) {
        const initial = await page.evaluate(
          (r) => window.spike.newGame(r),
          ruleset,
        );
        assert(initial.legal_moves.length > 0);
        const human = await page.evaluate(
          (move) => window.spike.move(move),
          initial.legal_moves[0],
        );
        assert.equal(human.state.ply, 1);
        const pendingAI = page.evaluate(() => window.spike.ai());
        await page.click("#pulse");
        const ai = await pendingAI;
        assert(
          human.legal_moves.some(
            (m) => m.source === ai.move.source && m.target === ai.move.target,
          ),
        );
        assert.equal(ai.state.ply, 2);
        results.push({ ruleset, offline, ai_ms: ai.ai_ms });
      }
    }
    await exercise(false);
    const startup_ms = await page.evaluate(
      () => window.spike.metrics.startup_ms,
    );
    await context.setOffline(true);
    // A reload creates a fresh worker/runtime. HTTP cache alone is insufficient.
    await page.reload();
    await page.evaluate(() => window.spike.ready);
    await exercise(true);
    assert.equal(external.length, 0);
    console.log(
      JSON.stringify(
        {
          browser: browser.version(),
          pyodide: manifest.pyodide,
          runtime,
          worker_probe,
          startup_ms,
          offline_startup_ms: await page.evaluate(
            () => window.spike.metrics.startup_ms,
          ),
          fixtures: 34,
          worker_ui_pulses: await page.textContent("#pulses"),
          results,
          external_requests: external,
          inventory: manifest.inventory,
        },
        null,
        2,
      ),
    );
  }
} finally {
  await browser?.close();
  server.close();
}
