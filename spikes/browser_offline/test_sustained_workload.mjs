// Deterministic checks for the sustained workload driver core, plus an optional
// short actual-browser run when SPIKE_NEURAL_DIR points at staged assets.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  exitCode,
  newCounters,
  recordBatch,
  recordBatchSuccess,
  recordCleanupFailure,
  recordFailure,
  recordSuccess,
  recordWorkers,
  selectAnalysisPositions,
  telemetryRecord,
} from "./sustained_workload_core.mjs";

const checks = [];
async function check(name, fn) {
  try {
    await fn();
    checks.push({ name, ok: true });
  } catch (error) {
    checks.push({ name, ok: false, error: String(error?.message ?? error) });
  }
}


function successOutcome({ completed = 8, inferences = 8, reason = "simulations" } = {}) {
  return {
    best_move: { source: 0, target: 1 },
    settings: {
      algorithm: "mcts",
      iterations: Math.max(1, completed),
      time_limit: 1,
      exploration: 1.4,
      seed: 7,
    },
    search: {
      completed_simulations: completed,
      completion_reason: reason,
      coordinator: { inferences },
      provider:
        inferences > 0
          ? { requested: "wasm", execution_placement: "wasm", gpu_execution_verified: false }
          : null,
    },
  };
}
const opening = { name: "opening", kind: "opening", ruleset: "brandubh", state: { ruleset: "brandubh", winner: null } };
const midgame = {
  name: "midgame",
  kind: "midgame",
  ruleset: "brandubh",
  state: { ruleset: "brandubh", winner: null, ply: 5 },
};
const terminal = { name: "terminal", kind: "terminal", ruleset: "brandubh", state: { ruleset: "brandubh", winner: "defenders" } };
const escape = { name: "king-escape", kind: "king-escape", ruleset: "brandubh", state: { ruleset: "brandubh", winner: null } };

await check("terminal and immediate-win fixtures are excluded", () => {
  const selected = selectAnalysisPositions([terminal, escape, opening, midgame], "brandubh");
  assert.deepEqual(selected.map((entry) => entry.name), ["opening", "midgame"]);
});

await check("duplicate and wrong-ruleset positions are excluded", () => {
  const selected = selectAnalysisPositions(
    [opening, { ...opening }, { ...opening, ruleset: "tablut" }, midgame],
    "brandubh",
  );
  assert.deepEqual(selected.map((entry) => entry.name), ["opening", "midgame"]);
});

await check("successful neural searches count work and progress", () => {
  const counters = newCounters();
  const result = recordSuccess(counters, successOutcome({ completed: 64, inferences: 61 }));
  assert.equal(result.completed, 64);
  assert.equal(counters.completed_requests, 1);
  assert.equal(counters.neural_searches, 1);
  assert.equal(counters.neural_inferences, 61);
  assert.equal(counters.completed_simulations, 64);
  assert.equal(exitCode(counters), 0);
});

await check("no-inference shortcuts are not neural analysis progress", () => {
  const counters = newCounters();
  recordSuccess(counters, successOutcome({ completed: 0, inferences: 0, reason: "immediate_win" }));
  assert.equal(counters.completed_requests, 1);
  assert.equal(counters.neural_searches, 0);
  assert.equal(counters.no_inference_shortcuts, 1);
  assert.equal(counters.completed_simulations, 0);
});

await check("malformed or incomplete outcomes are rejected", () => {
  for (const outcome of [
    null,
    {},
    { best_move: { source: 0, target: 1 } },
    { ...successOutcome(), best_move: null },
    { ...successOutcome({ completed: -1 }) },
    { ...successOutcome({ inferences: 1.5 }) },
    { ...successOutcome({ reason: "unknown" }) },
  ]) {
    const counters = newCounters();
    assert.throws(() => recordSuccess(counters, outcome));
    assert.equal(counters.completed_requests, 0);
  }
});

await check("provider fallback and invented observations are rejected", () => {
  const fallback = successOutcome();
  fallback.search.provider = {
    requested: "webgpu",
    execution_placement: "cpu-fallback",
    gpu_execution_verified: false,
  };
  assert.throws(() => recordSuccess(newCounters(), fallback));
  const invented = successOutcome({ completed: 0, inferences: 0, reason: "immediate_win" });
  invented.search.provider = {
    requested: "wasm",
    execution_placement: "wasm",
    gpu_execution_verified: false,
  };
  assert.throws(() => recordSuccess(newCounters(), invented));
});

