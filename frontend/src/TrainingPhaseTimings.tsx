import { useI18n } from './i18n';
import { finite, type Values } from './trainingDashboardData';
export function PhaseTimings({ performance }: { performance?: Values }) {
  const { t, locale } = useI18n();
  const phases = [
    ['self_play_wall_seconds', 'Self-play'],
    ['optimization_seconds', 'Optimization'],
    ['evaluation_seconds', 'Evaluation'],
  ] as const;
  const available = phases.filter(([key]) => finite(performance?.[key]));
  if (!available.length)
    return <p className="subtle">{t('Phase timings will appear when recorded by the worker.')}</p>;
  const max = Math.max(
    ...available.map(([key]) => performance![key] as number),
    finite(performance?.iteration_wall_seconds) ? performance.iteration_wall_seconds : 0,
    0.001,
  );
  return (
    <section className="phase-timings" aria-label={t('Sequential phase timings')}>
      <h4>
        {t('Phase timings')} <small>{t('Last completed iteration')}</small>
      </h4>
      <div className="phase-comparison">
        {available.map(([key, label]) => (
          <div key={key}>
            <span>{t(label)}</span>
            <span className="phase-track">
              <i style={{ width: `${((performance![key] as number) / max) * 100}%` }} />
            </span>
            <strong>
              {new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(
                performance![key] as number,
              )}{' '}
              s
            </strong>
          </div>
        ))}
      </div>
    </section>
  );
}
