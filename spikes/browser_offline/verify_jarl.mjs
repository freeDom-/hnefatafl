// Browser Jarl benchmark gate for the isolated spike. Serves a staged directory
// (see prepare.py), runs the shared benchmark inside the Pyodide worker and
// writes a machine-readable report. When `--native` is supplied, the canonical
// Python comparator (jarl_compare) enforces the mandatory correctness gate and
// this process exits non-zero if it fails.
//
// Usage:
//   node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
//     --suite quick --mode fixed_work --output /tmp/jarl-browser.json
//   node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
//     --positions custom.json --mode fixed_work --native /tmp/jarl-native.json
//
// The verifier analyses the exact serialized state objects supplied by
// `--positions`. It never re-selects positions by name from the staged bundle,
// so custom positions and positions that share a name with a fixture but differ
// in board or repetition history are honoured.
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

// Run a child process with data written to its standard input. `execFile` does
// not support an `input` option, so the comparator is invoked through `spawn`.
function runWithStdin(command, args, input) {
  return new Promise((done) => {
    const child = spawn(command, args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (error) =>
      done({ code: 1, stdout, stderr: `${stderr}${error.message}` }),
    );
    child.on("close", (code) => done({ code: code ?? 1, stdout, stderr }));
    child.stdin.end(input);
  });
}
const argv = process.argv.slice(2);
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const flag = (name) => argv.includes(`--${name}`);
const root = resolve(argv.find((arg) => !arg.startsWith("--")) || "work/phase0/offline");

const SUITES = {
  quick: { configs: ["quick"], reps: 1 },
  standard: { configs: ["quick", "interactive", "standard"], reps: 2 },
  extended: { configs: ["quick", "interactive", "standard", "deep", "extended"], reps: 2 },
};
// One case is independently bounded well above the 60 s fixed-work cooperative
// deadline; the whole run is not held under a single oversized watchdog.
const CASE_TIMEOUT_MS = 75000;
const INIT_TIMEOUT_MS = 30000;

function usageError(message) {
  console.error(`verify_jarl: ${message}`);
  process.exit(2);
}

const suiteName = option("suite", "quick");
if (!SUITES[suiteName]) usageError(`unknown suite: ${suiteName}`);
const mode = option("mode", "fixed_time");
if (!["fixed_time", "fixed_work"].includes(mode)) usageError(`unknown mode: ${mode}`);
const reps = Number(option("reps", String(SUITES[suiteName].reps)));
if (!Number.isInteger(reps) || reps < 1 || reps > 50) usageError("--reps must be 1..50");
const warmup = Number(option("warmup", "1"));
if (!Number.isInteger(warmup) || warmup < 0 || warmup > 10) usageError("--warmup must be 0..10");
const seed = Number(option("seed", "7"));
if (!Number.isInteger(seed) || seed < 0) usageError("--seed must be a non-negative integer");
const configs = option("configs", null)
  ? option("configs", "").split(",")
  : SUITES[suiteName].configs;
if (!configs.length || configs.some((name) => !name)) usageError("empty --configs entry");
const nativePath = option("native", null);
const output = option("output", null);
const positionsPath = option("positions", resolve(root, "jarl-positions.json"));
const mobile = flag("mobile");
const python = process.env.SPIKE_PYTHON || resolve(import.meta.dirname, "../../.venv/bin/python");

const positionsRaw = await readFile(resolve(positionsPath));
const positions = JSON.parse(positionsRaw.toString("utf8"));
const positionsSha256 = createHash("sha256").update(positionsRaw).digest("hex");
if (!Array.isArray(positions) || positions.length === 0)
  usageError("--positions must contain a non-empty JSON array");
const names = positions.map((entry) => entry?.name);
if (new Set(names).size !== names.length) usageError("--positions contains duplicate names");

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

const report = {
  kind: "browser-jarl-benchmark",
  schema: 2,
  generated: new Date().toISOString(),
  runtime: "pyodide",
  suite: suiteName,
  mode,
  configs,
  repetitions: reps,
  warmup,
  case_timeout_ms: CASE_TIMEOUT_MS,
  profile: mobile ? "mobile-emulated" : "desktop",
  positions_file: positionsPath.split(/[\\/]/).pop(),
  positions_sha256: positionsSha256,
  browser: null,
  records: [],
  failures: [],
  batch: {},
};

