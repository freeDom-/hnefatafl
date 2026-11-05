import { trainingNumberFields } from './TrainingFields';
import type { DeviceInfo } from './types';

export type TrainingPreset = 'recommended' | 'smoke' | 'beginner' | 'balanced';
export type TrainingConfig = Record<string, string | number | boolean>;

/** Shared controller defaults, mirroring the backend's ``adaptation_defaults()``. */
export const adaptationDefaults: TrainingConfig = {
  adaptation: 'off',
  adaptation_lr_candidates: '0.0003,0.001,0.003',
  adaptation_cadence: 1,
  adaptation_warmup: 2,
  adaptation_min_dwell: 1,
  adaptation_lag: 1,
  adaptation_min_trials: 1,
};

const base: TrainingConfig = {
  ruleset: 'tablut',
  preset: 'recommended',
  channels: 16,
  blocks: 1,
  iterations: 5,
  games: 4,
  epochs: 2,
  batch_size: 32,
  learning_rate: 0.001,
  optimizer: 'adamw',
  mcts_iterations: 24,
  mcts_time_limit: 10,
  self_play_mode: 'auto',
  self_play_concurrency: 16,
  inference_batch_size: 32,
  inference_cache_mode: 'off',
  inference_cache_capacity: 4096,
  exploration: 1.4,
  replay_buffer_size: 5000,
  weight_decay: 0.0001,
  momentum: 0.9,
  training_symmetry_mode: 'random_d4',
  checkpoint_frequency: 1,
  evaluation_frequency: 1,
  evaluation_mode: 'auto',
  evaluation_concurrency: 16,
  evaluation_batch_size: 32,
  evaluation_inference_backend: 'pytorch',
  evaluation_opponent: 'shield_bearer',
  evaluation_time_limit: 10,
  evaluation_jarl_depth: 5,
  evaluation_jarl_time_limit: 2,
  champion_confidence: 0.95,
  champion_min_pairs: 12,
  champion_min_improvement: 0,
  evaluation_games: 2,
  evaluation_iterations: 16,
  seed: 42,
  device: 'auto',
  inference_backend: 'pytorch',
  inference_device: 'auto',
  mixed_precision: true,
  max_game_plies: 150,
  evaluation_max_plies: 150,
  model_name: 'My first strategist',
  temperature: 1,
  cpu_threads: 2,
  search_workers: 0,
  dirichlet_alpha: 0.3,
  dirichlet_fraction: 0.25,
  save_all_best: false,
  ...adaptationDefaults,
};
/** Explicit schema for importable parameters. Anything else is metadata, not input. */
export const trainingChoices: Record<string, string[]> = {
  preset: ['recommended', 'smoke', 'beginner', 'balanced'],
  ruleset: ['brandubh', 'tablut', 'copenhagen'],
  device: ['auto', 'cpu', 'cuda', 'mps'],
  inference_cache_mode: ['off', 'exact'],
  training_symmetry_mode: ['off', 'random_d4'],
  inference_backend: ['pytorch', 'openvino'],
  optimizer: ['adamw', 'adam', 'sgd'],
  self_play_mode: ['auto', 'sequential', 'batched'],
  evaluation_mode: ['auto', 'sequential', 'batched'],
  evaluation_inference_backend: ['pytorch', 'training'],
  evaluation_opponent: ['shield_bearer', 'shield_maiden', 'jarl', 'champion'],
  adaptation: ['off', 'hypercontroller'],
};

export function isTrainingParameter(key: string): boolean {
  return Object.hasOwn(base, key);
}

/** One validator shared by recorded-config reuse and explicit config.json import. */
export function validTrainingInput(key: string, value: unknown): boolean {
  const bounds = trainingNumberFields[key];
  return (
    isTrainingParameter(key) &&
    typeof value === typeof base[key] &&
    (typeof value !== 'number' || Number.isFinite(value)) &&
    (!trainingChoices[key] || trainingChoices[key].includes(String(value))) &&
    (!bounds ||
      (typeof value === 'number' &&
        value >= bounds[1] &&
        value <= bounds[2] &&
        (!Number.isInteger(bounds[3] ?? 1) ||
          (Number.isInteger(value) && (value - bounds[1]) % (bounds[3] ?? 1) === 0)))) &&
    (key !== 'model_name' || (String(value).trim().length > 0 && String(value).length <= 100)) &&
    (key !== 'inference_device' || /^(auto|cpu|cuda|mps|GPU(?:\.\d+)?)$/.test(String(value)))
  );
}

