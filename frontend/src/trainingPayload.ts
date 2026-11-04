import { ApiError } from './api';
import { trainingNumberFields } from './TrainingFields';
import { targetRequest } from './TrainingTarget';
import type { TrainingConfig } from './trainingPresets';

/**
 * The exact request bodies the workshop posts to the training API. They live
 * here, away from the components, so the payload contract test can build every
 * shape a user can submit and compare it with the backend's accepted fields.
 */

/**
 * A request the form could not build, named by the field the user must fix. The
 * paths use the same vocabulary as the API, so one error pipeline handles both.
 */
export class TrainingInputError extends Error {
  constructor(
    /** Full request paths, exactly as the server would report them. */
    public readonly fields: string[],
    message: string,
  ) {
    super(message);
    this.name = 'TrainingInputError';
  }
}

/**
 * Field paths a request was rejected for, kept complete. `seed` (the Optuna study
 * seed) and `training.seed` (the model seed) are different settings, so collapsing
 * the nested prefix would mark and clear the wrong input.
 */
export function rejectedTrainingFields(error: unknown): string[] {
  const fields =
    error instanceof ApiError
      ? error.fields
      : error instanceof TrainingInputError
        ? error.fields
        : undefined;
  if (!fields?.length) return [];
  return [...new Set(fields)].sort();
}

/** Study controls that share their names with model settings. */
export const STUDY_FIELDS = ['trials', 'study_name', 'seed', 'search_space'];

/**
 * The input identity a rejected path refers to. A tuning job nests its training
 * configuration, so `training.channels` belongs to the form's `channels`; a bare
 * `seed` there is the study seed, which the form edits as `study.seed`.
 */
export function fieldIdentity(path: string, kind: 'train' | 'tune'): string {
  if (path.startsWith('training.')) return path.slice('training.'.length);
  if (kind === 'tune' && STUDY_FIELDS.includes(path)) return `study.${path}`;
  return path;
}

/** Parse the tuning search space only when a tuning job actually needs it. */
export function parseStudySpace(space: string): unknown {
  try {
    return JSON.parse(space);
  } catch {
    throw new TrainingInputError(['search_space'], 'The parameter search space is not valid JSON.');
  }
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
  'evaluation_mode',
  'evaluation_inference_backend',
  'device',
  'mixed_precision',
  'save_all_best',
  'self_play_mode',
  'inference_backend',
  'inference_cache_mode',
  'inference_device',
  'training_symmetry_mode',
  'adaptation',
  'adaptation_lr_candidates',
];

export interface StudyInput {
  trials: number;
  studyName: string;
  studySeed: number;
  /** Raw JSON text of the search space; parsed only for a tuning job. */
  space: string;
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
      search_space: parseStudySpace(study.space),
      training: config,
      ...(study.resumeStudy ? { resume_study: study.resumeStudy } : {}),
    };
  }
  // Ordinary training never reads the tuning search space, so malformed tuning
  // JSON can never prevent a valid model-training job from starting.
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
