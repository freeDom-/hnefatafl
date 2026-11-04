import { expect, it } from 'vitest';
import shippedTuneConfig from '../../configs/tune-smoke.json';
import shippedTrainConfig from '../../configs/train-beginner.json';
import contract from './trainingContract.json';
import {
  EXECUTION_FIELDS,
  RESUME_IMMUTABLE_FIELDS,
  STUDY_FIELDS,
  TUNE_FIELDS,
  TrainingInputError,
  fieldIdentity,
  launchPayload,
  parseStudySpace,
  rejectedTrainingFields,
  resumeOverrides,
  tuneResumePayload,
  type StudyInput,
} from './trainingPayload';
import { parseImportedConfig } from './trainingConfigImport';
import {
  changeTrainingPlan,
  isTrainingParameter,
  recordedTrainingParameters,
  trainingConfig,
  validTrainingInput,
  type TrainingConfig,
} from './trainingPresets';
import { recordedJobParameters } from './trainingReuse';
import type { Job } from './types';
import { ApiError } from './api';

/**
 * The workshop and the training API share one field vocabulary
 * (`trainingContract.json`). The backend asserts the same file against its own
 * allowlist, so any drift between the two sides fails one of the two tests
 * instead of only surfacing as "Unsupported training configuration field.".
 */

const RULESETS = ['brandubh', 'tablut', 'copenhagen'];
const PRESETS = ['recommended', 'smoke', 'beginner', 'balanced'] as const;
const TRAINING_FIELDS = new Set<string>(contract.training_fields);
const SEARCH_SPACE = {
  learning_rate: { type: 'float', low: 0.0001, high: 0.003, log: true },
};
const study: StudyInput = {
  trials: 3,
  studyName: 'hnefatafl-search',
  studySeed: 0,
  space: JSON.stringify(SEARCH_SPACE),
};

/** Every key a request may carry, including the server-removed controls. */
const REQUEST_FIELDS = new Set<string>([
  ...contract.training_fields,
  ...contract.tune_fields,
  ...contract.execution_fields,
  'kind',
]);

function unexpected(payload: Record<string, unknown>, allowed: Set<string>): string[] {
  return Object.keys(payload).filter((key) => !allowed.has(key));
}

function keysOf(payload: Record<string, unknown>): string[] {
  return Object.keys(payload).sort();
}

it('declares exactly the training parameters the form can edit', () => {
  const editable = Object.keys(trainingConfig('tablut', 'recommended')).sort();
  expect(keysOf({ ...trainingConfig('tablut', 'recommended') })).toEqual(editable);
  expect(editable).toEqual([...TRAINING_FIELDS].sort());
  for (const key of TRAINING_FIELDS) expect(isTrainingParameter(key)).toBe(true);
  // The payload module and the shared contract must agree on the study controls.
  expect([...TUNE_FIELDS]).toEqual(contract.tune_fields);
  expect([...EXECUTION_FIELDS]).toEqual(contract.execution_fields);
  expect([...RESUME_IMMUTABLE_FIELDS]).toEqual(contract.resume_immutable_fields);
});

it('builds an acceptable launch payload for every ruleset, preset and target', () => {
  for (const ruleset of RULESETS) {
    for (const preset of PRESETS) {
      const config = trainingConfig(ruleset, preset);
      expect(validTrainingInput('ruleset', ruleset)).toBe(true);
      for (const target of ['local', 'remote', 'worker:abcdef']) {
        const payload = launchPayload('train', config, target, study);
        expect(unexpected(payload, REQUEST_FIELDS)).toEqual([]);
        expect(payload.kind).toBe('train');
        expect(payload.preset).toBe(preset);
        expect(payload.ruleset).toBe(ruleset);
        expect(payload.execution).toBe(target === 'local' ? undefined : 'remote');
        expect(payload.worker_id).toBe(target.startsWith('worker:') ? 'abcdef' : undefined);
      }
    }
  }
});

