// Pure selection and work-accounting helpers for sustained_workload.mjs.

export const ANALYSIS_KINDS = ["opening", "midgame", "tactical", "endgame", "repetition"];
export const COMPLETION_REASONS = [
  "simulations",
  "deadline",
  "immediate_win",
  "terminal",
  "insufficient_budget",
  "completed",
];

export function selectAnalysisPositions(entries, ruleset) {
  if (!Array.isArray(entries) || !ruleset) throw new Error("invalid positions or ruleset");
  const selected = [];
  const seen = new Set();
  for (const entry of entries) {
    if (!entry || entry.ruleset !== ruleset) continue;
    if (!ANALYSIS_KINDS.includes(entry.kind)) continue;
    if (!entry.state || entry.state.ruleset !== ruleset || entry.state.winner !== null) continue;
    const key = JSON.stringify(entry.state);
    if (seen.has(key)) continue;
    seen.add(key);
    selected.push({ name: entry.name, kind: entry.kind, ruleset, state: entry.state });
  }
  return selected;
}

export function newCounters() {
  return {
    completed_requests: 0,
    workload_failures: 0,
    successful_searches: 0,
    neural_searches: 0,
    no_inference_shortcuts: 0,
    completed_simulations: 0,
    neural_inferences: 0,
    cancellations: 0,
    reloads: 0,
    worker_replacements: 0,
    client_disposals: 0,
    model_replacements: 0,
    cache_clears: 0,
    cache_evictions: 0,
    cache_invalidation_failures: 0,
    batches: 0,
    observed_max_batch: 0,
    batch_rounds: 0,
    batch_successful_rounds: 0,
    batch_counter_resets: 0,
    batch_elapsed_ms_total: 0,
    batch_max_elapsed_ms: 0,
    batch_search_elapsed_ms_total: 0,
    batch_search_max_elapsed_ms: 0,
    peak_search_workers: 0,
    peak_inference_workers: 0,
    peak_total_workers: 0,
  };
}

function _integer(value, label) {
  if (!Number.isInteger(value) || value < 0) throw new Error(`${label} must be a non-negative integer`);
  return value;
}

export function recordSuccess(counters, outcome) {
  if (!outcome || typeof outcome !== "object") throw new Error("search outcome is missing");
  const search = outcome.search;
  if (!search || typeof search !== "object") throw new Error("search result is missing");
  const move = outcome.best_move;
  if (!move || !Number.isInteger(move.source) || !Number.isInteger(move.target))
    throw new Error("search result lacks a legal-shaped best move");
  const completed = _integer(search.completed_simulations, "completed_simulations");
  const inferences = _integer(search.coordinator?.inferences, "neural inference count");
  const reason = search.completion_reason;
  if (!COMPLETION_REASONS.includes(reason)) throw new Error("invalid completion reason");
  const settings = outcome.settings;
  if (!settings || !Number.isInteger(settings.iterations) || settings.iterations < 1)
    throw new Error("effective search settings are missing");
  if (!Number.isFinite(settings.time_limit) || settings.time_limit <= 0)
    throw new Error("effective time limit is invalid");
  if (completed > settings.iterations) throw new Error("completed work exceeds the simulation budget");
  if (reason === "simulations" && completed !== settings.iterations)
    throw new Error("simulation completion contradicts completed work");
  if (reason === "deadline" && completed > settings.iterations)
    throw new Error("deadline completion exceeds the simulation ceiling");
  const provider = search.provider ?? null;
  if (inferences > 0) {
    if (!provider || typeof provider !== "object")
      throw new Error("neural search lacks a per-search provider observation");
    if (!["wasm", "webgpu-session-unverified", "webgpu-mixed", "webgpu-session-verified"].includes(provider.execution_placement))
      throw new Error("neural search observed an unsupported execution placement");
    if (provider.requested === "webgpu" && ["wasm", "cpu-fallback"].includes(provider.execution_placement))
      throw new Error("WebGPU search silently fell back");
    if (provider.gpu_execution_verified === true && provider.execution_placement !== "webgpu-session-verified")
      throw new Error("unverified WebGPU cannot claim verified GPU execution");
  } else if (provider !== null) {
    throw new Error("provider observation was reported without neural inference");
  }
  const shortcut = inferences === 0 && ["immediate_win", "terminal"].includes(reason);
  if (inferences === 0 && !shortcut)
    throw new Error("search completed without neural inference or a valid shortcut");
  counters.completed_requests += 1;
  counters.successful_searches += 1;
  counters.completed_simulations += completed;
  counters.neural_inferences += inferences;
  if (inferences > 0) counters.neural_searches += 1;
  else counters.no_inference_shortcuts += 1;
  return { completed, inferences, reason, provider, shortcut };
}

