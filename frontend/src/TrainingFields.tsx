import { useId, useRef } from 'react';
import { t } from './i18n';
import { FieldInfo, FieldLabel, useFieldTooltip } from './FieldTooltip';
import type { TrainingConfig } from './trainingPresets';

export const trainingNumberFields: Record<string, [string, number, number, number?]> = {
  iterations: ['Training iterations', 1, 100000],
  games: ['Self-play games per iteration', 1, 10000],
  batch_size: ['Batch size', 1, 2048],
  mcts_iterations: ['MCTS iterations per move', 1, 100000],
  self_play_concurrency: ['Concurrent self-play games', 1, 1024],
  inference_batch_size: ['Inference batch size', 1, 4096],
  inference_cache_capacity: ['Inference cache capacity', 1, 65536],
  channels: ['Network channels', 4, 128],
  blocks: ['Residual blocks', 0, 8],
  epochs: ['Epochs per iteration', 1, 100],
  learning_rate: ['Learning rate', 1e-7, 1, 0.0001],
  schedulefree_warmup_steps: ['Schedule-Free warm-up steps', 0, 1000000],
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
  evaluation_concurrency: ['Concurrent evaluation games', 1, 1024],
  evaluation_batch_size: ['Evaluation inference batch size', 1, 4096],
  evaluation_games: ['Evaluation games (even)', 2, 10000, 2],
  evaluation_iterations: ['Evaluation search iterations', 1, 100000],
  evaluation_time_limit: ['Evaluation search time limit (seconds)', 0.001, 300, 0.01],
  evaluation_jarl_depth: ['Jarl evaluation depth', 1, 8],
  evaluation_jarl_time_limit: ['Jarl search time limit (seconds)', 0.001, 300, 0.01],
  champion_confidence: ['Champion confidence level', 0.8, 0.9999, 0.01],
  champion_min_pairs: ['Minimum complete evaluation pairs', 2, 5000],
  champion_min_improvement: ['Required champion improvement', 0, 0.49, 0.01],
  max_game_plies: ['Self-play move limit', 2, 5000],
  evaluation_max_plies: ['Evaluation move limit', 2, 5000],
  cpu_threads: ['CPU threads', 1, 64],
  search_workers: ['CPU search processes (0 disables)', 0, 32],
  seed: ['Random seed', 0, 4294967295],
  adaptation_cadence: ['Decide every (evaluations)', 1, 10000],
  adaptation_warmup: ['Warm-up evaluations', 0, 10000],
  adaptation_min_dwell: ['Minimum dwell (evaluations)', 1, 10000],
  adaptation_lag: ['Observation history window', 1, 3],
  adaptation_min_trials: ['Minimum trials per candidate', 1, 100],
};

export const trainingParameterLabels: Record<string, string> = {
  device: 'Training device',
  inference_backend: 'Neural inference backend',
  inference_device: 'Inference device',
  preset: 'Training preset',
  optimizer: 'Optimizer',
  self_play_mode: 'Self-play execution',
  evaluation_opponent: 'Evaluation opponent',
  evaluation_mode: 'Evaluation execution',
  evaluation_inference_backend: 'Evaluation inference backend',
  model_name: 'Model name',
  ruleset: 'Battlefield',
  search_space: 'Parameter search space (JSON)',
  trials: 'Trial budget',
  study_name: 'Study name',
  study_seed: 'Study random seed',
  inference_cache_mode: 'Inference cache',
  training_symmetry_mode: 'Training symmetry augmentation',
  adaptation: 'Automatic hyperparameter updates',
  adaptation_lr_candidates: 'Learning rate candidates',
};

/**
 * Attributes that mark one control as rejected by the server. The alert that
 * lists every rejected field describes each marked input, so a screen reader
 * announces the error with the field it belongs to.
 */
export interface FieldErrorAttributes {
  'aria-invalid'?: true;
  'aria-describedby'?: string;
  className?: string;
}

