import { useI18n } from './i18n';
import { numberField } from './TrainingFields';
import type { TrainingConfig } from './trainingPresets';

export const evaluationDefaults: TrainingConfig = {
  evaluation_opponent: 'shield_bearer',
  evaluation_frequency: 1,
  evaluation_games: 2,
  evaluation_iterations: 16,
  evaluation_max_plies: 150,
  evaluation_time_limit: 2,
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
    previous_model: 'Previous model',
  })[opponent] ?? opponent;

export default function EvaluationSettings({
  config,
  update,
  disabled = false,
  tuning = false,
}: {
  config: TrainingConfig;
  update: (key: string, value: string | number | boolean) => void;
  disabled?: boolean;
  tuning?: boolean;
}) {
  const { t } = useI18n();
  const values = { ...evaluationDefaults, ...config };
  return (
    <fieldset className="training-evaluation" disabled={disabled}>
      <legend>{t('Evaluation')}</legend>
      <p className="subtle">
        {t(
          'Only the selected opponent is evaluated. The final iteration is also evaluated; checkpoint frequency is independent.',
        )}
      </p>
      <div className="form-grid">
        <label>
          {t('Evaluation opponent')}
          <select
            value={String(values.evaluation_opponent)}
            disabled={tuning}
            onChange={(event) => update('evaluation_opponent', event.target.value)}
          >
            <option value="shield_bearer">{t('Shield bearer')}</option>
            <option value="shield_maiden">{t('Shield maiden')}</option>
            <option value="jarl">{t('Jarl')}</option>
            <option value="champion">{t('Champion')}</option>
          </select>
        </label>
        {[
          'evaluation_frequency',
          'evaluation_games',
          'evaluation_max_plies',
          'evaluation_iterations',
          'evaluation_time_limit',
        ].map((key) => numberField(key, values, update))}
        {values.evaluation_opponent === 'jarl' &&
          ['evaluation_jarl_depth', 'evaluation_jarl_time_limit'].map((key) =>
            numberField(key, values, update),
          )}
        {values.evaluation_opponent === 'champion' &&
          ['champion_confidence', 'champion_min_pairs', 'champion_min_improvement'].map((key) =>
            numberField(key, values, update),
          )}
      </div>
      <p className="subtle">
        {t(
          tuning
            ? 'Optuna uses the fixed Shield bearer objective for comparable trials.'
            : values.evaluation_opponent === 'champion'
              ? 'Champion promotion requires complete side-swapped pairs and a confidence bound above 50% plus the required improvement. Small leads may be inconclusive.'
              : values.evaluation_opponent === 'shield_maiden'
                ? 'Shield maiden uses a stronger tactical heuristic, designed for low evaluation overhead.'
                : values.evaluation_opponent === 'jarl'
                  ? 'Jarl uses alpha-beta search. Depth and deadlines can make evaluation expensive.'
                  : 'Shield bearer is a heuristic benchmark for early training. Tied scores keep the earlier best checkpoint.',
        )}
      </p>
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
