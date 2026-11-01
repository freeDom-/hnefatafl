import { ApiError } from './api';
import { trainingNumberFields } from './TrainingFields';
import { targetRequest } from './TrainingTarget';
import type { TrainingConfig } from './trainingPresets';

/**
 * The exact request bodies the workshop posts to the training API. They live
 * here, away from the components, so the payload contract test can build every
 * shape a user can submit and compare it with the backend's accepted fields.
 */

/** The form edits a tuning job's nested configuration, so its inputs are unprefixed. */
export function rejectedTrainingFields(error: unknown): string[] {
  if (!(error instanceof ApiError) || !error.fields?.length) return [];
  const names = error.fields.map((field) =>
    field.startsWith('training.') ? field.slice('training.'.length) : field,
  );
  return [...new Set(names)].sort();
}

/** Execution target fields, removed by the server before training validation. */
export const EXECUTION_FIELDS = ['execution', 'worker_id'];

/** Study controls a tuning job accepts beside its nested `training` configuration. */
export const TUNE_FIELDS = [
  'trials',
  'study_name',
  'seed',
  'search_space',
  'training',
  'resume_study',
];

/** Checkpoint identity a resume may not change, plus the explicit new target. */
export const RESUME_IMMUTABLE_FIELDS = [
  'ruleset',
  'channels',
  'blocks',
  'seed',
  'optimizer',
  'iterations',
];

/** Resume-editable settings that are not numeric training fields. */
const RESUME_TEXT_FIELDS = [
  'model_name',
  'evaluation_opponent',
  'device',
  'mixed_precision',
  'save_all_best',
  'self_play_mode',
  'inference_backend',
  'inference_cache_mode',
  'inference_device',
  'training_symmetry_mode',
];

export interface StudyInput {
  trials: number;
  studyName: string;
  studySeed: number;
  search: unknown;
  /** Set when a saved study is continued instead of started. */
  resumeStudy?: string;
}

export function launchPayload(
  kind: 'train' | 'tune',
  config: TrainingConfig,
  target: string,
  study: StudyInput,
): Record<string, unknown> {
  if (kind === 'tune') {
    return {
      kind,
      trials: study.trials,
      study_name: study.studyName,
      seed: study.studySeed,
      search_space: study.search,
      training: config,
      ...(study.resumeStudy ? { resume_study: study.resumeStudy } : {}),
    };
  }
  return { ...config, kind, ...(target !== 'local' ? targetRequest(target) : {}) };
}

/** Only changed, resume-editable settings; checkpoint identity never leaves the server. */
export function resumeOverrides(config: TrainingConfig, saved: TrainingConfig): TrainingConfig {
  const overrides: TrainingConfig = {};
  for (const [key, value] of Object.entries(config)) {
    if (RESUME_IMMUTABLE_FIELDS.includes(key)) continue;
    if (!(key in trainingNumberFields) && !RESUME_TEXT_FIELDS.includes(key)) continue;
    if (value === saved[key]) continue;
    overrides[key] = value;
  }
  return overrides;
}

export function resumePayload(
  config: TrainingConfig,
  saved: TrainingConfig,
  iterations: number,
  executionTarget: string,
  previousTarget: string,
): Record<string, unknown> {
  return {
    iterations,
    ...resumeOverrides(config, saved),
    ...(executionTarget !== previousTarget ? targetRequest(executionTarget) : {}),
  };
}