/** A controller configuration problem and the form input the user must fix. */
export interface AdaptationConfigError {
  field: string;
  message: string;
}

// Candidate bounds mirror hnefatafl/hyper_adaptation.py: 2..8 distinct rates in
// [1e-7, 1], and candidates ** (lag + 1) learned contexts at most.
const MIN_ADAPTATION_CANDIDATES = 2;
const MAX_ADAPTATION_CANDIDATES = 8;
const MAX_ADAPTATION_CONTEXTS = 512;
const ADAPTATION_LR_MIN = 1e-7;
const ADAPTATION_LR_MAX = 1;
// Decimal/scientific notation only, so trailing garbage, hex, Infinity and NaN
// are rejected instead of being silently dropped. Digit-separating underscores
// are accepted like Python's float(), never adjacent to '.', 'e' or the sign.
const ADAPTATION_CANDIDATE_SYNTAX =
  /^[+-]?(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:[eE][+-]?\d(?:_?\d)*)?$/;

/**
 * Cross-field constraints the controller needs. Per-field validation cannot see
 * these, and the server is the security boundary; this gives the workshop an
 * immediate explanation for a configuration that cannot run. Every candidate is
 * validated (never filtered away) against the backend's contract.
 */
export function adaptationConfigError(config: TrainingConfig): AdaptationConfigError | null {
  if (config.adaptation !== 'hypercontroller') return null;
  if (config.evaluation_opponent === 'champion')
    return {
      field: 'evaluation_opponent',
      message: 'Automatic hyperparameter updates need a fixed evaluation opponent, not Champion.',
    };
  if (Number(config.evaluation_games) < 4)
    return {
      field: 'evaluation_games',
      message: 'Automatic hyperparameter updates need at least 4 evaluation games.',
    };

  const candidates: number[] = [];
  for (const entry of String(config.adaptation_lr_candidates ?? '').split(',')) {
    const token = entry.trim();
    // The backend skips empty entries, including whitespace-only ones.
    if (token === '') continue;
    if (!ADAPTATION_CANDIDATE_SYNTAX.test(token))
      return {
        field: 'adaptation_lr_candidates',
        message: 'Learning rate candidates must be decimal numbers separated by commas.',
      };
    const value = Number(token.replace(/_/g, ''));
    if (!Number.isFinite(value) || value < ADAPTATION_LR_MIN || value > ADAPTATION_LR_MAX)
      return {
        field: 'adaptation_lr_candidates',
        message: 'Learning rate candidates must be between 1e-7 and 1.',
      };
    if (!candidates.includes(value)) candidates.push(value);
  }
  if (
    candidates.length < MIN_ADAPTATION_CANDIDATES ||
    candidates.length > MAX_ADAPTATION_CANDIDATES
  )
    return {
      field: 'adaptation_lr_candidates',
      message: 'Learning rate candidates must contain between 2 and 8 distinct values.',
    };
  const lag = Number(config.adaptation_lag ?? 1);
  if (candidates.length ** (lag + 1) > MAX_ADAPTATION_CONTEXTS)
    return {
      field: 'adaptation_lr_candidates',
      message:
        'Learning rate candidates and the observation history window produce too many controller contexts; use fewer candidates or a smaller window.',
    };
  if (!candidates.includes(Number(config.learning_rate)))
    return {
      field: 'learning_rate',
      message: 'The learning rate must be one of the adaptation candidates.',
    };
  return null;
}