it('keeps a changed plan and a reused configuration inside the contract', () => {
  const plan = changeTrainingPlan(trainingConfig('tablut', 'recommended'), 'brandubh', 'balanced');
  expect(unexpected(launchPayload('train', plan, 'local', study), REQUEST_FIELDS)).toEqual([]);

  const recorded = recordedTrainingParameters(
    trainingConfig('tablut', 'recommended'),
    {
      name: 'Reused raven',
      ruleset: 'brandubh',
      training_config: {
        ...trainingConfig('brandubh', 'smoke'),
        // Legacy/exported records carry runtime fields that are not parameters.
        output_dir: 'data/training-old',
        registry_dir: 'data/models',
        storage: 'sqlite:///study.sqlite3',
      },
    },
    { cuda_available: true, mps_available: false, inference_backends: ['pytorch'] },
  )!;
  expect(unexpected(recorded.config, TRAINING_FIELDS)).toEqual([]);
  expect(
    unexpected(launchPayload('train', recorded.config, 'local', study), REQUEST_FIELDS),
  ).toEqual([]);

  const job: Job = {
    id: 'saved',
    status: 'completed',
    kind: 'train',
    config: {
      ...trainingConfig('brandubh', 'smoke'),
      output_dir: 'data/training-old',
      registry_dir: 'data/models',
    },
  };
  const reused = recordedJobParameters(trainingConfig('tablut', 'recommended'), job)!;
  expect(unexpected(reused.config, TRAINING_FIELDS)).toEqual([]);
});

it('keeps an imported configuration inside the contract and reports ignored fields', () => {
  // The shipped CLI configs are real "previously working" standard configurations.
  const imported = parseImportedConfig(
    shippedTrainConfig as Record<string, unknown>,
    trainingConfig('tablut', 'recommended'),
    { mode: 'create' },
  );
  expect(unexpected(imported.config, TRAINING_FIELDS)).toEqual([]);
  // Server-owned outputs are reported, never silently merged into the request.
  expect(imported.ignored).toEqual(expect.arrayContaining(['output_dir', 'registry_dir']));
  expect(imported.config).not.toHaveProperty('output_dir');
  expect(
    unexpected(launchPayload('train', imported.config, 'local', study), REQUEST_FIELDS),
  ).toEqual([]);

  const tune = parseImportedConfig(
    shippedTuneConfig as Record<string, unknown>,
    trainingConfig('tablut', 'recommended'),
    { mode: 'create' },
  );
  expect(tune.kind).toBe('tune');
  expect(unexpected(tune.config, TRAINING_FIELDS)).toEqual([]);
  expect(tune.ignored).toEqual(expect.arrayContaining(['output_dir']));
  expect(unexpected(launchPayload('tune', tune.config, 'local', study), REQUEST_FIELDS)).toEqual(
    [],
  );
});

it('preserves a tuning study\u2019s checkpoint source and identity when resuming it', () => {
  const saved = {
    trials: 3,
    study_name: 'brandubh-search',
    seed: 7,
    search_space: { learning_rate: { type: 'float', low: 0.001, high: 0.003 } },
    opponents: [{ opponent: 'jarl', weight: 1 }],
    verification: { games: 8, opponent: 'shield_bearer' },
    sampler: { n_startup_trials: 5 },
    pruner: { n_startup_trials: 2 },
    max_failures: 4,
    training: {
      preset: 'smoke',
      ruleset: 'brandubh',
      resume: '/data/jobs/source/last.pt',
      iterations: 12,
      evaluation_opponent: 'jarl',
    },
    output_dir: '/data/jobs/old',
    registry_dir: '/data/models',
    storage: 'sqlite:///old/study.sqlite3',
  };
  const payload = tuneResumePayload('tunejob', saved, 3);
  expect(payload.resume_study).toBe('tunejob');
  expect(payload.trials).toBe(3);
  // The checkpoint source and every objective-defining setting survive.
  expect((payload.training as Record<string, unknown>).resume).toBe('/data/jobs/source/last.pt');
  expect(payload.opponents).toEqual([{ opponent: 'jarl', weight: 1 }]);
  expect(payload.verification).toEqual({ games: 8, opponent: 'shield_bearer' });
  expect(payload.search_space).toEqual(saved.search_space);
  expect(payload.max_failures).toBe(3);
  // Server-owned paths are never sent back.
  for (const key of ['output_dir', 'registry_dir', 'storage']) {
    expect(payload).not.toHaveProperty(key);
  }
});

