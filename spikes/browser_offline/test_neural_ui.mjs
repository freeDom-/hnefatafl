// Actual-browser UI interaction tests for the standalone neural experiment.
//
// Covers clean-state Infer/batch/benchmark, operation ownership through delayed
// asset loading, cancellation/supersession, and provider recovery. The provider
// test is split into a deterministic simulated failure and an outcome-agnostic
// hardware check (WebGPU may legitimately succeed on real GPUs).
//
//   node spikes/browser_offline/test_neural_ui.mjs /tmp/neural-spike --output /tmp/ui.json
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
  kind: "browser-neural-ui",
  generated: new Date().toISOString(),
  checks: [],
  failures: [],
};
let server = null;
let browser = null;
let context = null;
try {
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

  async function freshPage(setup, { waitReady = true } = {}) {
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    const startIndex = requested.length;
    if (setup) await setup(page);
    await page.goto(origin + "/neural.html");
    if (waitReady) {
      await page.waitForFunction(() => window.neural && window.neural.ready, null, {
        timeout: 30000,
      });
    } else {
      // The page script exists, but readiness (and shared asset loading) is
      // intentionally still in flight so cancellation can be observed.
      await page.waitForFunction(() => window.neural, null, { timeout: 30000 });
    }
    return { page, errors, startIndex, requested };
  }
  async function state(page) {
    return page.textContent("#state");
  }
  async function waitState(page, expected, timeout = 60000) {
    const targets = Array.isArray(expected) ? expected : [expected];
    await page.waitForFunction(
      (values) => values.includes(document.querySelector("#state")?.textContent),
      targets,
      { timeout },
    );
  }
  async function clearOutput(page) {
    await page.evaluate(() => {
      document.querySelector("#output").textContent = "";
    });
  }
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
  const delay = (ms) => new Promise((done) => setTimeout(done, ms));

  await record("Infer works from a clean initial state", async () => {
    const { page, errors } = await freshPage();
    await clearOutput(page);
    await page.click("#infer");
    await waitState(page, "completed");
    const text = await page.textContent("#output");
    assert.match(text, /"provider_requested"/);
    assert.match(text, /"model_sha256"/);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Distinct batch inference works from a clean initial state", async () => {
    const { page, errors } = await freshPage();
    await clearOutput(page);
    await page.click("#inferMany");
    await waitState(page, "completed");
    const text = await page.textContent("#output");
    assert.match(text, /"batch": 8/);
    assert.match(text, /"results_count": 8/);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Benchmark works from a clean initial state", async () => {
    const { page, errors } = await freshPage();
    await clearOutput(page);
    await page.click("#benchmark");
    await waitState(page, "completed", 90000);
    const text = await page.textContent("#output");
    assert.match(text, /"rows"/);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Controls disable during an active operation", async () => {
    const { page } = await freshPage();
    await page.click("#infer");
    await page.waitForFunction(() => document.querySelector("#load")?.disabled === true, null, {
      timeout: 5000,
    });
    assert.equal(await page.isDisabled("#provider"), true);
    await waitState(page, "completed");
    await page.waitForFunction(() => document.querySelector("#load")?.disabled === false, null, {
      timeout: 5000,
    });
    await page.close();
  });

  await record("Cancellation invalidates stale results and reports cancelled", async () => {
    const { page } = await freshPage();
    await clearOutput(page);
    await page.click("#benchmark");
    await waitState(page, "running", 30000);
    await page.click("#cancel");
    assert.equal(await state(page), "cancelled");
    await page.waitForTimeout(300);
    assert.equal(await state(page), "cancelled", "stale operation must not overwrite status");
    await page.close();
  });

  await record("Cancellation during manifest loading does not initialize a worker", async () => {
    let first = true;
    const { page, errors } = await freshPage(
      async (page) => {
        await page.route("**/neural-assets.json", async (route) => {
          if (first) {
            first = false;
            await delay(1200);
          }
          await route.continue();
        });
      },
      { waitReady: false },
    );
    await page.evaluate(() => {
      // Force a fresh fetch even though readiness warmed the cache.
      window.neural.client.model = null;
    });
    await page.click("#infer");
    await page.waitForTimeout(150);
    await page.click("#cancel");
    await delay(1600);
    assert.equal(await state(page), "cancelled");
    const internals = await page.evaluate(() => ({
      worker: window.neural.client.worker !== null,
      model: window.neural.client.model !== null,
    }));
    assert.deepEqual(internals, { worker: false, model: false });
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Cancellation during reference loading does not publish a model", async () => {
    let first = true;
    const { page } = await freshPage(
      async (page) => {
        await page.route("**/model/reference.json", async (route) => {
          if (first) {
            first = false;
            await delay(1200);
          }
          await route.continue();
        });
      },
      { waitReady: false },
    );
    await page.click("#infer");
    await page.waitForTimeout(150);
    await page.click("#cancel");
    await delay(1600);
    assert.equal(await state(page), "cancelled");
    const internals = await page.evaluate(() => ({
      worker: window.neural.client.worker !== null,
      model: window.neural.client.model !== null,
    }));
    assert.deepEqual(internals, { worker: false, model: false });
    await page.close();
  });

  await record("Replacement operation after cancellation completes", async () => {
    let delayed = true;
    const { page } = await freshPage(
      async (page) => {
        await page.route("**/neural-assets.json", async (route) => {
          if (delayed) {
            delayed = false;
            await delay(1200);
          }
          await route.continue();
        });
      },
      { waitReady: false },
    );
    await page.click("#infer");
    await page.waitForTimeout(150);
    await page.click("#cancel");
    await waitState(page, "cancelled");
    await page.click("#infer");
    await waitState(page, "completed", 60000);
    const model = await page.evaluate(() => window.neural.client.model?.model_sha256 ?? null);
    assert.ok(model, "replacement operation must publish a model");
    await page.close();
  });

  await record("Obsolete asset completion cannot overwrite a newer operation", async () => {
    let delayed = true;
    const { page } = await freshPage(
      async (page) => {
        await page.route("**/neural-assets.json", async (route) => {
          if (delayed) {
            delayed = false;
            await delay(1000);
          }
          await route.continue();
        });
      },
      { waitReady: false },
    );
    await page.click("#infer");
    await page.waitForTimeout(100);
    await page.click("#cancel");
    await page.click("#inferMany");
    await waitState(page, "completed", 60000);
    await page.waitForTimeout(1200);
    assert.equal(await state(page), "completed", "obsolete completion must not publish");
    const text = await page.textContent("#output");
    assert.match(text, /"batch": 8/);
    await page.close();
  });

  await record("Deterministic provider failure recovers on WASM", async () => {
    const { page } = await freshPage();
    await page.evaluate(() => {
      const client = window.neural.client;
      client.__realLoad = client.load.bind(client);
      client.load = async (url, options) => {
        if (options.provider === "webgpu") {
          const error = new Error("simulated provider initialization failure");
          error.name = "ProviderError";
          throw error;
        }
        return client.__realLoad(url, options);
      };
    });
    await page.selectOption("#provider", "webgpu");
    await page.click("#load");
    await waitState(page, "failed", 30000);
    assert.match(await page.textContent("#status"), /simulated provider/i);
    await page.selectOption("#provider", "wasm");
    await page.click("#load");
    await waitState(page, "completed", 60000);
    await page.close();
  });

  await record("WebGPU provider load reports a legitimate outcome", async () => {
    const { page } = await freshPage();
    await page.selectOption("#provider", "webgpu");
    await page.click("#load");
    await waitState(page, ["completed", "failed"], 60000);
    const result = await state(page);
    report.webgpu_outcome = result;
    if (result === "completed") {
      // Real GPU hardware: validate the session and a numerical response, but do
      // not claim GPU-only placement.
      const loaded = await page.evaluate(() => ({
        provider: window.neural.client.model?.provider,
        placement: window.neural.client.model?.execution_placement,
        sha: window.neural.client.model?.model_sha256,
      }));
      assert.equal(loaded.provider, "webgpu");
      assert.ok(loaded.sha);
      assert.notEqual(loaded.placement, "cpu-fallback");
      await page.click("#infer");
      await waitState(page, "completed", 60000);
      const text = await page.textContent("#output");
      assert.match(text, /"provider_requested": "webgpu"/);
      report.webgpu_placement = loaded.placement;
    } else {
      const message = await page.textContent("#status");
      assert.match(message, /adapter|WebGPU|unsupported|provider/i);
    }
    await page.close();
  });

  await record("Worker rejects benchmark and sustained without a model identity", async () => {
    const { page, errors } = await freshPage();
    const result = await page.evaluate(async () => {
      const client = window.neural.client;
      const { assets: staged } = await window.neural.assets();
      const loaded = await client.load("./" + staged.model.file, {
        provider: "wasm",
        size: staged.model.size,
        ruleset: staged.model.ruleset,
        expectedSha256: staged.model.sha256,
      });
      const cases = [
        {
          planes: [0, 1],
          legal: [0],
          request_id: "control",
          position_digest: "c".repeat(64),
        },
      ];
      const codes = {};
      for (const action of ["benchmark", "sustained"]) {
        const request =
          action === "benchmark"
            ? { action, cases, batchSizes: [1], model: null }
            : { action, cases, iterations: 1, model: null };
        try {
          await client.request(request, { timeoutMs: 30000 });
          codes[action] = "accepted";
        } catch (error) {
          codes[action] = error?.code ?? error?.name ?? "unknown";
        }
      }
      return { codes, loaded: !!loaded.model_sha256 };
    });
    assert.equal(result.loaded, true);
    assert.equal(result.codes.benchmark, "MODEL_IDENTITY_REQUIRED");
    assert.equal(result.codes.sustained, "MODEL_IDENTITY_REQUIRED");
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Manifest failure on first use is retryable without a reload", async () => {
    let failed = false;
    const { page, errors, startIndex, requested } = await freshPage(
      async (page) => {
        await page.route("**/neural-assets.json", async (route) => {
          if (!failed) {
            failed = true;
            return route.fulfill({ status: 500, body: "unavailable" });
          }
          return route.continue();
        });
      },
      { waitReady: false },
    );
    assert.equal(
      requested.slice(startIndex).some((path) => path.includes("neural-assets.json")),
      false,
      "nothing neural may be fetched before an operation asks for it",
    );
    await page.click("#infer");
    await waitState(page, "failed", 60000);
    assert.match(await page.textContent("#status"), /500|Cannot load/i);
    await page.click("#infer");
    await waitState(page, "completed", 60000);
    assert.match(await page.textContent("#output"), /"model_sha256"/);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Reference-vector failure on first use is retryable", async () => {
    let failed = false;
    const { page, errors, startIndex, requested } = await freshPage(
      async (page) => {
        await page.route("**/model/reference.json", async (route) => {
          if (!failed) {
            failed = true;
            return route.fulfill({ status: 503, body: "later" });
          }
          return route.continue();
        });
      },
      { waitReady: false },
    );
    assert.equal(
      requested.slice(startIndex).some((path) => path.includes("reference.json")),
      false,
      "reference vectors must not load before a raw inference operation",
    );
    await page.click("#infer");
    await waitState(page, "failed", 60000);
    await page.click("#infer");
    await waitState(page, "completed", 60000);
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("Malformed asset JSON is rejected and retried", async () => {
    let failed = false;
    const { page } = await freshPage(
      async (page) => {
        await page.route("**/neural-assets.json", async (route) => {
          if (!failed) {
            failed = true;
            return route.fulfill({
              status: 200,
              contentType: "application/json",
              body: "{not json",
            });
          }
          return route.continue();
        });
      },
      { waitReady: false },
    );
    await page.click("#infer");
    await waitState(page, "failed", 60000);
    assert.match(await page.textContent("#status"), /JSON/i);
    await page.click("#infer");
    await waitState(page, "completed", 60000);
    await page.close();
  });

  await record("pagehide invalidates ownership and prevents worker creation", async () => {
    let delayed = true;
    const { page, errors } = await freshPage(
      async (page) => {
        await page.route("**/neural-assets.json", async (route) => {
          if (delayed) {
            delayed = false;
            await delay(800);
          }
          await route.continue();
        });
      },
      { waitReady: false },
    );
    await page.click("#infer");
    await page.waitForTimeout(100);
    await page.evaluate(() => window.neural.shutdown("test pagehide"));
    await delay(1000);
    assert.equal(await state(page), "cancelled");
    const internals = await page.evaluate(() => ({
      worker: window.neural.client.worker !== null,
      model: window.neural.client.model !== null,
      spawns: window.neural.diagnostics().workerSpawns,
    }));
    assert.deepEqual(internals, { worker: false, model: false, spawns: 0 });
    assert.equal(errors.length, 0, errors.join("; "));
    await page.close();
  });

  await record("pagehide during model loading does not publish a model", async () => {
    let delayed = false;
    const { page } = await freshPage(async (page) => {
      await page.route("**/model/model.onnx", async (route) => {
        if (!delayed) {
          delayed = true;
          await delay(900);
        }
        await route.continue();
      });
    });
    await page.click("#infer");
    await waitState(page, "loading", 30000);
    await page.evaluate(() => window.neural.shutdown("test pagehide during load"));
    await delay(1200);
    const internals = await page.evaluate(() => ({
      model: window.neural.client.model !== null,
      worker: window.neural.client.worker !== null,
    }));
    assert.deepEqual(internals, { model: false, worker: false });
    await page.close();
  });

  await record("Pageshow restoration permits a new operation", async () => {
    const { page } = await freshPage();
    await page.evaluate(() =>
      window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })),
    );
    await page.evaluate(() =>
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })),
    );
    await page.click("#infer");
    await waitState(page, "completed", 60000);
    await page.close();
  });

  await record("Navigation away and back recovers without a reload", async () => {
    const { page } = await freshPage();
    await page.goto("about:blank");
    await page.goBack();
    await page.waitForFunction(() => window.neural, null, { timeout: 30000 });
    await page.click("#infer");
    await waitState(page, "completed", 60000);
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
