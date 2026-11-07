import { useMemo, useState } from 'react';
import { useI18n } from './i18n';
import TrainingMetricChart from './TrainingMetricChart';
import TrainingDataDisclosure from './TrainingDataDisclosure';
import { PhaseTimings } from './TrainingPhaseTimings';
import {
  object,
  finite,
  latestPerformance,
  trainingHistory,
  type HistoryPoint,
  type Values,
} from './trainingDashboardData';

/**
 * Selectable Performance metrics. Each entry maps to one stored self-play
 * field; the win rates are the only derived entries, because the pipeline
 * records the underlying side wins and completed games instead of a rate.
 */
export interface PerformanceMetric {
  key: string;
  label: string;
  unit: string;
  /** Percentage values are already fractions, so formatting must not scale twice. */
  percent?: boolean;
  derive?: (performance: Values) => number | undefined;
  /** A stored value must satisfy this predicate to be displayed as-is. */
  valid?: (value: number, performance: Values) => boolean;
}

/** A recorded game count: finite, whole and non-negative. */
const validCount = (value: unknown): value is number =>
  finite(value) && Number.isInteger(value) && value >= 0;

/** Outcome counters that describe a completed game and must stay consistent. */
const outcomeKeys = ['attacker_wins', 'defender_wins', 'drawn_games'] as const;
/** Any stored counter means the row carries underlying outcome data. */
const counterKeys = ['completed_games', ...outcomeKeys] as const;

/** Missing (absent or null) stays distinct from present-but-invalid. */
const hasValue = (performance: Values, key: string): boolean =>
  performance[key] !== undefined && performance[key] !== null;

/** A present completed total that is not a valid count rejects the record. */
const hasInvalidCompletedTotal = (performance: Values): boolean =>
  hasValue(performance, 'completed_games') && !validCount(performance.completed_games);

/**
 * Completed games include rules draws and exclude truncated games, matching the
 * stored metric. A genuinely missing total is rebuilt from a complete and valid
 * outcome breakdown; an explicitly present but invalid total is never rebuilt.
 */
export const completedGames = (performance: Values): number | undefined => {
  const completed = performance.completed_games;
  if (validCount(completed)) return completed;
  if (hasInvalidCompletedTotal(performance)) return undefined;
  const wins = performance.attacker_wins;
  const losses = performance.defender_wins;
  const draws = performance.drawn_games;
  if (!validCount(wins) || !validCount(losses) || !validCount(draws)) return undefined;
  return wins + losses + draws;
};

/**
 * Validate every present outcome counter against the shared denominator: each
 * must be a finite nonnegative integer that fits completed games, the sum of
 * the present counters must not exceed it, and when all three are present their
 * sum must equal it. Missing counters are never treated as zero, a present but
 * malformed counter makes the whole row inconsistent, and so does an explicitly
 * present but invalid completed total.
 */
const validateOutcomes = (performance: Values): { completed?: number; consistent: boolean } => {
  const completed = completedGames(performance);
  let present = 0;
  let sum = 0;
  let consistent = !hasInvalidCompletedTotal(performance);
  for (const key of outcomeKeys) {
    if (!hasValue(performance, key)) continue;
    present += 1;
    const value = performance[key];
    if (!validCount(value) || (completed !== undefined && value > completed)) {
      consistent = false;
      continue;
    }
    sum += value;
  }
  if (completed !== undefined) {
    if (sum > completed) consistent = false;
    if (present === outcomeKeys.length && sum !== completed) consistent = false;
  }
  return { completed, consistent };
};

/** A measured metric: finite and never negative. */
const validNonnegative = (value: number): boolean => finite(value) && value >= 0;

/**
 * A stored count that participates in a fully consistent outcome breakdown.
 * Reuses `validateOutcomes` so a count is rejected whenever its row is
 * contradictory, exactly like the matching win-rate derivation.
 */
const validOutcomeCount = (value: number, performance: Values): boolean =>
  validCount(value) && validateOutcomes(performance).consistent;

/** True when the row carries any stored counter, valid or not. */
const hasCounters = (performance: Values): boolean =>
  counterKeys.some((key) => hasValue(performance, key));

