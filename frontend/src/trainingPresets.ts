import { trainingNumberFields } from './TrainingFields';
import type { DeviceInfo } from './types';

export type TrainingPreset = 'recommended' | 'smoke' | 'beginner' | 'balanced';
export type TrainingConfig = Record<string, string | number | boolean>;

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
  dirichlet_alpha: 0.3,
  dirichlet_fraction: 0.25,
  save_all_best: false,
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
  evaluation_opponent: ['shield_bearer', 'shield_maiden', 'jarl', 'champion'],
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
  };
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
    mixed_precision: previous.mixed_precision,
    save_all_best: previous.save_all_best,
    self_play_mode: previous.self_play_mode ?? 'auto',
    self_play_concurrency: previous.self_play_concurrency ?? 16,
    inference_batch_size: previous.inference_batch_size ?? 32,
    inference_cache_mode: previous.inference_cache_mode ?? 'off',
    inference_cache_capacity: previous.inference_cache_capacity ?? 4096,
    training_symmetry_mode: previous.training_symmetry_mode ?? 'random_d4',
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
