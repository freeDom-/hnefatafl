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
    algorithms[String(config.algorithm)] ?? config.algorithm,
    config.depth !== undefined ? `depth ${config.depth}` : undefined,
    config.iterations !== undefined ? `${config.iterations} iterations` : undefined,
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
  const entries = Object.entries(config).sort(([a], [b]) => {
    const order = Object.keys(labels);
    const aIndex = order.indexOf(a),
      bIndex = order.indexOf(b);
    return (aIndex < 0 ? 100 : aIndex) - (bIndex < 0 ? 100 : bIndex) || a.localeCompare(b);
  });
  return (
    <details className="record-parameters">
      <summary>
        {label}
        <span>{parameterSummary(config)}</span>
      </summary>
      {entries.length ? (
        <dl>
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt>{labels[key] ?? key.replaceAll('_', ' ')}</dt>
              <dd>
                {key === 'algorithm' ? (algorithms[String(value)] ?? String(value)) : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="subtle">No parameters were recorded for this historical opponent.</p>
      )}
      {variantId && <p className="variant-reference">Configuration {variantId.slice(0, 10)}</p>}
    </details>
  );
}