let browser = null;
let context = null;
try {
  browser = await chromium.launch({ executablePath: process.env.SPIKE_CHROMIUM || undefined });
  report.browser = browser.version();
  context = await browser.newContext(
    mobile
      ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }
      : { viewport: { width: 1920, height: 1080 } },
  );
  const external = [];
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) {
      external.push(url.href);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") pageErrors.push(`console: ${message.text()}`);
  });

  const initStarted = performance.now();
  await page.goto(origin);
  await page.waitForFunction(() => window.spike, null, { timeout: INIT_TIMEOUT_MS });
  await page.evaluate(() => window.spike.ready);
  report.startup_ms = performance.now() - initStarted;
  report.worker_info = await page.evaluate(() => window.spike.info());
  report.plan = await page.evaluate(() => window.spike.jarlPlan());
  if (report.plan.unsupported.extended !== "engine alpha-beta depth cap is 8")
    throw new Error("engine depth constraint report changed");

  // Identity is computed inside the worker from the exact supplied positions,
  // so the digest proves the browser analysed the same workload as the native
  // reference rather than a fixture selected by name.
  report.identity = await page.evaluate(
    (payload) =>
      window.spike.jarlIdentity(payload.positions, payload.configs, {
        mode: payload.mode,
        seed: payload.seed,
      }),
    { positions, configs, mode, seed },
  );
  // The declared repetition and warm-up counts are part of the comparison
  // contract so expected coverage can be derived independently of the records
  // that were actually produced.
  report.identity.repetitions = reps;
  report.identity.warmup = warmup;

  const perCase = async (entry, config, phase, repetition) => {
    try {
      const record = await page.evaluate(
        (payload) =>
          window.spike.jarlCase(payload.position, payload.config, {
            mode: payload.mode,
            seed: payload.seed,
            timeoutMs: payload.timeoutMs,
          }),
        { position: entry, config, mode, seed, timeoutMs: CASE_TIMEOUT_MS },
      );
      return { ...record, phase, repetition };
    } catch (error) {
      // Record a failed case without discarding earlier records; the next case
      // starts from a freshly recreated worker if the worker died.
      return {
        position: entry.name,
        kind: entry.kind ?? null,
        config,
        mode,
        seed,
        ruleset: entry.state?.ruleset ?? null,
        phase,
        repetition,
        expected_depth: report.plan.configs?.[config]?.depth ?? null,
        terminal: null,
        unsupported: false,
        elapsed_ms: null,
        best_move: null,
        value: null,
        completed_depth: null,
        status: "error",
        case_failure: true,
        error: `${error?.name ?? "Error"}: ${error?.message ?? String(error)}`,
      };
    }
  };

  // Warm-up runs are excluded from measured statistics.
  for (let index = 0; index < warmup; index += 1) {
    for (const entry of positions) {
      for (const config of configs) {
        report.records.push(await perCase(entry, config, "warmup", index));
      }
    }
  }

  const batchStarted = performance.now();
  for (let repetition = 0; repetition < reps; repetition += 1) {
    for (const entry of positions) {
      for (const config of configs) {
        report.records.push(await perCase(entry, config, "measured", repetition));
      }
    }
  }
  report.batch = {
    wall_clock_ms: performance.now() - batchStarted,
    measured_cases: report.records.filter((row) => row.phase === "measured").length,
  };

  // UI responsiveness while a real worker search is active: drive an actual
  // Playwright click and measure event-loop lag, not a synchronous DOM call.
  await page.evaluate(() => {
    window.__lag = [];
    let last = performance.now();
    const tick = () => {
      const now = performance.now();
      window.__lag.push(now - last);
      last = now;
      if (window.__lag.length < 4000) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  const busySearch = page
    .evaluate(
      (payload) =>
        window.spike.jarlCase(payload.position, payload.config, {
          mode: "fixed_time",
          seed: payload.seed,
          timeoutMs: payload.timeoutMs,
        }),
      {
        position: positions.find((entry) => entry.kind === "midgame") ?? positions[0],
        config: "standard",
        seed,
        timeoutMs: CASE_TIMEOUT_MS,
      },
    )
    .catch(() => null);
  await new Promise((done) => setTimeout(done, 250));
  const clickStart = performance.now();
  await page.click("#pulse");
  const clickMs = performance.now() - clickStart;
  const pulses = await page.textContent("#pulses");
  await busySearch;
  const lag = await page.evaluate(() => window.__lag ?? []);
  report.responsiveness = {
    click_to_handle_ms: clickMs,
    pulses,
    max_event_loop_lag_ms: lag.length ? Math.max(...lag) : null,
    median_event_loop_lag_ms: lag.length
      ? lag.slice().sort((a, b) => a - b)[Math.floor(lag.length / 2)]
      : null,
  };

  // Cancellation acknowledgement and worker restart after termination. The
  // error name is captured inside the page because Playwright rewrites a
  // rejected DOMException into a generic Error at the boundary.
  report.cancellation = await page.evaluate(
    async (payload) => {
      const busy = window.spike
        .jarlBenchmark(payload.positions, payload.configs, {
          mode: "fixed_time",
          seed: payload.seed,
          timeoutMs: payload.timeoutMs,
        })
        .then(
          () => "completed",
          (error) => error?.name ?? String(error),
        );
      await new Promise((done) => setTimeout(done, 100));
      const cancelStart = performance.now();
      window.spike.cancel();
      const cancelMs = performance.now() - cancelStart;
      const outcome = await busy;
      const restartStart = performance.now();
      await window.spike.info();
      return {
        cancel_ack_ms: cancelMs,
        outcome,
        restart_ms: performance.now() - restartStart,
      };
    },
    { positions, configs: ["standard"], seed, timeoutMs: CASE_TIMEOUT_MS },
  );

  report.page_errors = pageErrors;
  report.external_requests = external;
  if (pageErrors.length) report.failures.push(`page errors: ${pageErrors.length}`);
  if (external.length) report.failures.push(`external requests: ${external.length}`);
  if (report.cancellation.outcome !== "AbortError")
    report.failures.push(
      `cancellation outcome was ${report.cancellation.outcome}, expected AbortError`,
    );

  const measured = report.records.filter((row) => row.phase === "measured");
  const failed = measured.filter((row) => row.case_failure || row.status === "error");
  if (!measured.length) report.failures.push("no measured cases were executed");
  if (failed.length) report.failures.push(`${failed.length} measured case(s) failed to execute`);

  if (nativePath) {
    const comparisonOut = resolve(
      dirname(output ? resolve(output) : resolve(root, "jarl-unused.json")),
      `jarl-comparison-${process.pid}-${Date.now()}.json`,
    );
    await unlink(comparisonOut).catch(() => {});
    const comparison = await runWithStdin(
      python,
      [
        resolve(import.meta.dirname, "compare_jarl_benchmarks.py"),
        resolve(nativePath),
        "-",
        "--output",
        comparisonOut,
      ],
      JSON.stringify(report),
    );
    report.comparison_exit_code = comparison.code;
    try {
      report.comparison = JSON.parse(await readFile(comparisonOut, "utf8"));
    } catch {
      report.comparison = { stderr: String(comparison.stderr).slice(-2000) };
    }
    if (comparison.code !== 0) {
      report.failures.push(
        ...(report.comparison.failures ?? ["native/browser comparison failed"]),
      );
    }
    report.comparison_output = comparisonOut.split(/[\\/]/).pop();
  }
} catch (error) {
  report.failures.push(`harness error: ${error?.message ?? String(error)}`);
} finally {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  await new Promise((done) => server.close(done));
}

report.ok = report.failures.length === 0;
if (output) {
  await mkdir(dirname(resolve(output)), { recursive: true });
  await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
}
console.log(
  JSON.stringify(
    {
      ok: report.ok,
      records: report.records.length,
      failures: report.failures,
      startup_ms: report.startup_ms,
      batch: report.batch,
      comparison: report.comparison
        ? {
            ok: report.comparison.ok,
            expected_cases: report.comparison.expected_cases,
            compared_cases: report.comparison.compared_cases,
            execution_valid_cases: report.comparison.execution_valid_cases,
            parity_cases: report.comparison.parity_cases,
            parity_established: report.comparison.parity_established,
            acceptance_complete: report.comparison.acceptance_complete,
            unresolved: (report.comparison.unresolved ?? []).length,
            equal_work_latency_ratio_median:
              report.comparison.equal_work_latency_ratio_median,
            equal_work_latency_ratio_samples:
              report.comparison.equal_work_latency_ratio_samples,
            warnings: (report.comparison.warnings ?? []).length,
          }
        : null,
    },
    null,
    2,
  ),
);
process.exit(report.ok ? 0 : 1);