it('builds a tune payload from study controls and one nested configuration', () => {
  const payload = launchPayload('tune', trainingConfig('tablut', 'smoke'), 'local', {
    ...study,
    resumeStudy: 'study-id',
  });
  // Optional study controls are only sent when the form sets them, but every
  // key the payload can carry must be in the shared contract.
  expect(unexpected(payload, REQUEST_FIELDS)).toEqual([]);
  expect(keysOf(payload)).toEqual(
    ['kind', 'resume_study', 'search_space', 'seed', 'study_name', 'training', 'trials'].sort(),
  );
  expect(payload.resume_study).toBe('study-id');
  expect(payload.search_space).toEqual(SEARCH_SPACE);
  expect(unexpected(payload.training as Record<string, unknown>, TRAINING_FIELDS)).toEqual([]);
});

it('sends only changed, resume-editable settings and never checkpoint identity', () => {
  const saved: TrainingConfig = {
    ...trainingConfig('brandubh', 'smoke'),
    ruleset: 'brandubh',
    channels: 4,
    blocks: 0,
    seed: 7,
    optimizer: 'adamw',
  };
  const edited: TrainingConfig = {
    ...saved,
    ruleset: 'tablut',
    channels: 32,
    blocks: 3,
    seed: 9,
    optimizer: 'sgd',
    iterations: 20,
    batch_size: 64,
    device: 'cpu',
  };
  const overrides = resumeOverrides(edited, saved);
  expect(keysOf(overrides)).toEqual(['batch_size', 'device']);
  expect(unexpected(overrides, TRAINING_FIELDS)).toEqual([]);
  for (const key of RESUME_IMMUTABLE_FIELDS) expect(overrides).not.toHaveProperty(key);
});

it('never reads the tuning search space for ordinary training', () => {
  const malformed = { ...study, space: '{ not json' };
  for (const ruleset of RULESETS) {
    for (const target of ['local', 'remote']) {
      const payload = launchPayload(
        'train',
        trainingConfig(ruleset, 'recommended'),
        target,
        malformed,
      );
      // The invalid tuning text is never parsed, so it cannot block model training.
      expect(payload.search_space).toBeUndefined();
      expect(payload.training).toBeUndefined();
      expect(unexpected(payload, REQUEST_FIELDS)).toEqual([]);
      expect(payload.kind).toBe('train');
      expect(payload.execution).toBe(target === 'local' ? undefined : 'remote');
    }
  }
  // The same malformed text is only a problem while the form is tuning, and
  // switching it back to training submits the ordinary payload again.
  expect(() =>
    launchPayload('tune', trainingConfig('tablut', 'smoke'), 'local', malformed),
  ).toThrow(TrainingInputError);
  expect(launchPayload('train', trainingConfig('tablut', 'smoke'), 'local', malformed).kind).toBe(
    'train',
  );
});

