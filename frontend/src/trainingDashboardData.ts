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
export function mergeJobSummary(previous: Job | null, next: Job): Job {
  if (!next.summary || previous?.id !== next.id) return next;
  const oldProgress = object(previous.progress) ?? {};
  const progress = object(next.progress) ?? {};
  return {
    ...previous,
    ...next,
    progress: {
      ...oldProgress,
      ...progress,
      metrics: { ...object(oldProgress.metrics), ...object(progress.metrics) },
    },
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
export const jobProgress = (job: Job) => {
  const saved = object(job.progress) ?? object(job.metrics) ?? {};
  const trial = object(saved.trial_progress);
  return trial ? { ...saved, ...trial } : saved;
};
export const latestMetrics = (progress: Values) => object(progress.metrics) ?? progress;
export function latestPerformance(progress: Values, history: HistoryPoint[]) {
  return (
    object(progress.performance) ??
    object(latestMetrics(progress).performance) ??
    object(history.at(-1)?.performance)
  );
}
export const recentHistory = (rows: HistoryPoint[], range: number) => {
  if (!range || !rows.length) return rows;
  const first = rows.at(-1)!.iteration - range + 1;
  return rows.filter((row) => row.iteration >= first);
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
