// Browser Jarl benchmark gate for the isolated spike. Serves a staged directory
// (see prepare.py), runs the shared benchmark inside the Pyodide worker and
// writes a machine-readable report comparable with native_jarl_benchmark.py.
//
// Usage:
//   node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
//     --suite quick --mode fixed_time --output /tmp/jarl-browser.json
//   node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
//     --suite standard --native /tmp/jarl-native.json
import assert from "node:assert/strict";
import { readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { resolve, sep, extname } from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const argv = process.argv.slice(2);
const root = resolve(
  argv.find((arg) => !arg.startsWith("--")) || "work/phase0/offline",
);
const option = (name, fallback) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const suite = option("suite", "quick");
const mode = option("mode", "fixed_time");
const nativePath = option("native", null);
const output = option("output", null);
const positionsPath = option("positions", resolve(root, "jarl-positions.json"));
const mobile = argv.includes("--mobile");

const SUITES = {
  quick: { configs: ["quick"], reps: 1 },
  standard: { configs: ["quick", "interactive", "standard"], reps: 2 },
  extended: {
    configs: ["quick", "interactive", "standard", "deep", "extended"],
    reps: 3,
  },
};
assert.ok(SUITES[suite], `unknown suite: ${suite}`);
assert.ok(["fixed_time", "fixed_work"].includes(mode), `unknown mode: ${mode}`);
const { reps } = SUITES[suite];
const configs = option("configs", null)
  ? option("configs", "").split(",")
  : SUITES[suite].configs;
// Bound the whole batch: each config's worst-case time budget times the number
// of positions, plus serialization/startup margin.
const positionsPreview = JSON.parse(
  await readFile(positionsPath, "utf8"),
);
const maxTime = Math.max(
  ...configs.map((name) =>
    name === "deep" ? 5 : name === "standard" ? 2 : name === "interactive" ? 1 : 0.25,
  ),
);
const requestTimeoutMs = Math.min(
  180000,
  Math.ceil(
    positionsPreview.length * (mode === "fixed_work" ? 60000 : maxTime * 1000),
  ) + 15000,
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

const browser = await chromium.launch({
  executablePath: process.env.SPIKE_CHROMIUM || undefined,
});
try {
  const context = await browser.newContext(
    mobile
      ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }
      : { viewport: { width: 1920, height: 1080 } },
  );
  const page = await context.newPage();
  const started = performance.now();
  await page.goto(origin);
  await page.waitForFunction(() => window.spike);
  await page.evaluate(() => window.spike.ready);
  await page.evaluate(() => window.spike.info());
  const startupMs = performance.now() - started;
  const info = await page.evaluate(() => window.spike.info());
  const plan = await page.evaluate(() => window.spike.jarlPlan());
  const positions = await page.evaluate(() => window.spike.jarlPositions());
  const wanted = new Set(positionsPreview.map((entry) => entry.name));
  const selected = positions.filter((entry) => wanted.has(entry.name));
  assert.equal(selected.length, positionsPreview.length);
  assert.deepEqual(
    selected.map((entry) => entry.name),
    positionsPreview.map((entry) => entry.name),
  );
  assert.equal(plan.unsupported.extended, "engine alpha-beta depth cap is 8");

  // Warm-up: one bounded request so the shared benchmark module and search
  // caches are resident before measured runs.
  await page.evaluate(
    (payload) =>
      window.spike.jarlBenchmark(payload.positions, payload.configs, {
        mode: payload.mode,
        timeoutMs: 30000,
      }),
    {
      positions: positions.filter((entry) => entry.name.endsWith("king-escape")),
      configs: ["quick"],
      mode: "fixed_time",
    },
  );

  const runs = [];
  for (let repetition = 0; repetition < reps; repetition += 1) {
    for (const config of configs) {
      const subset = selected;
      const wallStart = performance.now();
      const result = await page.evaluate(
        (payload) =>
          window.spike.jarlBenchmark(payload.positions, payload.configs, {
            mode: payload.mode,
            seed: 7,
            timeoutMs: payload.timeoutMs,
          }),
        { positions: subset, configs: [config], mode, timeoutMs: requestTimeoutMs },
      );
      const wallMs = performance.now() - wallStart;
      for (const record of result.records) {
        runs.push({ ...record, phase: "measured", repetition, wall_ms: wallMs });
      }
    }
  }

  // UI responsiveness while a real worker search is busy.
  const responsiveness = await page.evaluate(async () => {
    const positions = await window.spike.jarlPositions();
    const busy = window.spike.jarlBenchmark(
      positions.filter((entry) => entry.name.endsWith("midgame")),
      ["standard"],
      { mode: "fixed_time", timeoutMs: 60000 },
    );
    await new Promise((resolve) => setTimeout(resolve, 250));
    const start = performance.now();
    document.querySelector("#pulse").click();
    const clickMs = performance.now() - start;
    await busy;
    return { click_ms: clickMs, pulses: document.querySelector("#pulses").textContent };
  });

  // Cancellation acknowledgement and worker restart after termination.
  const cancellation = await page.evaluate(async () => {
    const positions = await window.spike.jarlPositions();
    const busy = window.spike
      .jarlBenchmark(positions, ["standard"], {
        mode: "fixed_time",
        timeoutMs: 60000,
      })
      .then(
        () => "completed",
        (error) => error.name,
      );
    await new Promise((resolve) => setTimeout(resolve, 100));
    const start = performance.now();
    window.spike.cancel();
    const cancelMs = performance.now() - start;
    const outcome = await busy;
    const restartStart = performance.now();
    await window.spike.info();
    return {
      cancel_ack_ms: cancelMs,
      outcome,
      restart_ms: performance.now() - restartStart,
    };
  });
  assert.equal(cancellation.outcome, "AbortError");

  const memory = await page.evaluate(() => window.spike.memory());

  const report = {
    kind: "browser-jarl-benchmark",
    generated: new Date().toISOString(),
    suite,
    mode,
    configs,
    repetitions: reps,
    profile: mobile ? "mobile-emulated" : "desktop",
    browser: browser.version(),
    startup_ms: startupMs,
    request_timeout_ms: requestTimeoutMs,
    worker_info: info,
    plan,
    cancellation,
    responsiveness,
    memory,
    records: runs,
    summary: summarise(runs),
  };

  if (nativePath) {
    const native = JSON.parse(await readFile(nativePath, "utf8"));
    report.comparison = compare(report, native);
    assert.equal(
      report.comparison.plan_match,
      true,
      "native and browser benchmark plans must match",
    );
  }

  if (output) await writeFile(resolve(output), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
  await context.close();
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}

function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2
    ? ordered[middle]
    : (ordered[middle - 1] + ordered[middle]) / 2;
}

function percentile(values, fraction) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  if (ordered.length === 1) return ordered[0];
  const position = fraction * (ordered.length - 1);
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, ordered.length - 1);
  return ordered[lower] * (1 - (position - lower)) + ordered[upper] * (position - lower);
}

