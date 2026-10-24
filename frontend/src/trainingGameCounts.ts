import type { Job, Model } from './types';
import { object } from './trainingDashboardData';

export const recordedGameCount = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;

/** Legacy counters are cumulative; a resumed job without its baseline has no known local count. */
export function jobGameCounts(job: Job): { job?: number; cumulative?: number } {
  // trial_progress describes only the current trial, never the whole study.
  if (job.kind === 'tune') return {};
  const progress = { ...job.result, ...job.progress };
  const stats = object(progress.self_play_games);
  if (stats)
    return stats.version === 1
      ? { job: recordedGameCount(stats.job), cumulative: recordedGameCount(stats.cumulative) }
      : {};
  const cumulative = recordedGameCount(progress.games_generated);
  const resumed =
    job.config?.resume || job.config?.resume_job || job.config?.resume_study || 'provenance' in job;
  return { job: resumed ? undefined : cumulative, cumulative };
}

export function modelGameCount(model: Model): number | undefined {
  return model.self_play_games?.version === 1
    ? recordedGameCount(model.self_play_games.cumulative)
    : undefined;
}