/**
 * Side win rate over completed games. Invalid or inconsistent counters are
 * treated as missing rather than coerced, and the result stays within [0, 1].
 */
const sideWinRate =
  (key: 'attacker_wins' | 'defender_wins') =>
  (performance: Values): number | undefined => {
    const wins = performance[key];
    if (!validCount(wins)) return undefined;
    const { completed, consistent } = validateOutcomes(performance);
    if (!consistent || completed === undefined || completed <= 0 || wins > completed)
      return undefined;
    const rate = wins / completed;
    return rate >= 0 && rate <= 1 ? rate : undefined;
  };

export const performanceMetrics: PerformanceMetric[] = [
  { key: 'simulations_per_second', label: 'Simulations / second', unit: 'Simulations / second' },
  { key: 'positions_per_second', label: 'Positions / second', unit: 'Positions / second' },
  {
    key: 'inference_batch_size_average',
    label: 'Inference batch size',
    unit: 'Inference batch size',
  },
  {
    key: 'evaluation_seconds',
    label: 'Opponent evaluation (seconds)',
    unit: 'Opponent evaluation (seconds)',
  },
  {
    key: 'iteration_wall_seconds',
    label: 'Iteration wall time (seconds)',
    unit: 'Iteration wall time (seconds)',
  },
  {
    key: 'neural_evaluations_per_second',
    label: 'Neural evaluations / second',
    unit: 'Neural evaluations / second',
  },
  {
    key: 'games_per_hour',
    label: 'Games / hour',
    unit: 'Games / hour',
    valid: validNonnegative,
  },
  {
    key: 'average_game_length',
    label: 'Average game length',
    unit: 'Moves',
    valid: validNonnegative,
  },
  {
    key: 'attacker_wins',
    label: 'Attacker wins',
    unit: 'Games',
    valid: validOutcomeCount,
  },
  {
    key: 'attacker_win_rate',
    label: 'Attacker win rate',
    unit: 'Win rate',
    percent: true,
    derive: sideWinRate('attacker_wins'),
  },
  {
    key: 'defender_wins',
    label: 'Defender wins',
    unit: 'Games',
    valid: validOutcomeCount,
  },
  {
    key: 'defender_win_rate',
    label: 'Defender win rate',
    unit: 'Win rate',
    percent: true,
    derive: sideWinRate('defender_wins'),
  },
];

/**
 * Flatten each history row's stored `performance` snapshot into chart columns.
 * Count-based values are validated, and a rate prefers the canonical value
 * derived from the underlying counters. A valid stored fraction is kept only
 * when the row carries no counters at all, so ambiguous units are never
 * rescaled and contradictory stored rates are never shown.
 */
