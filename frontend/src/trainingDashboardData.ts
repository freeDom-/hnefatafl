import type { Job, Model } from './types';
import type { EvaluationRecord } from './TrainingEvaluationStatus';

export type Values = Record<string, unknown>;
export const object = (value: unknown): Values | undefined =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Values)
    : undefined;
export const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);
export const activeJob = (job: Job) =>
  ['running', 'pending', 'queued', 'cancelling'].includes(job.status);

// The server's without_live_timing() deliberately omits these between tuning trials.
const trialLiveFields = new Set([
  'stage',
  'phase_started_at',
  'phase_elapsed',
  'iteration_started_at',
  'iteration_elapsed',
  'active_iteration',
  'phase_progress',
  'duration',
  'updated_at',
  'last_progress_at',
  'last_progress_seconds',
]);

// Match the backend's same_run(): null and missing both mean a legacy run;
// one-sided missing identifiers cannot establish a shared execution identity.
const sameRun = (previous: Values, next: Values) => (previous.run ?? null) === (next.run ?? null);
const trialNumberChanged = (previous?: Values, next?: Values) =>
  previous?.trial != null && next?.trial != null && previous.trial !== next.trial;
const trialReportChanged = (previous: Values | undefined, next: Values, parentChanged: boolean) =>
  !previous ||
  !sameRun(previous, next) ||
  trialNumberChanged(previous, next) ||
  (parentChanged && previous.run == null && next.run == null);

/** Compare report ownership without copying large loaded metadata maps. */
export function jobReportIdentityChanged(previous: Job, next: Job): boolean {
  const old = object(previous.progress),
    update = object(next.progress);
  if (!update) return false;
  const tuning = (next.kind ?? previous.kind) === 'tune';
  if (!tuning && !sameRun(old ?? {}, update)) return true;
  const trial = object(update.trial_progress);
  return (
    !!trial &&
    trialReportChanged(
      object(old?.trial_progress),
      trial,
      tuning && trialNumberChanged(old, update),
    )
  );
}
interface SummaryMerge {
  live: boolean;
  tuning: boolean;
  incomplete: boolean;
  trialChanged: boolean;
}

/** Overlay partial reports without copying history or crossing identity boundaries. */
function mergeSummaryValue(
  previous: unknown,
  next: unknown,
  context: SummaryMerge,
  key?: string,
): unknown {
  // Heartbeats publish all counters for the current phase, including an empty map.
  if (context.live && key === 'phase_progress') return next;
  const old = object(previous);
  const update = object(next);
  // Tuning's outer run follows its active trial; only its child owns trial
  // history. The study's cumulative history remains at the outer level.
  if (
    update &&
    ((key === 'trial_progress' && trialReportChanged(old, update, context.trialChanged)) ||
      (key === 'progress' && !context.tuning && !sameRun(old ?? {}, update)))
  ) {
    context.incomplete = true;
    return next;
  }
  if (old && update) {
    // A new model ID must never acquire another model's presentation/path data.
    if (
      ('id' in update && old.id !== update.id) ||
      (key === 'model' &&
        (typeof old.id !== 'string' || !old.id || typeof update.id !== 'string' || !update.id))
    )
      return next;
    const retained =
      context.live && key === 'trial_progress'
        ? Object.fromEntries(
            Object.entries(old).filter(([field]) => !trialLiveFields.has(field) || field in update),
          )
        : old;
    return {
      ...retained,
      ...Object.fromEntries(
        Object.entries(update).map(([field, value]) => [
          field,
          mergeSummaryValue(old[field], value, context, field),
        ]),
      ),
    };
  }
  if (Array.isArray(previous)) {
    if (key === 'best_models' && Array.isArray(next)) {
      const pending = new Map(
        next
          .filter((entry) => typeof object(entry)?.model_id === 'string')
          .map((entry) => [object(entry)?.model_id, entry]),
      );
      const retained = previous.map((entry) => {
        const id = object(entry)?.model_id;
        const update = pending.get(id);
        pending.delete(id);
        return update ? mergeSummaryValue(entry, update, context) : entry;
      });
      return [...retained, ...pending.values()];
    }
    return previous;
  }
  return next;
}

