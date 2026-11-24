// Pure selection and work-accounting helpers for sustained_workload.mjs.
// Kept separate so deterministic position/work accounting can be tested without
// launching a browser.

export const ANALYSIS_KINDS = ["opening", "midgame", "tactical", "endgame", "repetition"];

export function selectAnalysisPositions(entries, ruleset) {
  if (!Array.isArray(entries) || !ruleset) throw new Error("invalid positions or ruleset");
  const selected = [];
  const seen = new Set();
  for (const entry of entries) {
    if (!entry || entry.ruleset !== ruleset) continue;
    if (!ANALYSIS_KINDS.includes(entry.kind)) continue;
    if (!entry.state || entry.state.ruleset !== ruleset) continue;
    if (entry.state.winner !== null) continue;
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
    model_replacements: 0,
    cache_clears: 0,
    cache_invalidation_failures: 0,
    batches: 0,
    observed_max_batch: 0,
    batch_rounds: 0,
    batch_elapsed_ms_total: 0,
    batch_max_elapsed_ms: 0,
  };
}

export function recordSuccess(counters, outcome) {
  const search = outcome?.search ?? {};
  const inferences = Number(search.coordinator?.inferences ?? 0);
  const completed = Number(search.completed_simulations ?? 0);
  if (!Number.isInteger(completed) || completed < 0) throw new Error("invalid completed work");
  counters.completed_requests += 1;
  counters.successful_searches += 1;
  counters.completed_simulations += completed;
  counters.neural_inferences += Number.isInteger(inferences) ? inferences : 0;
  if (inferences > 0) counters.neural_searches += 1;
  else counters.no_inference_shortcuts += 1;
  return { completed, inferences, reason: search.completion_reason ?? null };
}

export function recordFailure(counters) {
  counters.workload_failures += 1;
}

export function recordBatch(counters, batching, elapsedMs = null) {
  if (!batching) return;
  if (Number.isInteger(batching.batches)) counters.batches += batching.batches;
  if (Number.isInteger(batching.observed_max_batch))
    counters.observed_max_batch = Math.max(
      counters.observed_max_batch,
      batching.observed_max_batch,
    );
  counters.batch_rounds += 1;
  if (Number.isFinite(elapsedMs)) {
    counters.batch_elapsed_ms_total += elapsedMs;
    counters.batch_max_elapsed_ms = Math.max(counters.batch_max_elapsed_ms, elapsedMs);
  }
}

export function telemetryRecord(counters, identity, extra = {}) {
  return {
    timestamp_ms: Date.now(),
    session_id: identity.session,
    model_sha256: identity.modelSha256,
    workload: identity.workload,
    ...counters,
    events: {
      cancellations: counters.cancellations,
      reloads: counters.reloads,
      worker_replacements: counters.worker_replacements,
      model_replacements: counters.model_replacements,
      cache_clears: counters.cache_clears,
      batches: counters.batches,
    },
    ...extra,
  };
}

export function exitCode(counters) {
  if (counters.completed_requests > 0 && counters.workload_failures === 0) return 0;
  if (counters.completed_requests > 0) return 2;
  return 1;
}
