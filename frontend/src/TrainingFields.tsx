import { t } from './i18n';
import type { TrainingConfig } from './trainingPresets';

export const trainingNumberFields: Record<string, [string, number, number, number?]> = {
  iterations: ['Training iterations', 1, 100000],
  games: ['Self-play games per iteration', 1, 10000],
  batch_size: ['Batch size', 1, 2048],
  mcts_iterations: ['MCTS iterations per move', 1, 100000],
  channels: ['Network channels', 4, 128],
  blocks: ['Residual blocks', 0, 8],
  epochs: ['Epochs per iteration', 1, 100],
  learning_rate: ['Learning rate', 1e-7, 1, 0.0001],
  mcts_time_limit: ['Search time limit (seconds)', 0.001, 300, 0.01],
  exploration: ['MCTS exploration', 0, 20, 0.1],
  temperature: ['Self-play temperature', 0.01, 10, 0.01],
  dirichlet_alpha: ['Exploration noise alpha', 0.001, 100, 0.01],
  dirichlet_fraction: ['Exploration noise fraction', 0, 1, 0.05],
  replay_buffer_size: ['Replay buffer positions', 1, 20000],
  weight_decay: ['Weight regularization', 0, 1, 0.0001],
  momentum: ['Momentum', 0, 1, 0.01],
  checkpoint_frequency: ['Checkpoint every (iterations)', 1, 10000],
  evaluation_frequency: ['Evaluate every (iterations)', 1, 10000],
  evaluation_games: ['Evaluation games (even)', 2, 10000, 2],
  evaluation_iterations: ['Evaluation search iterations', 1, 100000],
  max_game_plies: ['Self-play move limit', 2, 5000],
  evaluation_max_plies: ['Evaluation move limit', 2, 5000],
  cpu_threads: ['CPU threads', 1, 64],
  seed: ['Random seed', 0, 4294967295],
};

export function validTrainingNumbers(config: TrainingConfig): boolean {
  return Object.entries(trainingNumberFields).every(([key, [, min, max, step = 1]]) => {
    if (config[key] === undefined) return true;
    const value = config[key];
    return (
      typeof value === 'number' &&
      Number.isFinite(value) &&
      value >= min &&
      value <= max &&
      (!Number.isInteger(step) || (Number.isInteger(value) && (value - min) % step === 0))
    );
  });
}

export function numberField(
  key: string,
  config: TrainingConfig,
  update: (key: string, value: number) => void,
  options: { label?: string; min?: number; disabled?: boolean } = {},
) {
  const [label, min, max, step = 1] = trainingNumberFields[key];
  return (
    <label key={key}>
      {t(options.label ?? label)}
      <input
        type="number"
        required
        min={options.min ?? min}
        max={max}
        step={Number.isInteger(step) ? step : 'any'}
        value={Number.isNaN(Number(config[key])) ? '' : Number(config[key])}
        disabled={options.disabled}
        onChange={(event) => update(key, event.currentTarget.valueAsNumber)}
      />
    </label>
  );
}
