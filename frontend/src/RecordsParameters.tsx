import { t, useI18n } from './i18n';
import { isAlphaBetaSearch, searchSummary, type AlphaBetaSearchSettings } from './alphaBetaSearch';
import type { Config } from './types';

const labels: Record<string, string> = {
  algorithm: 'Algorithm',
  heuristic: 'Heuristic',
  heuristic_version: 'Heuristic version',
  search_wrapper: 'Search wrapper',
  depth: 'Search depth',
  iterations: 'Search iterations',
  rollout_depth: 'Rollout depth',
  time_limit: 'Time limit (seconds)',
  exploration: 'Exploration',
  temperature: 'Temperature',
  seed: 'Random seed',
  evaluation: 'Evaluation',
  model_id: 'Model ID',
  search: 'Alpha-beta optimizations',
};
const algorithms: Record<string, string> = {
  baseline: 'Tactical heuristic',
  shield_maiden: 'Shield maiden heuristic',
  alpha_beta: 'Alpha-beta',
  mcts: 'Monte Carlo tree search',
  neural: 'Neural search',
};
const searchLabels: Record<keyof AlphaBetaSearchSettings, string> = {
  legacy: 'Legacy search',
  transposition: 'Transposition table',
  move_ordering: 'Improved move ordering',
  symmetry_transposition: 'D4-canonical transpositions',
  symmetry_pruning: 'Symmetry move pruning',
  table_capacity: 'Transposition table capacity',
};

export function parameterSummary(config: Config = {}) {
  const search = isAlphaBetaSearch(config.search) ? config.search : undefined;
  return [
    t(String(algorithms[String(config.algorithm)] ?? config.algorithm ?? '')),
    config.search_wrapper === 'alpha_beta'
      ? t('Alpha-beta')
      : config.search_wrapper === 'none'
        ? t('None (policy only)')
        : undefined,
    search ? t(searchSummary(search)) : undefined,
    config.heuristic !== undefined
      ? t(config.heuristic === 'shield_bearer' ? 'Shield bearer' : 'Shield maiden')
      : undefined,
    config.depth !== undefined ? t('depth {depth}', { depth: String(config.depth) }) : undefined,
    config.iterations !== undefined
      ? t('{iterations} iterations', { iterations: String(config.iterations) })
      : undefined,
    config.time_limit !== undefined ? `${config.time_limit}s` : undefined,
  ]
    .filter(Boolean)
    .join(' · ');
}

function SearchValues({ search }: { search: AlphaBetaSearchSettings }) {
  const entries = (Object.keys(searchLabels) as (keyof AlphaBetaSearchSettings)[]).filter(
    (key) => key !== 'table_capacity',
  );
  return (
    <dl className="record-search-parameters">
      {entries.map((key) => (
        <div key={key}>
          <dt>{t(searchLabels[key])}</dt>
          <dd>{search[key] ? t('On') : t('Off')}</dd>
        </div>
      ))}
      <div>
        <dt>{t(searchLabels.table_capacity)}</dt>
        <dd>{search.table_capacity.toLocaleString()}</dd>
      </div>
    </dl>
  );
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
                {key === 'algorithm' ? (
                  t(algorithms[String(value)] ?? String(value))
                ) : key === 'heuristic' ? (
                  t(value === 'shield_bearer' ? 'Shield bearer' : 'Shield maiden')
                ) : key === 'search_wrapper' ? (
                  t(
                    value === 'none'
                      ? 'None (policy only)'
                      : (algorithms[String(value)] ?? String(value)),
                  )
                ) : key === 'search' && isAlphaBetaSearch(value) ? (
                  <SearchValues search={value} />
                ) : (
                  String(value)
                )}
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