const plans: Record<string, TrainingConfig> = {
  brandubh: {
    channels: 32,
    blocks: 2,
    iterations: 20,
    games: 16,
    epochs: 2,
    batch_size: 64,
    learning_rate: 0.001,
    mcts_iterations: 128,
    mcts_time_limit: 10,
    replay_buffer_size: 8000,
    max_game_plies: 200,
    evaluation_games: 12,
    evaluation_iterations: 64,
    evaluation_max_plies: 300,
    dirichlet_alpha: 0.3,
  },
  tablut: {
    channels: 48,
    blocks: 3,
    iterations: 30,
    games: 24,
    epochs: 3,
    batch_size: 96,
    learning_rate: 0.0007,
    mcts_iterations: 192,
    mcts_time_limit: 10,
    replay_buffer_size: 12000,
    max_game_plies: 300,
    evaluation_games: 16,
    evaluation_iterations: 96,
    evaluation_max_plies: 500,
    dirichlet_alpha: 0.2,
  },
  copenhagen: {
    channels: 64,
    blocks: 4,
    iterations: 40,
    games: 32,
    epochs: 3,
    batch_size: 128,
    learning_rate: 0.0005,
    mcts_iterations: 256,
    mcts_time_limit: 10,
    replay_buffer_size: 20000,
    max_game_plies: 500,
    evaluation_games: 20,
    evaluation_iterations: 128,
    evaluation_max_plies: 800,
    dirichlet_alpha: 0.15,
  },
};

export function trainingConfig(ruleset: string, preset: TrainingPreset): TrainingConfig {
  const options =
    preset === 'recommended'
      ? (plans[ruleset] ?? plans.tablut)
      : preset === 'smoke'
        ? {
            channels: 4,
            blocks: 0,
            games: 1,
            iterations: 1,
            epochs: 1,
            batch_size: 8,
            mcts_iterations: 2,
            mcts_time_limit: 0.1,
            evaluation_time_limit: 0.1,
            evaluation_iterations: 2,
            max_game_plies: 12,
            evaluation_max_plies: 12,
            replay_buffer_size: 64,
          }
        : preset === 'balanced'
          ? {
              channels: 32,
              blocks: 2,
              games: 16,
              iterations: 20,
              epochs: 4,
              batch_size: 64,
              mcts_iterations: 100,
              replay_buffer_size: 10000,
              max_game_plies: 300,
            }
          : {};
  return {
    ...base,
    ...options,
    ruleset,
    preset,
    evaluation_time_limit: options.mcts_time_limit ?? base.mcts_time_limit,
    // Bounded candidate generation around the plan's starting rate, so enabling
    // adaptation on any preset is valid without hand-editing the list.
    adaptation_lr_candidates: boundedCandidates(
      Number(options.learning_rate ?? base.learning_rate),
    ),
  };
}

/**
 * Up to three distinct candidates around a starting rate, kept inside the valid
 * range. The exact starting rate is always present; clamping near the minimum or
 * maximum can collapse its neighbours, so the list is deduplicated and always
 * holds at least the backend's minimum of two distinct values.
 */
export function boundedCandidates(learningRate: number): string {
  const base = Math.min(1, Math.max(1e-7, learningRate));
  const values: number[] = [];
  const add = (value: number) => {
    const bounded = Math.min(1, Math.max(1e-7, value));
    if (!values.includes(bounded)) values.push(bounded);
  };
  add(base);
  add(Number((base / 3).toPrecision(4)));
  add(Number((base * 3).toPrecision(4)));
  if (values.length < 2) {
    // Defensive: only reachable if rounding collapses both neighbours.
    add(base >= 0.5 ? base / 3 : base * 3);
  }
  return values
    .sort((left, right) => left - right)
    .map((value) => String(value))
    .join(',');
}