await check("cleanup failures are explicit and non-successful", () => {
  const counters = newCounters();
  recordSuccess(counters, successOutcome());
  recordCleanupFailure(counters);
  assert.equal(counters.cleanup_failures, 1);
  assert.equal(counters.workload_failures, 1);
  assert.equal(exitCode(counters), 2);
});

await check("failed searches are failure-only and never successful progress", () => {
  const counters = newCounters();
  recordFailure(counters);
  assert.equal(counters.workload_failures, 1);
  assert.equal(counters.completed_requests, 0);
  assert.equal(exitCode(counters), 1);
  recordSuccess(counters, successOutcome());
  assert.equal(exitCode(counters), 2);
});

await check("telemetry binds identity and cumulative work", () => {
  const counters = newCounters();
  recordSuccess(counters, successOutcome());
  const record = telemetryRecord(
    counters,
    { session: "s", modelSha256: "a".repeat(64), workload: "sustained-analysis" },
  );
  assert.equal(record.session_id, "s");
  assert.equal(record.model_sha256, "a".repeat(64));
  assert.equal(record.successful_searches, 1);
  assert.equal(record.neural_inferences, 8);
});

await check("requested provider and effective settings are verified", () => {
  const expected = successOutcome().settings;
  const wrongProvider = successOutcome();
  wrongProvider.search.provider = {
    requested: "webgpu",
    execution_placement: "wasm",
    gpu_execution_verified: false,
  };
  assert.throws(() => recordSuccess(newCounters(), wrongProvider, { provider: "wasm", settings: expected }));
  const wrongPlacement = successOutcome();
  wrongPlacement.search.provider = {
    requested: "wasm",
    execution_placement: "webgpu-session-unverified",
    gpu_execution_verified: false,
  };
  assert.throws(() => recordSuccess(newCounters(), wrongPlacement, { provider: "wasm", settings: expected }));
  const fallback = successOutcome();
  fallback.search.provider = {
    requested: "webgpu",
    execution_placement: "wasm",
    gpu_execution_verified: false,
  };
  assert.throws(() => recordSuccess(newCounters(), fallback, { provider: "webgpu", settings: expected }));
  const badFlag = successOutcome();
  badFlag.search.provider = {
    requested: "wasm",
    execution_placement: "wasm",
    gpu_execution_verified: "false",
  };
  assert.throws(() => recordSuccess(newCounters(), badFlag, { provider: "wasm", settings: expected }));
  const wrongIterations = successOutcome();
  wrongIterations.settings = { ...wrongIterations.settings, iterations: 9 };
  assert.throws(() =>
    recordSuccess(newCounters(), wrongIterations, { provider: "wasm", settings: expected }),
  );
  for (const [field, value] of [
    ["time_limit", 2],
    ["exploration", 1.5],
    ["seed", 8],
  ]) {
    const wrongSettings = successOutcome();
    wrongSettings.settings = { ...wrongSettings.settings, [field]: value };
    assert.throws(
      () =>
        recordSuccess(newCounters(), wrongSettings, {
          provider: "wasm",
          settings: expected,
        }),
      field,
    );
  }
  const shortcut = successOutcome({ completed: 0, inferences: 0, reason: "immediate_win" });
  const counters = newCounters();
  recordSuccess(counters, shortcut, { provider: "wasm", settings: shortcut.settings });
  assert.equal(counters.no_inference_shortcuts, 1);
  assert.equal(counters.neural_searches, 0);
});

await check("webgpu unverified and mixed observations remain classified", () => {
  for (const placement of ["webgpu-session-unverified", "webgpu-mixed"]) {
    const outcome = successOutcome();
    outcome.search.provider = {
      requested: "webgpu",
      execution_placement: placement,
      gpu_execution_verified: false,
    };
    const counters = newCounters();
    recordSuccess(counters, outcome, { provider: "webgpu", settings: outcome.settings });
    assert.equal(counters.neural_searches, 1);
  }
});

await check("peak worker budget evidence preserves separate classifications", () => {
  const counters = newCounters();
  recordWorkers(counters, { search_workers: 3, inference_workers: 1 });
  assert.equal(counters.peak_search_workers, 3);
  assert.equal(counters.peak_inference_workers, 1);
  assert.equal(counters.peak_total_workers, 4);
  assert.throws(() => recordWorkers(counters, { search_workers: -1, inference_workers: 0 }));
  assert.throws(() => recordWorkers(counters, { search_workers: "2", inference_workers: 0 }));
});

