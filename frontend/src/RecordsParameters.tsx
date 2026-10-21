import { t, useI18n } from './i18n';
import type { Config } from './types';

const labels: Record<string, string> = {
  algorithm: 'Algorithm',
  depth: 'Search depth',
  iterations: 'Search iterations',
  rollout_depth: 'Rollout depth',
  time_limit: 'Time limit (seconds)',
  exploration: 'Exploration',
  temperature: 'Temperature',
  seed: 'Random seed',
  evaluation: 'Evaluation',
  model_id: 'Model ID',
};
const algorithms: Record<string, string> = {
  baseline: 'Tactical heuristic',
  alpha_beta: 'Alpha-beta',
  mcts: 'Monte Carlo tree search',
  neural: 'Neural search',
};

export function parameterSummary(config: Config = {}) {
  return [
    t(String(algorithms[String(config.algorithm)] ?? config.algorithm ?? '')),
    config.depth !== undefined ? t('depth {depth}', { depth: String(config.depth) }) : undefined,
    config.iterations !== undefined
      ? t('{iterations} iterations', { iterations: String(config.iterations) })
      : undefined,
    config.time_limit !== undefined ? `${config.time_limit}s` : undefined,
  ]
    .filter(Boolean)
    .join(' · ');
}

export default function RecordsParameters({
  config = {},
  label = 'AI parameters',
  variantId,
}: {
  config?: Config;
  label?: string;
  variantId?: string;
}) {
  useI18n();
  return (
    <details className="record-parameters">
      <summary>
        {t(label)}
        <span>{parameterSummary(config)}</span>
      </summary>
      <ParameterValues config={config} variantId={variantId} />
    </details>
  );
}

export function ParameterValues({
  config = {},
  variantId,
}: {
  config?: Config;
  variantId?: string;
}) {
  useI18n();
  const entries = Object.entries(config).sort(([a], [b]) => {
    const order = Object.keys(labels);
    const aIndex = order.indexOf(a),
      bIndex = order.indexOf(b);
    return (aIndex < 0 ? 100 : aIndex) - (bIndex < 0 ? 100 : bIndex) || a.localeCompare(b);
  });
  return (
    <>
      {entries.length ? (
        <dl>
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt>{t(labels[key] ?? key.replaceAll('_', ' '))}</dt>
              <dd>
                {key === 'algorithm'
                  ? t(algorithms[String(value)] ?? String(value))
                  : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="subtle">{t('No parameters were recorded for this historical opponent.')}</p>
      )}
      {variantId && (
        <p className="variant-reference">
          {t('Configuration ')}
          {variantId.slice(0, 10)}
        </p>
      )}
    </>
  );
}
