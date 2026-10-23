import { useI18n } from './i18n';

type Values = Record<string, unknown>;
const object = (value: unknown): Values | undefined =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Values)
    : undefined;
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export default function TrainingPerformance({ progress }: { progress: unknown }) {
  const { t, locale } = useI18n();
  const state = object(progress);
  const history = Array.isArray(state?.history)
    ? state.history.filter(
        (row): row is Values => !!object(row) && finite(row.iteration) && !!object(row.performance),
      )
    : [];
  const performance =
    object(state?.performance) ??
    object(object(state?.metrics)?.performance) ??
    object(history.at(-1)?.performance);
  if (!performance) return null;
  const format = (value: unknown) =>
    finite(value) ? new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(value) : '—';
  const fields = (entries: [string, string, number?][]) =>
    entries
      .filter(([key]) => finite(performance[key]))
      .map(([key, label, divisor = 1]) => (
        <div key={key}>
          <span>{t(label)}</span>
          <strong>{format(Number(performance[key]) / divisor)}</strong>
        </div>
      ));
  return (
    <section aria-label={t('Training performance')}>
      <h4>{t('Training performance')}</h4>
      {typeof performance.self_play_mode === 'string' && (
        <p>
          {t('Self-play execution')}:{' '}
          {t(performance.self_play_mode === 'batched' ? 'Batched' : 'Sequential')}
        </p>
      )}
      <h5>{t('Self-play throughput')}</h5>
      <div className="job-metrics">
        {fields([
          ['games_per_hour', 'Games / hour'],
          ['positions_per_second', 'Positions / second'],
          ['simulations_per_second', 'Simulations / second'],
          ['neural_evaluations_per_second', 'Neural evaluations / second'],
          ['average_game_length', 'Average game length'],
          ['completed_games', 'Completed games (including draws)'],
          ['attacker_wins', 'Attacker wins'],
          ['defender_wins', 'Defender wins'],
          ['drawn_games', 'Drawn games'],
          ['requested_mcts_simulations', 'Requested MCTS simulations'],
          ['mcts_simulations', 'Completed MCTS simulations'],
          ['immediate_win_shortcuts', 'Immediate-win shortcuts'],
          ['search_deadline_cutoffs', 'Search deadline cutoffs'],
          ['truncated_games', 'Truncated games'],
        ])}
      </div>
      <h5>{t('Inference batching')}</h5>
      <div className="job-metrics">
        <div>
          <span>{t('Actual inference batch size (average / min / max)')}</span>
          <strong>
            {[
              'inference_batch_size_average',
              'inference_batch_size_min',
              'inference_batch_size_max',
            ]
              .map((key) => format(performance[key]))
              .join(' / ')}
          </strong>
        </div>
        {fields([
          ['inference_batches', 'Inference batches'],
          ['inference_batch_latency_seconds', 'Average batch latency (seconds)'],
          ['gpu_peak_allocated_bytes', 'Peak GPU allocated memory (MiB)', 1024 ** 2],
          ['gpu_peak_reserved_bytes', 'Peak GPU reserved memory (MiB)', 1024 ** 2],
        ])}
      </div>
      <h5>{t('Phase timings')}</h5>
      <p className="subtle">
        {t(
          'Wall-clock durations and accumulated activity times are reported separately; activities can overlap.',
        )}
      </p>
      <div className="job-metrics">
        {fields([
          ['self_play_wall_seconds', 'Self-play wall time (seconds)'],
          ['cpu_search_seconds', 'CPU search activity (seconds)'],
          ['inference_time_seconds', 'Inference host activity (seconds)'],
          ['inference_cuda_seconds', 'Inference CUDA activity (seconds)'],
          ['optimization_seconds', 'Gradient optimization (seconds)'],
          ['evaluation_seconds', 'Opponent evaluation (seconds)'],
          ['iteration_wall_seconds', 'Iteration wall time (seconds)'],
        ])}
      </div>
      {history.length > 0 && (
        <details>
          <summary>{t('Throughput history')}</summary>
          <div className="table-scroll">
            <table aria-label={t('Throughput history')}>
              <thead>
                <tr>
                  {[
                    'Iteration',
                    'Self-play execution',
                    'Games / hour',
                    'Positions / second',
                    'Simulations / second',
                    'Inference batch size',
                    'Self-play wall time (seconds)',
                  ].map((label) => (
                    <th key={label}>{t(label)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((row, index) => {
                  const values = object(row.performance)!;
                  return (
                    <tr key={`${row.iteration}-${index}`}>
                      <td>{format(row.iteration)}</td>
                      <td>
                        {values.self_play_mode === 'batched'
                          ? t('Batched')
                          : values.self_play_mode === 'sequential'
                            ? t('Sequential')
                            : '—'}
                      </td>
                      {[
                        'games_per_hour',
                        'positions_per_second',
                        'simulations_per_second',
                        'inference_batch_size_average',
                        'self_play_wall_seconds',
                      ].map((key) => (
                        <td key={key}>{format(values[key])}</td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