await check("batch accounting uses cumulative deltas and handles resets", () => {
  const counters = newCounters();
  let previous = recordBatch(
    counters,
    { batches: 3, multi_request_batches: 2, observed_max_batch: 2 },
    null,
    10,
  );
  assert.equal(counters.batches, 3);
  assert.equal(counters.observed_max_batch, 2);
  previous = recordBatch(
    counters,
    { batches: 5, multi_request_batches: 4, observed_max_batch: 2 },
    previous,
    12,
  );
  assert.equal(counters.batches, 5);
  previous = recordBatch(
    counters,
    { batches: 2, multi_request_batches: 1, observed_max_batch: 2 },
    previous,
    8,
  );
  assert.equal(counters.batches, 7);
  assert.equal(counters.batch_counter_resets, 1);
  assert.equal(counters.batch_rounds, 3);
  assert.equal(counters.multi_request_batches, 5);
  recordBatchSuccess(counters, 9);
  recordBatchSuccess(counters, 4);
  assert.equal(counters.batch_successful_rounds, 2);
  assert.equal(counters.batch_search_max_elapsed_ms, 9);
  recordWorkers(counters, { search_workers: 3, inference_workers: 1 });
  recordWorkers(counters, { search_workers: 2, inference_workers: 1 });
  assert.equal(counters.peak_search_workers, 3);
  assert.equal(counters.peak_total_workers, 4);
});

await check("invalid usage exits with a bounded error status", async () => {
  const child = spawn(process.execPath, [resolve(import.meta.dirname, "sustained_workload.mjs"), "--duration", "1"], {
    stdio: ["ignore", "ignore", "pipe"],
  });
  const code = await new Promise((done) => child.on("exit", done));
  assert.equal(code, 2);
});

const failed = checks.filter((entry) => !entry.ok);
console.log(JSON.stringify({ kind: "sustained-workload-checks", checks: checks.length, failed }, null, 2));
if (failed.length) process.exit(1);

