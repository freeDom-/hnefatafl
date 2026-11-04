import { useId } from 'react';
import type { Model } from './types';
import TuningOpponents from './TuningOpponents';
import { useI18n } from './i18n';
import { fieldErrorAttributes, numberField } from './TrainingFields';
import { FieldLabel } from './FieldTooltip';
import type { TrainingConfig } from './trainingPresets';

export const evaluationDefaults: TrainingConfig = {
  evaluation_opponent: 'shield_bearer',
  evaluation_frequency: 1,
  evaluation_mode: 'auto',
  evaluation_concurrency: 16,
  evaluation_batch_size: 32,
  evaluation_inference_backend: 'pytorch',
  evaluation_games: 2,
  evaluation_iterations: 16,
  evaluation_max_plies: 150,
  evaluation_time_limit: 10,
  evaluation_jarl_depth: 5,
  evaluation_jarl_time_limit: 2,
  champion_confidence: 0.95,
  champion_min_pairs: 12,
  champion_min_improvement: 0,
};

export const opponentName = (opponent: string) =>
  ({
    shield_bearer: 'Shield bearer',
    shield_maiden: 'Shield maiden',
    baseline: 'Shield bearer',
    jarl: 'Jarl',
    champion: 'Champion',
    frozen_model: 'Frozen neural model',
    previous_model: 'Previous model',
  })[opponent] ?? opponent;

export default function EvaluationSettings({
  config,
  update,
  disabled = false,
  tuning = false,
  invalidFields = [],
  errorId,
  models = [],
  strategy,
  onStrategyChange,
}: {
  models?: Model[];
  strategy?: string;
  onStrategyChange?: (value: string) => void;
  config: TrainingConfig;
  update: (key: string, value: string | number | boolean) => void;
  disabled?: boolean;
  tuning?: boolean;
  /** Input identities the server rejected, so each control marks itself. */
  invalidFields?: string[];
  errorId?: string;
}) {
  const { t } = useI18n();
  const opponentId = useId();
  const modeId = useId();
  const backendId = useId();
  const rejected = new Set(invalidFields);
  const values = { ...evaluationDefaults, ...config };
  // `disabled` is applied per control rather than to the fieldset, so a disabled
  // form still offers the parameter explanations through the help buttons.
  const field = (key: string) => ({
    invalid: rejected.has(key),
    errorId,
    disabled,
  });
  return (
    <fieldset className="training-evaluation">
      <legend>{t('Evaluation')}</legend>
      <p className="subtle">
        {t(
          'Only the selected opponent is evaluated. The final iteration is also evaluated; checkpoint frequency is independent.',
        )}
      </p>
      {tuning && values.evaluation_opponent === 'champion' && (
        <p className="form-error" role="alert">
          {t('Choose a fixed benchmark before starting tuning. Champion changes during training.')}
        </p>
      )}
      <div className="form-grid">
        <div className="training-field">
          <FieldLabel
            htmlFor={opponentId}
            label="Evaluation opponent"
            tooltipKey="evaluation_opponent"
          />
          <select
            id={opponentId}
            data-training-field="evaluation_opponent"
            {...fieldErrorAttributes(
              rejected.has('evaluation_opponent') ||
                (tuning && values.evaluation_opponent === 'champion'),
              errorId,
            )}
            value={String(values.evaluation_opponent)}
            disabled={disabled}
            onChange={(event) => update('evaluation_opponent', event.target.value)}
          >
            <option value="shield_bearer">{t('Shield bearer')}</option>
            <option value="shield_maiden">{t('Shield maiden')}</option>
            <option value="jarl">{t('Jarl')}</option>
            <option value="champion" disabled={tuning}>
              {t('Champion')}
            </option>
          </select>
        </div>
        <div className="training-field">
          <FieldLabel htmlFor={modeId} label="Evaluation execution" tooltipKey="evaluation_mode" />
          <select
            id={modeId}
            data-training-field="evaluation_mode"
            value={String(values.evaluation_mode)}
            disabled={disabled}
            onChange={(event) => update('evaluation_mode', event.target.value)}
          >
            <option value="auto">{t('Automatic')}</option>
            <option value="sequential">{t('Sequential')}</option>
            <option value="batched">{t('Batched')}</option>
          </select>
        </div>
        <div className="training-field">
          <FieldLabel
            htmlFor={backendId}
            label="Evaluation inference backend"
            tooltipKey="evaluation_inference_backend"
          />
          <select
            id={backendId}
            data-training-field="evaluation_inference_backend"
            value={String(values.evaluation_inference_backend)}
            disabled={disabled}
            onChange={(event) => update('evaluation_inference_backend', event.target.value)}
          >
            <option value="pytorch">PyTorch</option>
            <option value="training">{t('Use training inference backend')}</option>
          </select>
        </div>
        {[
          'evaluation_concurrency',
          'evaluation_batch_size',
          'evaluation_frequency',
          'evaluation_games',
          'evaluation_max_plies',
          'evaluation_iterations',
          'evaluation_time_limit',
        ].map((key) => numberField(key, values, update, field(key)))}
        {values.evaluation_opponent === 'jarl' &&
          ['evaluation_jarl_depth', 'evaluation_jarl_time_limit'].map((key) =>
            numberField(key, values, update, field(key)),
          )}
        {values.evaluation_opponent === 'champion' &&
          ['champion_confidence', 'champion_min_pairs', 'champion_min_improvement'].map((key) =>
            numberField(key, values, update, field(key)),
          )}
      </div>
      <p className="subtle">
        {t(
          tuning
            ? 'Optimization uses fixed references for comparable objectives. Champion evolves during training and is unavailable for tuning.'
            : values.evaluation_opponent === 'champion'
              ? 'Champion promotion requires complete side-swapped pairs and a confidence bound above 50% plus the required improvement. Small leads may be inconclusive.'
              : values.evaluation_opponent === 'shield_maiden'
                ? 'Shield maiden uses a stronger tactical heuristic, designed for low evaluation overhead.'
                : values.evaluation_opponent === 'jarl'
                  ? 'Jarl uses alpha-beta search. Depth and deadlines can make evaluation expensive.'
                  : 'Shield bearer is a heuristic benchmark for early training. Tied scores keep the earlier best checkpoint.',
        )}
      </p>
      {tuning && onStrategyChange && (
        <TuningOpponents
          models={models}
          ruleset={String(config.ruleset)}
          strategy={strategy ?? ''}
          onChange={onStrategyChange}
          disabled={disabled}
        />
      )}
      {values.evaluation_opponent === 'champion' &&
        Number(values.evaluation_games) < 2 * Number(values.champion_min_pairs) && (
          <p role="status">
            {t(
              'This game budget is below the minimum pair count; promotion will remain inconclusive.',
            )}
          </p>
        )}
    </fieldset>
  );
}