export function mergeJobSummary(previous: Job | null, next: Job): Job {
  if (!next.summary || previous?.id !== next.id) return next;
  const context = {
    live: activeJob(previous) || activeJob(next),
    tuning: (next.kind ?? previous.kind) === 'tune',
    incomplete: previous.summary === true,
    trialChanged: false,
  };
  const oldProgress = object(previous.progress);
  const progress = object(next.progress);
  context.trialChanged = context.tuning && trialNumberChanged(oldProgress, progress);
  // A replacement execution cannot use reports/logs owned by the former run.
  const retained =
    !context.tuning && progress && !sameRun(oldProgress ?? {}, progress)
      ? Object.fromEntries(
          Object.entries(previous).filter(([key]) => !['metrics', 'result', 'logs'].includes(key)),
        )
      : previous;
  const merged = mergeSummaryValue(retained, next, context) as Job;
  return {
    ...merged,
    summary: context.incomplete,
  };
}
export const jobName = (job: Job) =>
  String(
    // An explicit rename wins over derived configuration names; job IDs and
    // training parameters are never rewritten by renaming.
    job.name ??
      (job.kind === 'tune' ? job.config?.study_name : undefined) ??
      job.config?.model_name ??
      object(job.config?.training)?.model_name ??
      job.ruleset ??
      job.config?.ruleset ??
      job.id.slice(0, 8),
  );
export const jobRuleset = (job: Job) =>
  String(job.ruleset ?? job.config?.ruleset ?? object(job.config?.training)?.ruleset ?? '—');
export function sortJobs(jobs: Job[], sort = 'active') {
  return [...jobs].sort(
    (a, b) =>
      (sort === 'active' ? Number(activeJob(b)) - Number(activeJob(a)) : 0) ||
      (sort === 'oldest' ? 1 : -1) *
        (Date.parse(a.created_at ?? '') - Date.parse(b.created_at ?? '') || 0) ||
      a.id.localeCompare(b.id),
  );
}
export function filterJobs(
  jobs: Job[],
  query: string,
  status: string,
  sort: string,
  models: Model[] = [],
) {
  const search = query.trim().toLocaleLowerCase();
  return sortJobs(
    jobs.filter(
      (job) =>
        (status === 'all' || (status === 'active' ? activeJob(job) : job.status === status)) &&
        (!search ||
          [
            jobName(job),
            job.id,
            jobRuleset(job),
            ...models
              .filter(
                (model) =>
                  model.id === job.model_id ||
                  (Array.isArray(job.progress?.best_models) &&
                    job.progress.best_models.some((best) => best?.model_id === model.id)),
              )
              .map((model) => model.name),
          ]
            .join(' ')
            .toLocaleLowerCase()
            .includes(search)),
    ),
    sort,
  );
}
export interface HistoryPoint extends Values {
  iteration: number;
  policy_loss?: number;
  value_loss?: number;
  evaluation_score?: number;
  evaluation_truncated?: number;
  evaluation_draws?: number;
  evaluation_opponent?: string;
  opponent_identity?: string;
  evaluation?: EvaluationRecord;
  attacker_score?: number;
  defender_score?: number;
}
export function trainingHistory(value: unknown): HistoryPoint[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((row): row is HistoryPoint => !!object(row) && finite(row.iteration))
    .map((row) => ({
      ...row,
      evaluation_score: finite(row.evaluation?.score) ? row.evaluation.score : row.evaluation_score,
      attacker_score: row.evaluation?.candidate_as_attacker?.score,
      defender_score: row.evaluation?.candidate_as_defender?.score,
    }))
    .sort((a, b) => a.iteration - b.iteration);
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return JSON.stringify(value.map(canonical));
  const record = object(value);
  return record
    ? JSON.stringify(
        Object.keys(record)
          .sort()
          .map((key) => [key, canonical(record[key])]),
      )
    : (JSON.stringify(value) ?? '');
}
/** Effective context and incumbent remain guards even on imported/legacy records. */
export const evaluationIdentity = (row: HistoryPoint) =>
  canonical([
    row.opponent_identity ?? row.evaluation?.opponent_identity ?? 'legacy',
    row.evaluation?.opponent ?? row.evaluation_opponent ?? 'shield_bearer',
    row.evaluation?.context ?? row.evaluation?.opponent_config ?? null,
    row.evaluation?.champion_iteration ?? null,
  ]);
