import { useI18n } from './i18n';
import { finite, object } from './trainingDashboardData';

/** Bounded scalar diagnostics; learning/performance graph meanings stay intact. */
export default function TrainingOptimizerStatus({
  progress,
}: {
  progress: Record<string, unknown>;
}) {
  const { t } = useI18n();
  const optimizer = object(progress.optimizer) ?? object(object(progress.metrics)?.optimizer);
  if (optimizer?.name !== 'adamw_schedulefree') return null;
  const rows: [string, unknown][] = [
    ['Base learning rate', optimizer.base_learning_rate],
    ['Warm-up learning rate (last update)', optimizer.warmup_learning_rate],
    ['Completed optimizer steps', optimizer.completed_steps],
    ['Schedule-Free warm-up steps', optimizer.warmup_steps],
    [
      'Warm-up progress',
      finite(optimizer.warmup_progress)
        ? `${Math.round(100 * optimizer.warmup_progress)}%`
        : undefined,
    ],
  ];
  return (
    <div className="live-phase" aria-label={t('Schedule-Free AdamW')}>
      <span>{t('Schedule-Free AdamW')}</span>
      <dl className="live-phase-counters">
        {rows
          .filter(([, value]) => finite(value) || typeof value === 'string')
          .map(([label, value]) => (
            <div key={label}>
              <dt>{t(label)}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
      </dl>
    </div>
  );
}
