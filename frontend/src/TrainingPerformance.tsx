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
}

/** Completed games include rules draws, matching the stored metric and personal statistics. */
export const completedGames = (performance: Values) => {
  if (finite(performance.completed_games)) return performance.completed_games;
  const wins = performance.attacker_wins;
  const losses = performance.defender_wins;
  const draws = performance.drawn_games;
  return finite(wins) && finite(losses) && finite(draws) ? wins + losses + draws : undefined;
};

const sideWinRate =
  (key: 'attacker_wins' | 'defender_wins') =>
  (performance: Values): number | undefined => {
    const wins = performance[key];
    const completed = completedGames(performance);
    return finite(wins) && completed !== undefined && completed > 0 ? wins / completed : undefined;
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
  { key: 'games_per_hour', label: 'Games / hour', unit: 'Games / hour' },
  { key: 'average_game_length', label: 'Average game length', unit: 'Moves' },
  { key: 'attacker_wins', label: 'Attacker wins', unit: 'Games' },
  {
    key: 'attacker_win_rate',
    label: 'Attacker win rate',
    unit: 'Win rate',
    percent: true,
    derive: sideWinRate('attacker_wins'),
  },
  { key: 'defender_wins', label: 'Defender wins', unit: 'Games' },
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
 * Derived entries are only filled when the backend did not store them, and a
 * missing or invalid source leaves the key absent instead of plotting a zero.
 */
export function performanceChartRows(history: HistoryPoint[]): HistoryPoint[] {
  return history.map((row) => {
    const values: HistoryPoint = { ...row, ...object(row.performance) };
    for (const item of performanceMetrics) {
      if (item.derive && !finite(values[item.key])) {
        const derived = item.derive(values);
        if (derived !== undefined) values[item.key] = derived;
      }
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
