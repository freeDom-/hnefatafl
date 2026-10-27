import { useEffect, useId, useMemo, useState } from 'react';
import { useI18n } from './i18n';
import Tabs from './Tabs';
import type { Job } from './types';
import { jobGameCounts } from './trainingGameCounts';
import {
  activeJob,
  finite,
  jobName,
  jobProgress,
  jobRuleset,
  trainingHistory,
  object,
  recentHistory,
} from './trainingDashboardData';
import {
  TrainingJobOverview,
  TrainingLearningView,
  TrainingEvaluationView,
  TrainingPerformance,
  TrainingJobDetails,
} from './TrainingAnalysisViews';
import { availableJobTabs, type JobTab } from './jobTabs';

interface Props {
  job: Job;
  tab: JobTab;
  onTabChange: (tab: JobTab) => void;
  revision: number;
  busy: boolean;
  exporting: boolean;
  onCancel: () => void;
  onResume: () => void;
  onExport: () => void;
  onRefresh: () => void;
  onDelete: () => void;
  onRename: () => void;
  onUseParameters: () => void;
  canUseParameters: boolean;
}
export default function TrainingJobDashboard({
  job,
  tab,
  onTabChange,
  revision,
  busy,
  exporting,
  onCancel,
  onResume,
  onExport,
  onRefresh,
  onDelete,
  onRename,
  onUseParameters,
  canUseParameters,
}: Props) {
  const { t, locale } = useI18n();
  const id = useId();
  const [range, setRange] = useState(0);
  const availableTabs = availableJobTabs(job);
  // The active tab is owned by the parent so it survives switching jobs. The
  // per-job iteration range stays local and resets for a new job or revision.
  useEffect(() => {
    setRange(0);
  }, [job.id, revision]);
  const progress = jobProgress(job);
  const games = jobGameCounts(job);
  const formatCount = (value: number | undefined) =>
    value === undefined ? t('Not recorded') : value.toLocaleString(locale);
  const history = useMemo(() => trainingHistory(progress.history), [progress.history]);
  const rangedHistory = useMemo(() => recentHistory(history, range), [history, range]);
  const config = object(job.config?.training) ?? job.config;
  const iteration = finite(progress.iteration) ? progress.iteration : history.at(-1)?.iteration;
  const total = progress.total_iterations ?? config?.iterations;
  const ratio =
    finite(iteration) && finite(total) && total > 0
      ? Math.max(0, Math.min(1, iteration / total))
      : undefined;
  const elapsed = progress.duration;
  const duration = finite(elapsed)
    ? `${Math.floor(elapsed / 3600) ? `${Math.floor(elapsed / 3600)}h ` : ''}${Math.floor((elapsed % 3600) / 60)}m ${Math.floor(elapsed % 60)}s`
    : undefined;
  return (
    <article className="job-detail training-job-dashboard" aria-label={t('Selected training job')}>
      <header className="selected-job-summary">
        <div className="dashboard-job-heading">
          <div>
            <span className="eyebrow">{t('Training progress')}</span>
            <h3>{jobName(job)}</h3>
          </div>
          <span className={`job-status ${job.status}`} role="status">
            {t(job.status)}
          </span>
        </div>
        <p className="job-worker">
          {jobRuleset(job)} ·{' '}
          {job.execution === 'remote'
            ? (job.worker_name ?? t('Waiting for an available worker'))
            : t('This server')}
          {duration && ` · ${t('Elapsed')} ${duration}`}
          {job.last_reported_at &&
            ` · ${t('Last update {time}', { time: new Date(job.last_reported_at).toLocaleString(locale) })}`}
        </p>
        {job.status === 'queued' && typeof progress.queue_reason === 'string' && (
          <p className="job-worker" role="status">
            {t(progress.queue_reason)}
          </p>
        )}
        <div className="dashboard-progress">
          <strong>
            {t('Iteration')} {iteration ?? '—'} / {finite(total) ? total : '—'}
          </strong>
          {ratio !== undefined && (
            <progress value={ratio} max={1} aria-label={t('Training progress')} />
          )}
          <span>
            {typeof progress.stage === 'string'
              ? t(progress.stage.replaceAll('_', ' '))
              : t(job.status)}
          </span>
        </div>
        <dl className="dashboard-game-counts" aria-label={t('Self-play games generated')}>
          <div>
            <dt>{t('Job games')}</dt>
            <dd>{formatCount(games.job)}</dd>
          </div>
          <div>
            <dt>{t('Cumulative training games')}</dt>
            <dd>{formatCount(games.cumulative)}</dd>
          </div>
        </dl>
        <div className="job-actions">
          <button className="button secondary small" onClick={onRename}>
            {t('Rename job')}
          </button>
          <button
            className="button secondary small"
            disabled={!canUseParameters}
            title={
              !canUseParameters
                ? t('No valid recorded training parameters are available for this job.')
                : undefined
            }
            onClick={onUseParameters}
          >
            {t('Use parameters')}
          </button>
          {activeJob(job) ? (
            <button
              className="button secondary small"
              disabled={job.status === 'cancelling'}
              onClick={onCancel}
            >
              {t(job.status === 'cancelling' ? 'Stopping…' : 'Stop training')}
            </button>
          ) : (
            <button
              className="button primary small"
              disabled={
                busy || (job.kind !== 'tune' && (!job.checkpoint || job.resumable === false))
              }
              onClick={onResume}
            >
              {t(job.kind === 'tune' ? 'Resume study' : 'Resume from checkpoint')}
            </button>
          )}
          {['completed', 'cancelled', 'failed'].includes(job.status) && (
            <button className="button secondary small" disabled={exporting} onClick={onExport}>
              {t(exporting ? 'Exporting job…' : 'Export job')}
            </button>
          )}
          {!activeJob(job) && (
            <button className="button secondary small danger-text" onClick={onDelete}>
              {t('Delete training data')}
            </button>
          )}
          <button className="text-button" onClick={onRefresh}>
            {t('Refresh job')}
          </button>
        </div>
        {job.error && (
          <p className="form-error" role="alert">
            {t(job.error)}
          </p>
        )}
      </header>
      <Tabs
        id={id}
        items={availableTabs.map((name) => ({ id: name, label: name }))}
        active={tab}
        onChange={(name) => onTabChange(name as JobTab)}
        label="Training analysis"
        className="training-tabs"
      />
      <section
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-tab-${tab}`}
        tabIndex={0}
        className="training-analysis-panel"
      >
        {!['Overview', 'Details'].includes(tab) && (
          <label className="training-range">
            {t('Iteration range')}
            <select
              aria-label={t('Iteration range')}
              value={range}
              onChange={(event) => setRange(Number(event.target.value))}
            >
              <option value={0}>{t('All iterations')}</option>
              {[10, 25, 50].map((value) => (
                <option key={value} value={value}>
                  {t('Recent {count} iterations', { count: value })}
                </option>
              ))}
            </select>
          </label>
        )}
        {tab === 'Overview' && (
          <TrainingJobOverview job={job} progress={progress} history={history} />
        )}
        {tab === 'Learning' && (
          <TrainingLearningView
            progress={progress}
            history={rangedHistory}
            config={config}
            range={0}
          />
        )}
        {tab === 'Evaluation' && (
          <TrainingEvaluationView progress={progress} history={rangedHistory} range={0} />
        )}
        {tab === 'Performance' && (
          <TrainingPerformance progress={{ ...progress, history: rangedHistory }} />
        )}
        {tab === 'Details' && <TrainingJobDetails job={job} progress={progress} />}
      </section>
    </article>
  );
}
