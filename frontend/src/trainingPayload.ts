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
  'source_checkpoint',
  'opponents',
  'verification',
  'sampler',
  'pruner',
  'max_failures',
];

/** Controller settings that are inert while adaptation is off. */
export const ADAPTATION_SETTINGS = [
  'adaptation_lr_candidates',
  'adaptation_cadence',
  'adaptation_warmup',
  'adaptation_min_dwell',
  'adaptation_lag',
  'adaptation_min_trials',
];

/** Checkpoint identity a resume may not change, plus the explicit new target. */
export const RESUME_IMMUTABLE_FIELDS = [
  'ruleset',
  'channels',
  'blocks',
  'seed',
  'optimizer',
  'schedulefree_warmup_steps',
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
  /** Optional JSON text for opponents, verification, sampler, pruner and failures. */
  strategy?: string;
}

/** Parse the optional advanced study settings; empty text means no overrides. */
/** Optional study settings the tuning form may forward, with no overrides. */
export const STUDY_STRATEGY_FIELDS = [
  'opponents',
  'verification',
  'sampler',
  'pruner',
  'max_failures',
] as const;

export function parseStudyStrategy(strategy: string | undefined): Record<string, unknown> {
  if (!strategy?.trim()) return {};
  let value: unknown;
  try {
    value = JSON.parse(strategy);
  } catch {
    throw new TrainingInputError(['strategy'], 'The optional study settings are not valid JSON.');
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TrainingInputError(
      ['strategy'],
      'The optional study settings must be a JSON object.',
    );
  }
  // Only the documented strategy fields are accepted, so the text can never
  // override the authoritative study controls (search space, training config,
  // study identity) or smuggle in an unknown top-level request field.
  const unknown = Object.keys(value).filter(
    (key) => !(STUDY_STRATEGY_FIELDS as readonly string[]).includes(key),
  );
  if (unknown.length) {
    throw new TrainingInputError(
      ['strategy'],
      `The optional study settings contain unsupported fields: ${unknown.sort().join(', ')}.`,
    );
  }
  return value as Record<string, unknown>;
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
      ...parseStudyStrategy(study.strategy),
      ...(study.resumeStudy ? { resume_study: study.resumeStudy } : {}),
    };
  }
  // Ordinary training never reads the tuning search space, so malformed tuning
  // JSON can never prevent a valid model-training job from starting.
  return { ...config, kind, ...(target !== 'local' ? targetRequest(target) : {}) };
}

/**
 * Preserve a tuning study's full configuration when resuming it.
 *
 * The checkpoint source (`training.resume`), opponent, search space and strategy
 * must survive so a resumed study does not silently restart from fresh weights
 * or change its objective. The server merges and validates this against the
 * stored study identity.
 */
export function tuneResumePayload(
  jobId: string,
  saved: Record<string, unknown>,
  trials: number,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    ...Object.fromEntries(
      Object.entries(saved).filter(
        ([key]) =>
          ![
            'output_dir',
            'registry_dir',
            'storage',
            'execution',
            'worker_id',
            'kind',
            'max_failures',
          ].includes(key),
      ),
    ),
    kind: 'tune',
    trials,
    resume_study: jobId,
  };
  if (saved.training && typeof saved.training === 'object' && !Array.isArray(saved.training)) {
    payload.training = Object.fromEntries(
      Object.entries(saved.training).filter(
        ([key]) => !['output_dir', 'registry_dir'].includes(key),
      ),
    );
  }
  if (typeof saved.max_failures === 'number') {
    payload.max_failures = Math.min(saved.max_failures, trials);
  }
  return payload;
}

/** Only changed, resume-editable settings; checkpoint identity never leaves the server. */
export function resumeOverrides(config: TrainingConfig, saved: TrainingConfig): TrainingConfig {
  const overrides: TrainingConfig = {};
  const adaptationActive = config.adaptation === 'hypercontroller';
  for (const [key, value] of Object.entries(config)) {
    if (RESUME_IMMUTABLE_FIELDS.includes(key)) continue;
    if (!(key in trainingNumberFields) && !RESUME_TEXT_FIELDS.includes(key)) continue;
    // Inactive controller settings are inert: sending a generated display list
    // when the controller is off would be an unnecessary override. The mode
    // itself is always compared so disabling it still reaches the server.
    if (!adaptationActive && ADAPTATION_SETTINGS.includes(key)) continue;
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
