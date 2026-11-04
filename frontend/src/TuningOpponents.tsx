import { useId } from 'react';
import { useI18n } from './i18n';
import type { Model } from './types';
import { modelOptionLabel } from './modelLabel';
import { object } from './trainingDashboardData';
import { parseStudyStrategy } from './trainingPayload';

/** Same registry labels and native form controls used by agent/tournament selection.
 * The advanced JSON remains the single import/export representation. */
export default function TuningOpponents({
  models,
  ruleset,
  strategy,
  onChange,
  disabled,
}: {
  models: Model[];
  ruleset: string;
  strategy: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  let settings: Record<string, unknown>;
  try {
    settings = parseStudyStrategy(strategy);
  } catch {
    return (
      <p className="form-error" role="alert">
        {t('Correct the optional study JSON to edit fixed opponents.')}
      </p>
    );
  }
  if (
    settings.opponents !== undefined &&
    (!Array.isArray(settings.opponents) || settings.opponents.some((entry) => !object(entry)))
  ) {
    return (
      <p className="form-error" role="alert">
        {t('Correct the optional study JSON to edit fixed opponents.')}
      </p>
    );
  }
  const entries = Array.isArray(settings.opponents)
    ? (settings.opponents as Record<string, unknown>[])
    : [];
  const compatible = models.filter((model) => model.ruleset === ruleset);
  const commit = (next: Record<string, unknown>[]) =>
    onChange(JSON.stringify({ ...settings, opponents: next }, null, 2));
  return (
    <fieldset className="training-evaluation">
      <legend>{t('Fixed evaluation opponents')}</legend>
      <p className="subtle">
        {t(
          'Optional weighted objective: evaluate each trial’s best new candidate against these fixed references. Registered model weights stay fixed; network sizes may differ.',
        )}
      </p>
      {entries.map((entry, index) => {
        const value =
          entry.opponent === 'frozen_model' ? String(entry.model_id) : String(entry.opponent);
        const valid =
          entry.opponent !== 'frozen_model' ||
          compatible.some((model) => model.id === entry.model_id);
        return (
          <div className="form-grid" key={index}>
            <div className="training-field">
              <label htmlFor={`${id}-${index}`}>
                {t('Fixed opponent')} {index + 1}
              </label>
              <select
                id={`${id}-${index}`}
                value={valid ? value : ''}
                required
                disabled={disabled}
                aria-invalid={!valid || undefined}
                onChange={(event) =>
                  commit(
                    entries.map((item, position) =>
                      position === index
                        ? {
                            opponent: ['shield_bearer', 'shield_maiden', 'jarl'].includes(
                              event.target.value,
                            )
                              ? event.target.value
                              : 'frozen_model',
                            ...(!['shield_bearer', 'shield_maiden', 'jarl'].includes(
                              event.target.value,
                            )
                              ? { model_id: event.target.value }
                              : {}),
                            weight: entry.weight ?? 1,
                          }
                        : item,
                    ),
                  )
                }
              >
                <option value="" disabled>
                  {t('Select a compatible registered model or heuristic')}
                </option>
                <option value="shield_bearer">{t('Shield bearer')}</option>
                <option value="shield_maiden">{t('Shield maiden')}</option>
                <option value="jarl">{t('Jarl')}</option>
                {compatible.map((model) => (
                  <option key={model.id} value={model.id}>
                    {modelOptionLabel(model, t)}
                  </option>
                ))}
              </select>
              {!valid && (
                <p className="form-error" role="alert">
                  {t('The selected frozen model is missing or has an incompatible battlefield.')}
                </p>
              )}
            </div>
            <label className="training-field">
              {t('Opponent weight')} {index + 1}
              <input
                type="number"
                min="0.000001"
                max="1"
                step="any"
                required
                disabled={disabled}
                value={typeof entry.weight === 'number' ? entry.weight : ''}
                onChange={(event) =>
                  commit(
                    entries.map((item, position) =>
                      position === index
                        ? { ...item, weight: event.currentTarget.valueAsNumber }
                        : item,
                    ),
                  )
                }
              />
            </label>
            <button
              type="button"
              className="button secondary small"
              disabled={disabled}
              onClick={() => commit(entries.filter((_, position) => position !== index))}
            >
              {t('Remove opponent')} {index + 1}
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="button secondary small"
        disabled={disabled || entries.length >= 8}
        onClick={() => commit([...entries, { opponent: 'shield_bearer', weight: 1 }])}
      >
        {t('Add fixed opponent')}
      </button>
    </fieldset>
  );
}
