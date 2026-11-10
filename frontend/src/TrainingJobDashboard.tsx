import { useEffect, useId, useMemo, useState } from 'react';
import { useI18n } from './i18n';
import Tabs from './Tabs';
import type { Job } from './types';
import { jobGameCounts } from './trainingGameCounts';
import {
  activeJob,
  finite,
  iterationRanges,
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
  TrainingTuningView,
} from './TrainingAnalysisViews';
import TrainingLiveStatus, { LiveElapsed } from './TrainingLiveStatus';
import TrainingOptimizerStatus from './TrainingOptimizerStatus';
import { availableJobTabs, type JobTab } from './jobTabs';

interface Props {
  job: Job;
  /** Monotonic time of the last successful monitoring read, for live freshness. */
  observedAt?: number;
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
  onDeleteStudy?: () => void;
  onRename: () => void;
  onUseParameters: () => void;
  onTuneCheckpoint: () => void;
  canUseParameters: boolean;
  trainingJobs: Job[];
  onUseBestParameters: () => void;
  onContinueTrial: (additionalIterations: number) => void;
  onApplyToJob: (targetJobId: string, additionalIterations: number) => void;
  onDownloadBestConfig: () => void;
}
export default function TrainingJobDashboard({
  job,
  observedAt,
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
  onDeleteStudy,
  onRename,
  onUseParameters,
  onTuneCheckpoint,
  canUseParameters,
  trainingJobs,
  onUseBestParameters,
  onContinueTrial,
  onApplyToJob,
  onDownloadBestConfig,
}: Props) {
  const { t, locale } = useI18n();
  const id = useId();
  const [range, setRange] = useState(0);
  const [evaluationRangeMode, setEvaluationRangeMode] = useState<'shared' | 'fit-series'>('shared');
  const fitEvaluationSeries = tab === 'Evaluation' && evaluationRangeMode === 'fit-series';
  const availableTabs = availableJobTabs(job);
  // The active tab is owned by the parent so it survives switching jobs. The
  // per-job iteration range stays local and resets for a new job or revision.
  useEffect(() => {
    setRange(0);
    setEvaluationRangeMode('shared');
  }, [job.id, revision]);
  const progress = jobProgress(job);
  const games = jobGameCounts(job);
  const formatCount = (value: number | undefined) =>
    value === undefined ? t('Not recorded') : value.toLocaleString(locale);
  const history = useMemo(() => trainingHistory(progress.history), [progress.history]);
  const rangedHistory = useMemo(() => recentHistory(history, range), [history, range]);
  const config = object(job.config?.training) ?? job.config;
  // The live header follows the iteration currently being trained; the recorded
  // iteration remains the last completed one for the analysis views.
  const recorded = finite(progress.iteration) ? progress.iteration : history.at(-1)?.iteration;
  const iteration = finite(progress.active_iteration) ? progress.active_iteration : recorded;
  const total = progress.total_iterations ?? config?.iterations;
  const ratio =
    finite(iteration) && finite(total) && total > 0
      ? Math.max(0, Math.min(1, iteration / total))
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
          <LiveElapsed job={job} observedAt={observedAt} />
          {job.last_reported_at &&
            ` · ${t('Last update {time}', { time: new Date(job.last_reported_at).toLocaleString(locale) })}`}
        </p>
        {job.status === 'queued' && typeof progress.queue_reason === 'string' && (
          <p className="job-worker" role="status">
            {t(progress.queue_reason)}
          </p>
        )}
        {job.status === 'completed' && job.result?.model_reused === true && (
          <p className="job-worker" role="status">
            {t(
              job.result?.produced_best === true
                ? 'The trained weights already matched a registered model, which was reused.'
                : job.result?.produced_best === false
                  ? 'No new best model was produced. The existing registered model was reused.'
                  : 'The existing registered model was reused.',
            )}
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
        <TrainingLiveStatus job={job} observedAt={observedAt} />
        <TrainingOptimizerStatus progress={progress} />
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
          {job.kind === 'train' && !activeJob(job) && job.checkpoint && job.resumable !== false && (
            <button className="button secondary small" disabled={busy} onClick={onTuneCheckpoint}>
              {t('Tune this checkpoint')}
            </button>
          )}
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
          {!activeJob(job) && job.kind === 'tune' && onDeleteStudy && (
            <button
              className="button secondary small danger-text"
              disabled={busy}
              onClick={onDeleteStudy}
            >
              {t('Delete entire tuning study')}
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
        aria-busy={job.summary || undefined}
      >
        {job.summary && <p role="status">{t('Loading history…')}</p>}
        {!job.summary && !['Overview', 'Details', 'Tuning results'].includes(tab) && (
          <label className="training-range">
            {t('Iteration range')}
            <select
              aria-label={t('Iteration range')}
              value={fitEvaluationSeries ? 'fit-series' : range}
              onChange={(event) => {
                if (event.target.value === 'fit-series') {
                  setEvaluationRangeMode('fit-series');
                } else {
                  setEvaluationRangeMode('shared');
                  setRange(Number(event.target.value));
                }
              }}
            >
              <option value={0}>{t('All iterations')}</option>
              {tab === 'Evaluation' && <option value="fit-series">{t('Fit series')}</option>}
              {iterationRanges.map((value) => (
                <option key={value} value={value}>
                  {t('Recent {count} iterations', { count: value })}
                </option>
              ))}
            </select>
          </label>
        )}
        {!job.summary && tab === 'Overview' && (
          <TrainingJobOverview job={job} progress={progress} history={history} />
        )}
        {!job.summary && tab === 'Learning' && (
          <TrainingLearningView
            key={`${job.id}:${revision}`}
            progress={progress}
            history={history}
            config={config}
            range={range}
          />
        )}
        {!job.summary && tab === 'Evaluation' && (
          <TrainingEvaluationView
            progress={progress}
            history={fitEvaluationSeries ? history : rangedHistory}
            range={0}
            fitSeries={fitEvaluationSeries}
          />
        )}
        {!job.summary && tab === 'Performance' && (
          <TrainingPerformance progress={{ ...progress, history: rangedHistory }} />
        )}
        {!job.summary && tab === 'Tuning results' && (
          <TrainingTuningView
            job={job}
            progress={progress}
            trainingJobs={trainingJobs}
            busy={busy}
            onUseBestParameters={onUseBestParameters}
            onContinueTrial={onContinueTrial}
            onApplyToJob={onApplyToJob}
            onDownloadBestConfig={onDownloadBestConfig}
          />
        )}
        {!job.summary && tab === 'Details' && <TrainingJobDetails job={job} progress={progress} />}
      </section>
    </article>
  );
}