export function recordFailure(counters) {
  counters.workload_failures += 1;
}

export function recordBatch(counters, cumulative, previous, elapsedMs) {
  if (!cumulative || typeof cumulative !== "object")
    throw new Error("batch statistics are missing");
  const batches = _integer(cumulative.batches, "batch count");
  const maxBatch = _integer(cumulative.observed_max_batch, "observed max batch");
  const prior = previous && Number.isInteger(previous.batches) ? previous.batches : 0;
  let delta = batches - prior;
  if (delta < 0) {
    counters.batch_counter_resets += 1;
    delta = batches;
  }
  counters.batches += delta;
  counters.observed_max_batch = Math.max(counters.observed_max_batch, maxBatch);
  counters.batch_rounds += 1;
  if (Number.isFinite(elapsedMs)) {
    counters.batch_elapsed_ms_total += elapsedMs;
    counters.batch_max_elapsed_ms = Math.max(counters.batch_max_elapsed_ms, elapsedMs);
  }
  return { batches, observed_max_batch: maxBatch };
}

export function recordBatchSuccess(counters, searchElapsedMs) {
  counters.batch_successful_rounds += 1;
  if (Number.isFinite(searchElapsedMs)) {
    counters.batch_search_elapsed_ms_total += searchElapsedMs;
    counters.batch_search_max_elapsed_ms = Math.max(
      counters.batch_search_max_elapsed_ms,
      searchElapsedMs,
    );
  }
}

export function recordWorkers(counters, workers) {
  if (!workers || typeof workers !== "object") return;
  const searchWorkers = Number.isInteger(workers.search_workers) ? workers.search_workers : 0;
  const inferenceWorkers = Number.isInteger(workers.inference_workers)
    ? workers.inference_workers
    : 0;
  counters.peak_search_workers = Math.max(counters.peak_search_workers, searchWorkers);
  counters.peak_inference_workers = Math.max(
    counters.peak_inference_workers,
    inferenceWorkers,
  );
  counters.peak_total_workers = Math.max(
    counters.peak_total_workers,
    searchWorkers + inferenceWorkers,
  );
}

export function telemetryRecord(counters, identity, extra = {}) {
  return {
    timestamp_ms: Date.now(),
    session_id: identity.session,
    model_sha256: identity.modelSha256,
    workload: identity.workload,
    ...counters,
    ...extra,
    events: {
      cancellations: counters.cancellations,
      reloads: counters.reloads,
      worker_replacements: counters.worker_replacements,
      client_disposals: counters.client_disposals,
      model_replacements: counters.model_replacements,
      cache_clears: counters.cache_clears,
      cache_evictions: counters.cache_evictions,
      cache_invalidation_failures: counters.cache_invalidation_failures,
      batches: counters.batches,
      batch_successful_rounds: counters.batch_successful_rounds,
    },
  };
}

export function exitCode(counters) {
  if (
    counters.completed_requests > 0 &&
    counters.workload_failures === 0 &&
    counters.cache_invalidation_failures === 0
  )
    return 0;
  if (counters.completed_requests > 0) return 2;
  return 1;
}
