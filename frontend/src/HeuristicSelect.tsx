import { useI18n } from './i18n';
import type { Config } from './types';

export const HEURISTIC_VALUES = ['shield_maiden', 'shield_bearer'] as const;
export type HeuristicValue = (typeof HEURISTIC_VALUES)[number];

/** The evaluator used by model-free alpha-beta/MCTS; defaults to Shield maiden. */
export function heuristicFromConfig(config: Config | undefined): HeuristicValue {
  return config?.heuristic === 'shield_bearer' ? 'shield_bearer' : 'shield_maiden';
}

export default function HeuristicSelect({
  value,
  label,
  onChange,
}: {
  value: string;
  label: string;
  onChange: (value: HeuristicValue) => void;
}) {
  const { t } = useI18n();
  return (
    <label>
      {t('Heuristic')}
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value as HeuristicValue)}
      >
        <option value="shield_maiden">{t('Shield maiden')}</option>
        <option value="shield_bearer">{t('Shield bearer')}</option>
      </select>
    </label>
  );
}
