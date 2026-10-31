import { useI18n } from './i18n';
import type { Job } from './types';
import { jobProgress, object } from './trainingDashboardData';
import {
  formatDuration,
  phaseSummary,
  useLiveProgress,
  type HeartbeatState,
} from './trainingLiveProgress';

const HEARTBEAT_LABELS: Record<HeartbeatState, string> = {
  active: 'Worker active',
  long_operation: 'Running, long operation',
  stalled: 'Potentially stalled',
  unresponsive: 'No recent updates',
  waiting: 'Waiting for a worker',
  terminal: 'Finished',
};

interface Props {
  job: Job;
}

/**
 * Live elapsed time for the job header. This is deliberately a leaf component:
 * its one-second ticker must not re-render the dashboard's charts and tables.
 */
export function LiveElapsed({ job }: Props) {
  const { t } = useI18n();
  const elapsed = useLiveProgress(job).elapsed;
  if (elapsed === undefined) return null;
  return (
    <>
      {' · '}
      {t('Elapsed')} {formatDuration(elapsed)}
    </>
  );
}

/**
 * Live phase monitoring: continuously advancing phase time, phase-specific
 * counters and an independent heartbeat/progress state. Older jobs that predate
 * the monitoring fields simply render nothing here.
 */
export default function TrainingLiveStatus({ job }: Props) {
  const { t, locale } = useI18n();
  const progress = jobProgress(job);
  const live = useLiveProgress(job);
  const stage = typeof progress.stage === 'string' ? progress.stage : '';
  const summary = phaseSummary(stage, object(progress.phase_progress) ?? {}, locale);
  const heartbeat = live.heartbeat && live.heartbeat !== 'terminal' ? live.heartbeat : undefined;
  if (!heartbeat && live.phaseElapsed === undefined && !summary.rows.length) return null;
  return (
    <div className="live-phase" aria-label={t('Live phase progress')}>
      {heartbeat && (
        <span className={`job-heartbeat ${heartbeat}`} role="status">
          {t(HEARTBEAT_LABELS[heartbeat])}
        </span>
      )}
      {live.phaseElapsed !== undefined && (
        <span className="live-phase-time">
          {t('Phase elapsed {time}', { time: formatDuration(live.phaseElapsed) })}
        </span>
      )}
      {live.progressAge !== undefined && heartbeat && (
        <span className="live-phase-progress-age">
          {t('Last progress {seconds}s ago', { seconds: Math.floor(live.progressAge) })}
        </span>
      )}
      {summary.ratio !== undefined && (
        <progress
          className="live-phase-bar"
          value={summary.ratio}
          max={1}
          aria-label={t('Phase progress')}
        />
      )}
      {summary.rows.length > 0 && (
        <dl className="live-phase-counters">
          {summary.rows.map((row) => (
            <div key={row.label}>
              <dt>{t(row.label)}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
