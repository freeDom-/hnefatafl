import { useMemo, useState } from 'react';
import { useI18n } from './i18n';
import type { Job, Model } from './types';
import { jobGameCounts } from './trainingGameCounts';
import { filterJobs, jobName, jobRuleset, jobProgress, finite } from './trainingDashboardData';

export default function TrainingJobNavigator({
  jobs,
  selected,
  models,
  onSelect,
}: {
  jobs: Job[];
  selected?: string;
  models: Model[];
  onSelect: (job: Job) => void;
}) {
  const { t, locale } = useI18n();
  const [expanded, setExpanded] = useState(
    () => typeof matchMedia === 'undefined' || !matchMedia('(max-width: 900px)').matches,
  );
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState('active');
  const visible = useMemo(
    () => filterJobs(jobs, query, status, sort, models),
    [jobs, query, status, sort, models],
  );
  const formatGames = (games: number | undefined) =>
    games === undefined
      ? t('Not recorded')
      : t(games === 1 ? '{count} game' : '{count} games', { count: games.toLocaleString(locale) });
  return (
    <nav className="training-job-navigator" aria-label={t('Training jobs')}>
      <label className="mobile-job-selector">
        {t('Selected job')}
        <select
          aria-label={t('Selected job')}
          value={selected ?? ''}
          onChange={(event) => {
            const job = jobs.find((job) => job.id === event.target.value);
            if (job) onSelect(job);
          }}
        >
          {!selected && <option value="">{t('Select a training job')}</option>}
          {selected && !visible.some((job) => job.id === selected) && (
            <option value={selected}>
              {jobName(jobs.find((job) => job.id === selected)!)} · {t('Outside current filters')}
            </option>
          )}
          {visible.map((job) => (
            <option key={job.id} value={job.id}>
              {jobName(job)} · {t(job.status)} · {formatGames(jobGameCounts(job).job)}
            </option>
          ))}
        </select>
      </label>
      <details
        className="job-navigation-options"
        open={expanded}
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary>{t('Find a training job')}</summary>
        <div className="job-filters">
          <label>
            {t('Search jobs')}
            <input
              type="search"
              value={query}
              placeholder={t('Job or model name')}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="job-filter-row">
            <label>
              {t('Status')}
              <select
                aria-label={t('Status')}
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                {[
                  'all',
                  'active',
                  'running',
                  'queued',
                  'pending',
                  'cancelling',
                  'completed',
                  'failed',
                  'cancelled',
                ].map((value) => (
                  <option key={value} value={value}>
                    {t(
                      value === 'all' ? 'All statuses' : value === 'active' ? 'Active jobs' : value,
                    )}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t('Sort jobs')}
              <select
                aria-label={t('Sort jobs')}
                value={sort}
                onChange={(event) => setSort(event.target.value)}
              >
                <option value="active">{t('Active first')}</option>
                <option value="newest">{t('Newest first')}</option>
                <option value="oldest">{t('Oldest first')}</option>
              </select>
            </label>
          </div>
        </div>
        <p className="navigator-count subtle" role="status">
          {t('{shown} of {total} jobs', { shown: visible.length, total: jobs.length })}
        </p>
        <div className="jobs-list">
          {visible.map((job) => {
            const progress = jobProgress(job);
            const games = jobGameCounts(job).job;
            return (
              <button
                key={job.id}
                data-job-id={job.id}
                className={`job-row ${selected === job.id ? 'selected' : ''}`}
                aria-current={selected === job.id ? 'true' : undefined}
                onClick={() => onSelect(job)}
              >
                <span className={`job-status ${job.status}`}>{t(job.status)}</span>
                <strong>{jobName(job)}</strong>
                <span>
                  {jobRuleset(job)} · {t('Iteration')}{' '}
                  {finite(progress.iteration) ? progress.iteration : '—'}
                  {' · '}
                  <span aria-label={`${t('Job games')}: ${formatGames(games)}`}>
                    {formatGames(games)}
                  </span>
                </span>
                <small>
                  {job.created_at
                    ? new Date(job.created_at).toLocaleString(locale, {
                        dateStyle: 'medium',
                        timeStyle: 'short',
                      })
                    : job.id.slice(0, 8)}
                </small>
                <small className="job-location">
                  {job.execution === 'remote'
                    ? (job.worker_name ?? t('Remote queue'))
                    : t('This server')}
                </small>
              </button>
            );
          })}
          {!visible.length && <p className="subtle">{t('No jobs match these filters.')}</p>}
        </div>
      </details>
    </nav>
  );
}