export function performanceChartRows(history: HistoryPoint[]): HistoryPoint[] {
  return history.map((row) => {
    const values: HistoryPoint = { ...row, ...object(row.performance) };
    // Validation reads the raw counters before invalid ones are removed from the
    // chart columns, so present-but-invalid stays distinct from missing.
    const source: Values = { ...values };
    for (const item of performanceMetrics) {
      if (item.valid) {
        const stored = values[item.key];
        if (!(finite(stored) && item.valid(stored, source))) delete values[item.key];
        continue;
      }
      if (!item.derive) continue;
      const stored = values[item.key];
      const derived = item.derive(source);
      if (derived !== undefined) {
        values[item.key] = derived;
        continue;
      }
      // Counters were provided but could not produce a canonical rate, so an
      // unverifiable stored fraction must not be shown. Only a row with no
      // underlying counters keeps its valid historical fraction.
      if (!hasCounters(source) && finite(stored) && item.percent && stored >= 0 && stored <= 1)
        continue;
      delete values[item.key];
    }
    return values;
  });
}
export default function TrainingPerformance({ progress }: { progress: unknown }) {
  const { t, locale } = useI18n();
  const state = object(progress) ?? {};
  const history = useMemo(() => trainingHistory(state.history), [state.history]);
  const [metric, setMetric] = useState<string>('simulations_per_second');
  const performance = latestPerformance(state, history);
  const graphRows = useMemo(() => performanceChartRows(history), [history]);
  const selectedMetric =
    performanceMetrics.find((item) => item.key === metric) ?? performanceMetrics[0];
  const label = selectedMetric.label;
  const series = useMemo(
    () => [{ key: metric, name: label, color: 'var(--chart-value)' }],
    [metric, label],
  );
  if (!performance)
    return <p className="subtle">{t('Performance metrics were not recorded by this worker.')}</p>;
  const format = (value: unknown) =>
    finite(value) ? new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(value) : '—';
  const fields = (entries: [string, string, number?][]) =>
    entries.map(([key, label, divisor = 1]) => (
      <div key={key}>
        <dt>{t(label)}</dt>
        <dd>{finite(performance[key]) ? format(performance[key] / divisor) : '—'}</dd>
      </div>
    ));
  const keys = [...new Set(history.flatMap((row) => Object.keys(object(row.performance) ?? {})))];
  return (
    <section className="training-performance" aria-label={t('Training performance')}>
      <h4>{t('Training performance')}</h4>
      {typeof performance.inference_backend === 'string' && (
        <p>
          {t('Training device')}: {String(performance.optimization_device ?? state.device ?? '—')} ·{' '}
          {t('Neural inference backend')}: {performance.inference_backend} ·{' '}
          {String(performance.inference_device_name ?? performance.inference_device ?? '—')}
        </p>
      )}
      {typeof performance.evaluation_inference_backend === 'string' && (
        <p>
          {t('Evaluation inference')}: {String(performance.evaluation_inference_backend)} ·{' '}
          {String(performance.evaluation_inference_device)}
        </p>
      )}
      {typeof performance.self_play_mode === 'string' && (
        <p>
          {t('Self-play execution')}:{' '}
          {t(
            performance.self_play_mode === 'batched'
              ? 'Batched'
              : performance.self_play_mode === 'parallel'
                ? 'Parallel'
                : 'Sequential',
          )}
        </p>
      )}
      <label className="performance-metric-control">
        {t('Performance metric')}
        <select
          aria-label={t('Performance metric')}
          value={metric}
          onChange={(event) => setMetric(event.target.value)}
        >
          {performanceMetrics.map((item) => (
            <option key={item.key} value={item.key}>
              {t(item.label)}
            </option>
          ))}
        </select>
      </label>
      <TrainingMetricChart
        title={label}
        rows={graphRows}
        series={series}
        unit={selectedMetric.unit}
        percent={selectedMetric.percent}
        gaps
      />
      <div className="performance-groups">
        <section>
          <h5>{t('Self-play throughput')}</h5>
          <dl className="job-metrics">
            {fields([
              ['games_per_hour', 'Games / hour'],
              ['positions_per_second', 'Positions / second'],
              ['simulations_per_second', 'Simulations / second'],
              ['neural_evaluations_per_second', 'Neural evaluations / second'],
              ['average_game_length', 'Average game length'],
            ])}
          </dl>
        </section>
        <section>
          <h5>MCTS</h5>
          <dl className="job-metrics">
            {fields([
              ['requested_mcts_simulations', 'Requested MCTS simulations'],
              ['mcts_simulations', 'Completed MCTS simulations'],
              ['mcts_neural_requests', 'MCTS neural requests'],
              ['mcts_node_prediction_reuses', 'MCTS node predictions reused'],
              ['immediate_win_shortcuts', 'Immediate-win shortcuts'],
              ['search_deadline_cutoffs', 'Search deadline cutoffs'],
              ['completed_games', 'Completed games (including draws)'],
              ['attacker_wins', 'Attacker wins'],
              ['defender_wins', 'Defender wins'],
              ['drawn_games', 'Drawn games'],
              ['truncated_games', 'Truncated games'],
            ])}
            <div>
              <dt>{t('Simulation completion ratio')}</dt>
              <dd>
                {finite(performance.mcts_simulations) &&
                finite(performance.requested_mcts_simulations) &&
                performance.requested_mcts_simulations > 0
                  ? new Intl.NumberFormat(locale, {
                      style: 'percent',
                      maximumFractionDigits: 1,
                    }).format(performance.mcts_simulations / performance.requested_mcts_simulations)
                  : '—'}
              </dd>
            </div>
          </dl>
        </section>
        <section>
          <h5>{t('GPU and inference')}</h5>
          <dl className="job-metrics">
            <div>
              <dt>{t('Actual inference batch size (average / min / max)')}</dt>
              <dd>
                {[
                  'inference_batch_size_average',
                  'inference_batch_size_min',
                  'inference_batch_size_max',
                ]
                  .map((key) => format(performance[key]))
                  .join(' / ')}
              </dd>
            </div>
            {fields([
              ['inference_batches', 'Inference batches'],
              ['inference_requests', 'Inference requests'],
              ['neural_evaluations', 'Neural evaluations'],
              ['inference_cache_hits', 'Inference cache hits'],
              ['inference_cache_misses', 'Inference cache misses'],
              ['inference_batch_duplicates', 'Batch duplicates eliminated'],
              ['inference_cache_hit_rate', 'Inference cache hit rate'],
              ['inference_cache_entries', 'Inference cache entries'],
              ['inference_cache_peak_entries', 'Peak inference cache entries'],
              ['inference_cache_lookup_seconds', 'Cache lookup (seconds)'],
              ['inference_initialization_seconds', 'Inference initialization (seconds)'],
              ['inference_conversion_seconds', 'Model conversion (seconds)'],
              ['inference_compilation_seconds', 'Model compilation and warm-up (seconds)'],
              ['inference_refresh_seconds', 'Total inference refresh (seconds)'],
              ['inference_batch_latency_seconds', 'Average batch latency (seconds)'],
              ['gpu_peak_allocated_bytes', 'Peak GPU allocated memory (MiB)', 1024 ** 2],
              ['gpu_peak_reserved_bytes', 'Peak GPU reserved memory (MiB)', 1024 ** 2],
            ])}
          </dl>
        </section>
        <section>
          <PhaseTimings performance={object(history.at(-1)?.performance) ?? performance} />
          <dl className="job-metrics">
            {fields([
              ['self_play_wall_seconds', 'Self-play wall time (seconds)'],
              ['optimization_seconds', 'Gradient optimization (seconds)'],
              ['augmentation_seconds', 'Symmetry augmentation (seconds)'],
              ['evaluation_seconds', 'Opponent evaluation (seconds)'],
              ['iteration_wall_seconds', 'Iteration wall time (seconds)'],
            ])}
          </dl>
        </section>
      </div>
      {typeof performance.augmentation_mode === 'string' && (
        <>
          <h5>{t('Training symmetry augmentation')}</h5>
          <dl className="job-metrics">
            <div>
              <dt>{t('Mode')}</dt>
              <dd>{t(performance.augmentation_mode === 'random_d4' ? 'Random D4' : 'Disabled')}</dd>
            </div>
            {fields([
              ['training_exposures', 'Training exposures'],
              ['augmentation_exposures', 'Augmented exposures'],
              ['augmentation_identity', 'Identity transformations'],
              ['augmentation_optimization_share', 'Share of optimization time'],
            ])}
          </dl>
        </>
      )}
      <h5>{t('Overlapping activity counters')}</h5>
      <p className="subtle">
        {t(
          'Wall-clock durations and accumulated activity times are reported separately; activities can overlap.',
        )}
      </p>
      <dl className="job-metrics">
        {fields([
          ['cpu_search_seconds', 'CPU search activity (seconds)'],
          ['inference_time_seconds', 'Inference host activity (seconds)'],
          ['inference_cuda_seconds', 'Inference CUDA activity (seconds)'],
        ])}
      </dl>
      {!!history.length && (
        <TrainingDataDisclosure title="Throughput history">
          {() => (
            <div className="table-scroll bounded-table">
              <table aria-label={t('Throughput history')}>
                <thead>
                  <tr>
                    <th scope="col">{t('Iteration')}</th>
                    {keys.map((key) => (
                      <th scope="col" key={key}>
                        {t(
                          performanceMetrics.find((item) => item.key === key)?.label ??
                            key.replaceAll('_', ' '),
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {history.map((row) => (
                    <tr key={row.iteration}>
                      <th scope="row">{row.iteration}</th>
                      {keys.map((key) => (
                        <td key={key}>
                          {typeof object(row.performance)?.[key] === 'string'
                            ? t(String(object(row.performance)?.[key]))
                            : format(object(row.performance)?.[key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </TrainingDataDisclosure>
      )}
    </section>
  );
}