function summarise(records) {
  const groups = new Map();
  for (const record of records) {
    const key = `${record.config}|${record.ruleset}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, rows]) => {
      const [config, ruleset] = key.split("|");
      const measured = rows.filter((row) => row.phase === "measured");
      const elapsed = measured.map((row) => row.elapsed_ms);
      const depths = measured
        .map((row) => row.completed_depth)
        .filter((value) => value);
      return {
        config,
        ruleset,
        samples: measured.length,
        errors: measured.filter((row) => row.error).length,
        elapsed_ms_median: median(elapsed),
        elapsed_ms_p90: percentile(elapsed, 0.9),
        completed_depth_median: median(depths),
      };
    });
}

function compare(browserReport, nativeReport) {
  const planMatch =
    JSON.stringify(browserReport.plan.configs) ===
    JSON.stringify(nativeReport.plan.configs);
  const key = (row) => `${row.position}|${row.config}|${row.ruleset}`;
  const native = new Map();
  for (const row of nativeReport.records) {
    if (row.phase && row.phase !== "measured") continue;
    const list = native.get(key(row)) || [];
    list.push(row);
    native.set(key(row), list);
  }
  const rows = [];
  let comparable = 0;
  let moveAgreement = 0;
  let valueAgreement = 0;
  for (const row of browserReport.records) {
    const reference = native.get(key(row));
    if (!reference || !reference.length) continue;
    const first = reference[0];
    comparable += 1;
    const moveMatch =
      JSON.stringify(row.best_move) === JSON.stringify(first.best_move);
    const valueMatch =
      row.value != null &&
      first.value != null &&
      Math.abs(row.value - first.value) <= 1e-9;
    if (moveMatch) moveAgreement += 1;
    if (valueMatch) valueAgreement += 1;
    rows.push({
      position: row.position,
      config: row.config,
      ruleset: row.ruleset,
      native_best_move: first.best_move,
      browser_best_move: row.best_move,
      move_match: moveMatch,
      native_value: first.value,
      browser_value: row.value,
      value_match: valueMatch,
      native_completed_depth: first.completed_depth,
      browser_completed_depth: row.completed_depth,
      browser_elapsed_ms: row.elapsed_ms,
      native_elapsed_ms: first.elapsed_ms,
    });
  }
  return {
    plan_match: planMatch,
    comparable_cases: comparable,
    move_agreement: comparable ? moveAgreement / comparable : null,
    value_agreement: comparable ? valueAgreement / comparable : null,
    rows,
  };
}
