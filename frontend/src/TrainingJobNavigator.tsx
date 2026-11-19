import { useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from './i18n';
import type { Job, Model } from './types';
import { jobGameCounts } from './trainingGameCounts';
import { filterJobs, jobName, jobRuleset, jobProgress, finite } from './trainingDashboardData';

// The navigator and its tests share one responsive query so the desktop row and
// mobile selector branches never drift apart.
function desktopNavigation() {
  return typeof matchMedia === 'undefined' || !matchMedia('(max-width: 900px)').matches;
}

// Focus only targets an element that is still connected, rendered and visible.
// A collapsed <details> is checked explicitly because jsdom has no layout and
// Element.checkVisibility() is unavailable there.
function isFocusableTarget(element: HTMLElement | null): element is HTMLElement {
  if (!element?.isConnected) return false;
  if (element.closest('details:not([open])')) return false;
  return typeof element.checkVisibility !== 'function' || element.checkVisibility();
}

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
  const navigatorRef = useRef<HTMLElement>(null);
  const selectorRef = useRef<HTMLSelectElement>(null);
  const detailsRef = useRef<HTMLDetailsElement>(null);
  // One bounded request per Show selected job activation, tied to the intended
  // job id. Background polls never create or replay a request.
  const [focusRequest, setFocusRequest] = useState<{ id: string; token: number } | null>(null);
  const focusToken = useRef(0);
  const handledToken = useRef(0);
  const latest = useRef({ jobs, selected });
  latest.current = { jobs, selected };
  // The disclosure is uncontrolled: it starts open on desktop and only Show
  // selected job reopens it, so filtering, sorting and polling never expand it.
  useEffect(() => {
    if (detailsRef.current) detailsRef.current.open = desktopNavigation();
  }, []);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState('active');
  const [page, setPage] = useState(0);
  const visible = useMemo(
    () => filterJobs(jobs, query, status, sort, models),
    [jobs, query, status, sort, models],
  );
  const pageCount = Math.max(1, Math.ceil(visible.length / 20));
  const currentPage = Math.min(page, pageCount - 1);
  // Persist the clamp so polling cannot resurrect a removed page on expansion.
  if (page !== currentPage) setPage(currentPage);
  const pageJobs = visible.slice(currentPage * 20, currentPage * 20 + 20);
  const selectedJob = jobs.find((job) => job.id === selected);
  const selectedOnPage = pageJobs.some((job) => job.id === selected);
  // Follow an explicit selection, not every poll/sort update or page click.
  useEffect(() => {
    const index = visible.findIndex((job) => job.id === selected);
    if (index >= 0) setPage(Math.floor(index / 20));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);
  const showSelected = () => {
    if (!selected) return;
    const filteredIndex = visible.findIndex((job) => job.id === selected);
    if (filteredIndex >= 0) setPage(Math.floor(filteredIndex / 20));
    else {
      const all = filterJobs(jobs, '', 'all', sort, models);
      setQuery('');
      setStatus('all');
      setPage(Math.max(0, Math.floor(all.findIndex((job) => job.id === selected) / 20)));
    }
    // The restored row lives inside the desktop disclosure; open it first.
    if (desktopNavigation() && detailsRef.current) detailsRef.current.open = true;
    // Restore focus after the restored row/selector has rendered.
    focusToken.current += 1;
    setFocusRequest({ id: selected, token: focusToken.current });
  };
  useEffect(() => {
    if (!focusRequest || focusRequest.token <= handledToken.current) return;
    // Mark handled before applying so a completed or cancelled request can
    // never execute again, even if this effect runs a second time.
    handledToken.current = focusRequest.token;
    const { id } = focusRequest;
    const { jobs: currentJobs, selected: currentSelection } = latest.current;
    // A vanished job or a selection that moved on invalidates the request.
    if (currentSelection !== id || !currentJobs.some((job) => job.id === id)) return;
    let target: HTMLElement | null = null;
    if (desktopNavigation()) {
      const scope = navigatorRef.current;
      const rows = scope
        ? Array.from(scope.querySelectorAll<HTMLButtonElement>('.job-row[data-job-id]'))
        : [];
      target = rows.find((row) => row.dataset.jobId === id) ?? null;
    } else {
      target = selectorRef.current;
    }
    // Never fall back to an unrelated row: drop the request if the intended
    // target is gone, hidden or inside a collapsed disclosure.
    if (!isFocusableTarget(target)) return;
    target.focus();
    target.scrollIntoView?.({ block: 'nearest' });
    // Only an explicit Show selected job request should move focus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequest]);
  const formatGames = (games: number | undefined) =>
    games === undefined
      ? t('Not recorded')
      : t(games === 1 ? '{count} game' : '{count} games', { count: games.toLocaleString(locale) });
  return (
    <nav ref={navigatorRef} className="training-job-navigator" aria-label={t('Training jobs')}>
      <label className="mobile-job-selector">
        {t('Selected job')}
        <select
          ref={selectorRef}
          aria-label={t('Selected job')}
          value={selected ?? ''}
          onChange={(event) => {
            const job = jobs.find((job) => job.id === event.target.value);
            if (job) onSelect(job);
          }}
        >
          {!selected && <option value="">{t('Select a training job')}</option>}
          {selected && !selectedOnPage && (
            <option value={selected}>
              {jobs.some((job) => job.id === selected)
                ? jobName(jobs.find((job) => job.id === selected)!)
                : selected.slice(0, 8)}{' '}
              ·{' '}
              {t(
                visible.some((job) => job.id === selected)
                  ? 'Outside current page'
                  : 'Outside current filters',
              )}
            </option>
          )}
          {pageJobs.map((job) => (
            <option key={job.id} value={job.id}>
              {jobName(job)} · {t(job.status)} · {formatGames(jobGameCounts(job).job)}
            </option>
          ))}
        </select>
      </label>
      {selectedJob && !selectedOnPage && (
        <div className="navigator-selection">
          <span>
            {t('Selected job')}: <strong>{jobName(selectedJob)}</strong>
          </span>
          <button className="button secondary small" onClick={showSelected}>
            {t('Show selected job')}
          </button>
        </div>
      )}
      {visible.length > 20 && (
        <div className="job-pagination" role="group" aria-label={t('Training job pages')}>
          <button
            className="button secondary small"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            {t('Previous job page')}
          </button>
          <output aria-live="polite">
            {t('Page {current} of {total}', { current: currentPage + 1, total: pageCount })}
          </output>
          <button
            className="button secondary small"
            disabled={currentPage === pageCount - 1}
            onClick={() => setPage(currentPage + 1)}
          >
            {t('Next job page')}
          </button>
        </div>
      )}
      <details ref={detailsRef} className="job-navigation-options">
        <summary>{t('Find a training job')}</summary>
        <div className="job-filters">
          <label>
            {t('Search jobs')}
            <input
              type="search"
              value={query}
              placeholder={t('Job or model name')}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
            />
          </label>
          <div className="job-filter-row">
            <label>
              {t('Status')}
              <select
                aria-label={t('Status')}
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setPage(0);
                }}
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
                onChange={(event) => {
                  setSort(event.target.value);
                  setPage(0);
                }}
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
          {' · '}
          {t('Showing {first}–{last}', {
            first: visible.length ? currentPage * 20 + 1 : 0,
            last: Math.min((currentPage + 1) * 20, visible.length),
          })}
        </p>
        <div className="jobs-list">
          {pageJobs.map((job) => {
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