export function fieldErrorAttributes(invalid: boolean, errorId?: string): FieldErrorAttributes {
  return invalid
    ? { 'aria-invalid': true, 'aria-describedby': errorId, className: 'field-invalid' }
    : {};
}

/** Human label (translation key) for one importable training parameter. */
export function trainingParameterLabel(key: string): string {
  return trainingNumberFields[key]?.[0] ?? trainingParameterLabels[key] ?? key;
}

export interface NumberFieldOptions {
  label?: string;
  tooltipKey?: string;
  min?: number;
  disabled?: boolean;
  invalid?: boolean;
  errorId?: string;
}

/**
 * One numeric training parameter: its label with the shared tooltip, the number
 * input, and the bounds the shared schema declares. The label is explicit so the
 * tooltip button never joins the control's accessible name.
 */
export function TrainingNumberField({
  fieldKey,
  config,
  update,
  options = {},
}: {
  fieldKey: string;
  config: TrainingConfig;
  update: (key: string, value: number) => void;
  options?: NumberFieldOptions;
}) {
  const id = useId();
  const [label, min, max, step = 1] = trainingNumberFields[fieldKey];
  return (
    <div className="training-field">
      <FieldLabel
        htmlFor={id}
        label={options.label ?? label}
        tooltipKey={options.tooltipKey ?? fieldKey}
      />
      <input
        id={id}
        type="number"
        required
        min={options.min ?? min}
        max={max}
        step={Number.isInteger(step) ? step : 'any'}
        value={Number.isNaN(Number(config[fieldKey])) ? '' : Number(config[fieldKey])}
        disabled={options.disabled}
        // Lets the form mark this exact input when the server rejects the field.
        data-training-field={fieldKey}
        {...fieldErrorAttributes(options.invalid === true, options.errorId)}
        onChange={(event) => update(fieldKey, event.currentTarget.valueAsNumber)}
      />
    </div>
  );
}

/** Renders one numeric parameter field; kept as a factory so call sites stay flat. */
export function numberField(
  key: string,
  config: TrainingConfig,
  update: (key: string, value: number) => void,
  options: NumberFieldOptions = {},
) {
  return (
    <TrainingNumberField
      key={key}
      fieldKey={key}
      config={config}
      update={update}
      options={options}
    />
  );
}

/**
 * A checkbox training parameter. The label wraps the input so the whole row stays
 * clickable, and the help button sits beside it so the accessible name is
 * unchanged. Hovering the row reveals the tooltip as well as the button.
 */
export function CheckboxField({
  fieldKey,
  label,
  checked,
  onChange,
  disabled,
  invalid,
  errorId,
  className,
}: {
  fieldKey: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  invalid?: boolean;
  errorId?: string;
  className?: string;
}) {
  const id = useId();
  const container = useRef<HTMLDivElement>(null);
  const state = useFieldTooltip(container);
  return (
    <div
      className={className ? `checkbox-field ${className}` : 'checkbox-field'}
      ref={container}
      {...state.hover}
    >
      <label className="checkbox-label">
        <input
          type="checkbox"
          data-training-field={fieldKey}
          checked={checked}
          disabled={disabled}
          {...fieldErrorAttributes(invalid === true, errorId)}
          onChange={(event) => onChange(event.target.checked)}
        />
        {t(label)}
      </label>
      <FieldInfo tooltipKey={fieldKey} fieldLabel={t(label)} state={state} id={`${id}-help`} />
    </div>
  );
}

export function SelfPlayModeField({
  value,
  onChange,
  disabled,
  invalid,
  errorId,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  invalid?: boolean;
  errorId?: string;
}) {
  const id = useId();
  return (
    <div className="training-field">
      <FieldLabel htmlFor={id} label="Self-play execution" tooltipKey="self_play_mode" />
      <select
        id={id}
        data-training-field="self_play_mode"
        {...fieldErrorAttributes(invalid === true, errorId)}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        <option value="auto">{t('Automatic · batched on CUDA/OpenVINO')}</option>
        <option value="sequential">{t('Sequential')}</option>
        <option value="batched">{t('Batched')}</option>
      </select>
    </div>
  );
}

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