it('carries optional study strategy settings into the tune payload', () => {
  const payload = launchPayload('tune', trainingConfig('tablut', 'smoke'), 'local', {
    ...study,
    strategy: JSON.stringify({
      opponents: [{ opponent: 'jarl', weight: 1 }],
      verification: { games: 8, opponent: 'shield_bearer' },
      max_failures: 2,
    }),
  });
  expect(unexpected(payload, REQUEST_FIELDS)).toEqual([]);
  expect(payload.opponents).toEqual([{ opponent: 'jarl', weight: 1 }]);
  expect(payload.verification).toEqual({ games: 8, opponent: 'shield_bearer' });
  expect(payload.max_failures).toBe(2);
  // Empty strategy text keeps the default single-opponent objective.
  expect(
    launchPayload('tune', trainingConfig('tablut', 'smoke'), 'local', study),
  ).not.toHaveProperty('opponents');
  // Malformed or non-object strategy text is a named input error.
  expect(() =>
    launchPayload('tune', trainingConfig('tablut', 'smoke'), 'local', {
      ...study,
      strategy: '[1, 2]',
    }),
  ).toThrow(TrainingInputError);
  expect(() =>
    launchPayload('tune', trainingConfig('tablut', 'smoke'), 'local', {
      ...study,
      strategy: '{',
    }),
  ).toThrow(TrainingInputError);
});

it('reports malformed tuning JSON as a search-space field error', () => {
  expect(() =>
    launchPayload('tune', trainingConfig('tablut', 'smoke'), 'local', { ...study, space: '{' }),
  ).toThrow(TrainingInputError);
  try {
    parseStudySpace('{');
  } catch (error) {
    expect(rejectedTrainingFields(error)).toEqual(['search_space']);
    expect((error as Error).message).toBe('The parameter search space is not valid JSON.');
  }
  // A valid search space still produces the tuning request unchanged.
  expect(parseStudySpace(JSON.stringify(SEARCH_SPACE))).toEqual(SEARCH_SPACE);
});

it('keeps complete field paths and maps them to the right input', () => {
  // `seed` is the Optuna study seed; `training.seed` is the model seed.
  expect(fieldIdentity('seed', 'tune')).toBe('study.seed');
  expect(fieldIdentity('training.seed', 'tune')).toBe('seed');
  expect(fieldIdentity('seed', 'train')).toBe('seed');
  expect(fieldIdentity('training.seed', 'train')).toBe('seed');
  expect(fieldIdentity('training.channels', 'tune')).toBe('channels');
  expect(fieldIdentity('channels', 'train')).toBe('channels');
  // Study controls only exist in a tuning form.
  for (const key of STUDY_FIELDS) {
    expect(fieldIdentity(key, 'tune')).toBe(`study.${key}`);
    expect(fieldIdentity(key, 'train')).toBe(key);
  }
  // An unknown nested path keeps a usable identity instead of collapsing.
  expect(fieldIdentity('training.future_field', 'tune')).toBe('future_field');
  expect(fieldIdentity('output_dir', 'tune')).toBe('output_dir');
});

it('keeps the two seeds distinct when both are rejected', () => {
  const error = new ApiError(400, 'Unsupported', 'unsupported_training_field', [
    'training.seed',
    'seed',
  ]);
  expect(rejectedTrainingFields(error)).toEqual(['seed', 'training.seed']);
  expect(rejectedTrainingFields(error).map((path) => fieldIdentity(path, 'tune'))).toEqual([
    'study.seed',
    'seed',
  ]);
});

it('names rejected fields without collapsing or dropping any path', () => {
  expect(
    rejectedTrainingFields(
      new ApiError(400, 'Unsupported', 'unsupported_training_field', ['output_dir']),
    ),
  ).toEqual(['output_dir']);
  expect(
    rejectedTrainingFields(
      new ApiError(400, 'Unsupported', 'unsupported_training_field', [
        'training.channels',
        'training.channels',
        'output_dir',
        'training.seed',
        'seed',
      ]),
    ),
  ).toEqual(['output_dir', 'seed', 'training.channels', 'training.seed']);
  expect(rejectedTrainingFields(new TrainingInputError(['search_space'], 'bad'))).toEqual([
    'search_space',
  ]);
  // A plain error carries no field information and must not be treated as one.
  expect(rejectedTrainingFields(new Error('Unsupported'))).toEqual([]);
});