export function evaluationGroups(rows: HistoryPoint[]) {
  const groups = new Map<string, HistoryPoint[]>();
  for (const row of rows) {
    if (!finite(row.evaluation_score)) continue;
    const key = evaluationIdentity(row);
    const points = groups.get(key);
    if (points) points.push(row);
    else groups.set(key, [row]);
  }
  return [...groups].map(([identity, points]) => ({ identity, points }));
}
/** Most recent valid observation, without changing group order or series labels. */
export function latestEvaluationGroup(groups: ReturnType<typeof evaluationGroups>) {
  let latest: (typeof groups)[number] | undefined;
  let latestIteration = -Infinity;
  for (const group of groups) {
    for (const point of group.points) {
      if (
        finite(point.iteration) &&
        finite(point.evaluation_score) &&
        point.iteration > latestIteration
      ) {
        latest = group;
        latestIteration = point.iteration;
      }
    }
  }
  return latest;
}
/** The job's own snapshot; a nested tuning trial never replaces its fields. */
export const jobLevelProgress = (job: Job): Values =>
  object(job.progress) ?? object(job.metrics) ?? {};

/** The active tuning trial's snapshot, when a tuning job nests one. */
export const trialProgress = (job: Job): Values | undefined =>
  object(jobLevelProgress(job).trial_progress);

/**
 * Job progress for the training views. A tuning job nests the active trial's
 * snapshot, whose phase, iteration and counters belong to that trial; the job's
 * own elapsed time must be read with `jobLevelProgress` instead, because the
 * trial's duration restarts with every trial.
 */
export const jobProgress = (job: Job) => {
  const saved = jobLevelProgress(job);
  const trial = object(saved.trial_progress);
  return trial ? { ...saved, ...trial } : saved;
};
export const latestMetrics = (progress: Values) => object(progress.metrics) ?? progress;
/** A snapshot with no recorded fields is not usable and must not mask later sources. */
const usablePerformance = (value: unknown): Values | undefined => {
  const performance = object(value);
  return performance && Object.keys(performance).length ? performance : undefined;
};
export function latestPerformance(progress: Values, history: HistoryPoint[]) {
  const current =
    usablePerformance(progress.performance) ??
    usablePerformance(latestMetrics(progress).performance);
  if (current) return current;
  // An interrupted or sparse log can end without a performance snapshot; fall
  // back to the most recent snapshot that actually recorded something so earlier
  // observations stay visible. The backward scan stops at the first hit.
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const performance = usablePerformance(history[index].performance);
    if (performance) return performance;
  }
  return undefined;
}
/**
 * Selectable iteration windows for the shared analysis range control. One
 * window is applied to every chart in the Learning, Evaluation and Performance
 * tabs, so the options live here instead of being repeated per view.
 */
export const iterationRanges = [10, 25, 50, 100, 250] as const;

/**
 * Rows whose iteration falls inside the latest `range` iterations ending at
 * `anchor`. The explicit anchor lets sparse logs (such as automatic
 * hyperparameter decisions) share the exact window of the training history
 * instead of being anchored to their own last entry. A missing or non-finite
 * anchor cannot define a window, so the rows are returned unchanged.
 */
export const withinIterationWindow = <T extends { iteration: number }>(
  rows: T[],
  range: number,
  anchor: number | undefined,
) => {
  if (!range || anchor === undefined || !Number.isFinite(anchor)) return rows;
  const windowStart = anchor - range + 1;
  return rows.filter((row) => row.iteration >= windowStart && row.iteration <= anchor);
};

/**
 * Latest `range` iterations of a chronological history. The window is anchored
 * to the last recorded iteration number rather than the array length, so a
 * resumed job with a nonzero start keeps its true span and sparse or gapped
 * histories are never back-filled with missing iterations. `rows` is already
 * sorted by iteration; filtering preserves that order.
 */
export const recentHistory = (rows: HistoryPoint[], range: number) => {
  if (!range || !rows.length) return rows;
  return withinIterationWindow(rows, range, rows.at(-1)!.iteration);
};
/** Diagnostic views must never expose a future credential added to a public payload. */
export function safeDiagnostics(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(safeDiagnostics);
  const record = object(value);
  if (!record) return value;
  return Object.fromEntries(
    Object.entries(record).map(([key, entry]) => [
      key,
      /token|secret|password|credential|authorization/i.test(key)
        ? '[redacted]'
        : safeDiagnostics(entry),
    ]),
  );
}