export function changeTrainingPlan(
  previous: TrainingConfig,
  ruleset: string,
  preset: TrainingPreset,
): TrainingConfig {
  return {
    ...trainingConfig(ruleset, preset),
    model_name: previous.model_name,
    seed: previous.seed,
    device: previous.device,
    inference_backend: previous.inference_backend ?? 'pytorch',
    inference_device: previous.inference_device ?? 'auto',
    cpu_threads: previous.cpu_threads,
    search_workers: previous.search_workers ?? 0,
    mixed_precision: previous.mixed_precision,
    save_all_best: previous.save_all_best,
    self_play_mode: previous.self_play_mode ?? 'auto',
    self_play_concurrency: previous.self_play_concurrency ?? 16,
    inference_batch_size: previous.inference_batch_size ?? 32,
    inference_cache_mode: previous.inference_cache_mode ?? 'off',
    inference_cache_capacity: previous.inference_cache_capacity ?? 4096,
    training_symmetry_mode: previous.training_symmetry_mode ?? 'random_d4',
    adaptation: previous.adaptation ?? 'off',
    adaptation_cadence: previous.adaptation_cadence ?? 1,
    adaptation_warmup: previous.adaptation_warmup ?? 2,
    adaptation_min_dwell: previous.adaptation_min_dwell ?? 1,
    adaptation_lag: previous.adaptation_lag ?? 1,
    adaptation_min_trials: previous.adaptation_min_trials ?? 1,
  };
}

export interface RecordedParameters {
  config: TrainingConfig;
  warnings: string[];
}

/** One allowlist/validator for model and job reuse. Execution metadata never becomes input. */
export function recordedTrainingParameters(
  previous: TrainingConfig,
  model: { name: string; ruleset: string; training_config?: Record<string, unknown> },
  capabilities?: DeviceInfo,
): RecordedParameters | null {
  const recorded = model.training_config;
  if (!recorded || typeof recorded !== 'object' || Array.isArray(recorded)) return null;
  const values: TrainingConfig = {};
  const warnings: string[] = [];
  for (const key of Object.keys(base)) {
    const value = recorded[key];
    if (value === undefined) continue;
    if (validTrainingInput(key, value)) values[key] = value as string | number | boolean;
    else warnings.push(key);
  }
  if (!Object.keys(values).some((key) => !['model_name', 'ruleset', 'preset'].includes(key)))
    return null;
  const config: TrainingConfig = {
    ...base,
    ...Object.fromEntries(Object.entries(previous).filter(([key]) => Object.hasOwn(base, key))),
    ...Object.fromEntries(
      warnings
        .filter((key) => !validTrainingInput(key, previous[key]))
        .map((key) => [key, base[key]]),
    ),
    inference_backend: 'pytorch',
    inference_device: 'auto',
    inference_cache_mode: 'off',
    inference_cache_capacity: 4096,
    training_symmetry_mode: 'off',
    ...values,
    model_name: model.name.trim() && model.name.length <= 100 ? model.name : previous.model_name,
    ruleset: trainingChoices.ruleset.includes(model.ruleset) ? model.ruleset : previous.ruleset,
  };
  if (!trainingChoices.ruleset.includes(model.ruleset)) warnings.push('ruleset');
  if (capabilities) {
    if (
      (config.device === 'cuda' && !capabilities.cuda_available) ||
      (config.device === 'mps' && !capabilities.mps_available)
    ) {
      config.device = 'cpu';
      warnings.push('device');
    }
    if (config.inference_backend === 'openvino') {
      const requested = String(config.inference_device);
      const available =
        capabilities.inference_backends?.includes('openvino') &&
        capabilities.openvino_devices?.some(
          (device) =>
            device.available &&
            /^GPU(?:\.\d+)?$/.test(device.id) &&
            (device.id === requested || ['auto', 'GPU'].includes(requested)),
        );
      if (available) {
        if (!['cpu', 'auto'].includes(String(config.device))) warnings.push('device');
        config.device = 'cpu';
      } else {
        config.inference_backend = 'pytorch';
        config.inference_device = 'auto';
        warnings.push('inference_backend', 'inference_device');
      }
    } else if (config.inference_device !== 'auto' && config.inference_device !== config.device) {
      config.inference_device = 'auto';
      warnings.push('inference_device');
    }
  }
  return { config, warnings: [...new Set(warnings)] };
}

export function recordedTrainingConfig(
  previous: TrainingConfig,
  model: { name: string; ruleset: string; training_config?: Record<string, unknown> },
): TrainingConfig | null {
  return recordedTrainingParameters(previous, model)?.config ?? null;
}