if (process.env.SPIKE_NEURAL_DIR) {
  const root = resolve(process.env.SPIKE_NEURAL_DIR);
  const directory = await mkdtemp(join(tmpdir(), "sustained-workload-"));
  const telemetry = join(directory, "telemetry.jsonl");
  const ready = join(directory, "ready.json");
  const output = join(directory, "summary.json");
  const child = spawn(
    process.execPath,
    [
      resolve(import.meta.dirname, "sustained_workload.mjs"),
      root,
      "--duration",
      "4",
      "--interval",
      "0.1",
      "--mode",
      "repeated",
      "--session",
      "driver-test",
      "--telemetry",
      telemetry,
      "--ready",
      ready,
      "--overwrite",
      "--iterations",
      "8",
      "--time-limit",
      "1",
      "--output",
      output,
    ],
    { stdio: ["ignore", "ignore", "inherit"] },
  );
  const code = await new Promise((done) => child.on("exit", done));
  assert.equal(code, 0);
  const summary = JSON.parse(await readFile(output, "utf8"));
  assert.ok(summary.counters.successful_searches >= 1);
  assert.equal(summary.counters.workload_failures, 0);
  assert.equal(summary.session_id, "driver-test");
  assert.ok(summary.provider_placements.every((item) => item.startsWith("wasm/")));
  assert.ok(summary.model_provider_placements.includes("wasm/wasm"));
  const readyDocument = JSON.parse(await readFile(ready, "utf8"));
  assert.equal(readyDocument.ready, true);
  assert.equal(readyDocument.session, "driver-test");
  const lines = (await readFile(telemetry, "utf8")).trim().split("\n").filter(Boolean);
  assert.ok(lines.length >= 1);
  const first = JSON.parse(lines[0]);
  assert.equal(first.session_id, "driver-test");
  assert.equal(first.workload, "sustained-analysis");
  console.log(JSON.stringify({ kind: "sustained-workload-browser-check", ok: true, requests: summary.counters.completed_requests }));

  async function runMode(mode, extraArgs = []) {
    const modeTelemetry = join(directory, `telemetry-${mode}.jsonl`);
    const modeOutput = join(directory, `summary-${mode}.json`);
    const modeReady = join(directory, `ready-${mode}.json`);
    const childProcess = spawn(
      process.execPath,
      [
        resolve(import.meta.dirname, "sustained_workload.mjs"),
        root,
        "--duration",
        "5",
        "--interval",
        "0.1",
        "--mode",
        mode,
        "--session",
        `driver-test-${mode}`,
        "--telemetry",
        modeTelemetry,
        "--ready",
        modeReady,
        "--overwrite",
        "--iterations",
        "8",
        "--time-limit",
        "1",
        "--output",
        modeOutput,
        ...extraArgs,
      ],
      { stdio: ["ignore", "ignore", "inherit"] },
    );
    const code = await new Promise((done) => childProcess.on("exit", done));
    assert.equal(code, 0, `${mode} exit status`);
    return JSON.parse(await readFile(modeOutput, "utf8"));
  }

  const cancel = await runMode("cancel-restart");
  assert.ok(cancel.counters.cancellations >= 1);
  const reload = await runMode("model-reload");
  assert.ok(reload.counters.reloads >= 1);
  const replacement = await runMode("worker-replacement");
  assert.ok(replacement.counters.worker_replacements >= 1);
  assert.ok(
    replacement.lifecycle_events.some(
      (event) =>
        event.kind === "worker-replacement" &&
        event.worker_generation_after > event.worker_generation_before,
    ),
  );
  const disposal = await runMode("client-disposal");
  assert.ok(disposal.counters.client_disposals >= 1);
  assert.ok(
    disposal.lifecycle_events.some(
      (event) =>
        event.kind === "client-disposal" && event.disposed && event.recreated,
    ),
  );
  const cacheChurn = await runMode("cache-churn", ["--cache"]);
  assert.ok(cacheChurn.counters.cache_evictions >= 1);
  const batching = await runMode("batching");
  assert.ok(batching.counters.batches >= 1);
  assert.ok(batching.counters.observed_max_batch >= 2);
  assert.ok(batching.counters.batch_successful_rounds >= 2);
  assert.ok(batching.counters.peak_total_workers >= 3);
  assert.ok(batching.counters.multi_request_batches >= 2);
  assert.ok(batching.counters.batch_search_max_elapsed_ms > 0);
  async function runFailureMode(mode, extraArgs = []) {
    const failureTelemetry = join(directory, `telemetry-${mode}-failure.jsonl`);
    const failureOutput = join(directory, `summary-${mode}-failure.json`);
    const failureReady = join(directory, `ready-${mode}-failure.json`);
    const childProcess = spawn(
      process.execPath,
      [
        resolve(import.meta.dirname, "sustained_workload.mjs"),
        root,
        "--duration",
        "5",
        "--interval",
        "0.1",
        "--mode",
        mode,
        "--session",
        `driver-test-${mode}-failure`,
        "--telemetry",
        failureTelemetry,
        "--ready",
        failureReady,
        "--overwrite",
        "--iterations",
        "8",
        "--time-limit",
        "1",
        "--output",
        failureOutput,
        ...extraArgs,
      ],
      { stdio: ["ignore", "ignore", "inherit"] },
    );
    const code = await new Promise((done) => childProcess.on("exit", done));
    return { code, summary: JSON.parse(await readFile(failureOutput, "utf8")) };
  }

  const failedDispose = await runFailureMode("batching", ["--fail-coordinator-dispose"]);
  assert.equal(failedDispose.code, 2);
  assert.ok(failedDispose.summary.counters.cleanup_failures >= 1);

  const failedRecovery = await runFailureMode("cancel-restart", ["--fail-recovery-search"]);
  assert.equal(failedRecovery.code, 2);
  assert.equal(failedRecovery.summary.counters.cancellations, 0);
  assert.ok(failedRecovery.summary.counters.workload_failures >= 1);

  if (process.env.SPIKE_REPLACEMENT_MODEL_FILE && process.env.SPIKE_REPLACEMENT_MODEL_SHA256) {
    const replaced = await runMode("model-replacement", [
      "--cache",
      "--replacement-model-file",
      process.env.SPIKE_REPLACEMENT_MODEL_FILE,
      "--replacement-model-sha256",
      process.env.SPIKE_REPLACEMENT_MODEL_SHA256,
    ]);
    assert.ok(replaced.counters.model_replacements >= 1);
    assert.ok(replaced.model_transitions.length >= 1);
    assert.notEqual(replaced.model_transitions[0].from, replaced.model_transitions[0].to);
    assert.equal(replaced.counters.cache_invalidation_failures, 0);
  }
}
