import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from './api';
import { useI18n } from './i18n';
import { leagueDate, leagueError, leagueMonth, RULESET_LABELS } from './leaguePresentation';
import { LeaguePageControls, LeagueReadState, LeagueTable, useLeaguePages } from './LeagueTables';
import type {
  LeagueAudit,
  LeagueHealth,
  LeaguePage,
  LeagueSeason,
  StaleLeagueEntry,
} from './leagueTypes';
import Modal from './Modal';
import { notify } from './notifications';
import { formatRemaining } from './remainingTime';
import { useOnlinePoll } from './useOnlinePoll';

const ADMIN_REASON_LIMIT = 1000;
const ADMIN_POLL_INTERVAL = 15000;

type Action =
  | { kind: 'pause' | 'resume'; version: number }
  | { kind: 'cancel'; entry: StaleLeagueEntry }
  | { kind: 'rebuild'; season: LeagueSeason };

const ACTION_LABELS: Record<Action['kind'], string> = {
  pause: 'Pause matchmaking',
  resume: 'Resume matchmaking',
  cancel: 'Cancel stale entry',
  rebuild: 'Rebuild season',
};

const AUDIT_LABELS: Record<string, string> = {
  pause_matchmaking: 'Matchmaking paused',
  resume_matchmaking: 'Matchmaking resumed',
  cancel_stale_queue: 'Stale entry cancelled',
  request_rebuild: 'Rebuild requested',
  complete_rebuild: 'Season rebuilt',
};

function AdminAudit({ accountId, changes }: { accountId: string; changes: number }) {
  const { t, locale } = useI18n();
  const audit = useLeaguePages<LeaguePage<LeagueAudit>>('/leagues/admin/audit', accountId);
  const refresh = audit.refresh;
  const previousChange = useRef(changes);
  useEffect(() => {
    if (previousChange.current !== changes) {
      previousChange.current = changes;
      void refresh();
    }
  }, [changes, refresh]);
  return (
    <>
      <LeagueReadState
        error={audit.error}
        loading={audit.phase === 'loading'}
        retry={audit.retry}
      />
      {audit.data && audit.data.items.length === 0 && (
        <p>{t('No league administration actions have been recorded.')}</p>
      )}
      {!!audit.data?.items.length && (
        <LeagueTable label={t('League audit entries')}>
          <thead>
            <tr>
              {['Date', 'Action', 'Season', 'Reason'].map((label) => (
                <th key={label} scope="col">
                  {t(label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {audit.data.items.map((entry) => (
              <tr key={entry.sequence}>
                <td>
                  <time dateTime={entry.at}>{leagueDate(entry.at, locale)}</time>
                </td>
                <td>{t(AUDIT_LABELS[entry.action] ?? 'League policy updated')}</td>
                <td>{entry.season_id ?? '—'}</td>
                <td className="league-audit-reason">{entry.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </LeagueTable>
      )}
      <LeaguePageControls
        {...audit}
        label={t('Audit pages')}
        nextCursor={audit.data?.next_cursor ?? null}
      />
    </>
  );
}

export default function LeagueAdministration({
  accountId,
  season,
  onChanged,
}: {
  accountId: string;
  season: LeagueSeason | null;
  onChanged: () => void;
}) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
  const [action, setAction] = useState<Action | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const [changes, setChanges] = useState(0);
  const [monitor, setMonitor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const mutation = useRef<AbortController | null>(null);
  const rebuilds = useRef<string[] | null>(null);
  const load = useCallback(
    (signal: AbortSignal) =>
      api<LeagueHealth>('/leagues/admin/health', undefined, undefined, signal),
    [],
  );
  const poll = useOnlinePoll(`league-health:${accountId}`, load, {
    intervalMs: monitor ? ADMIN_POLL_INTERVAL : 0,
  });
  const health = poll.data;
  const refresh = () => void poll.refresh();
  useEffect(() => {
    if (!health) return;
    const pendingRebuilds = health.rebuilds.map((entry) => entry.season_id);
    setMonitor(pendingRebuilds.length > 0 || health.settlement.pending > 0);
    if (rebuilds.current?.some((id) => !pendingRebuilds.includes(id))) {
      onChanged();
      setChanges((previous) => previous + 1);
    }
    rebuilds.current = pendingRebuilds;
  }, [health, onChanged]);
  const seasonId = season?.id;
  useEffect(() => {
    setAction(null);
    setFailure(null);
    setBusy(false);
    return () => {
      mutation.current?.abort();
      mutation.current = null;
    };
  }, [seasonId]);

  function open(next: Action) {
    setFailure(null);
    setAction(next);
  }
  function close() {
    if (mutation.current) return;
    setAction(null);
    setFailure(null);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!action || mutation.current) return;
    const reason = String(new FormData(event.currentTarget).get('reason') ?? '').trim();
    if (!reason) {
      setFailure(new ApiError(422, '', 'league_reason_required'));
      return;
    }
    const path =
      action.kind === 'cancel'
        ? '/leagues/admin/queue/cancel'
        : action.kind === 'rebuild'
          ? `/leagues/admin/seasons/${encodeURIComponent(action.season.id)}/rebuild`
          : '/leagues/admin/matchmaking';
    const payload =
      action.kind === 'cancel'
        ? { request_id: action.entry.request_id, reason }
        : action.kind === 'rebuild'
          ? { version: action.season.revision, reason }
          : { paused: action.kind === 'pause', version: action.version, reason };
    const controller = new AbortController();
    mutation.current = controller;
    setBusy(true);
    setFailure(null);
    try {
      await api(
        path,
        payload,
        'POST',
        AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
      );
      if (!controller.signal.aborted) {
        setAction(null);
        setChanges((previous) => previous + 1);
        refresh();
        onChanged();
        notify.success(
          t(
            action.kind === 'rebuild'
              ? 'Season rebuild requested.'
              : 'League administration updated.',
          ),
        );
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setFailure(error);
        refresh();
        onChanged();
      }
    } finally {
      if (mutation.current === controller) {
        mutation.current = null;
        setBusy(false);
      }
    }
  }

  const target = action?.kind === 'rebuild' && action.season.id !== seasonId ? null : action;
  return (
    <section className="arena-surface league-panel" aria-labelledby="league-admin-heading">
      <h2 id="league-admin-heading">{t('League administration')}</h2>
      <LeagueReadState error={poll.error} loading={poll.phase === 'loading'} retry={refresh} />
      {health && (
        <>
          <dl className="league-metrics">
            <div>
              <dt>{t('Matchmaking')}</dt>
              <dd>{t(health.settings.paused ? 'Paused' : 'Enabled')}</dd>
            </div>
            <div>
              <dt>{t('Worker')}</dt>
              <dd>{t(health.worker.running ? 'Running' : 'Stopped')}</dd>
            </div>
            <div>
              <dt>{t('Queue entries')}</dt>
              <dd>{number.format(health.queue.total)}</dd>
            </div>
            <div>
              <dt>{t('Expired entries')}</dt>
              <dd>{number.format(health.queue.expired)}</dd>
            </div>
            <div>
              <dt>{t('Pending settlements')}</dt>
              <dd>{number.format(health.settlement.pending)}</dd>
            </div>
            <div>
              <dt>{t('Failed settlements')}</dt>
              <dd>{number.format(health.settlement.failed)}</dd>
            </div>
            <div>
              <dt>{t('Oldest pending settlement')}</dt>
              <dd>
                {health.settlement.oldest_pending_seconds == null
                  ? '—'
                  : formatRemaining(health.settlement.oldest_pending_seconds)}
              </dd>
            </div>
          </dl>
          <p>
            {t('{brandubh} Brandubh · {tablut} Tablut · {copenhagen} Copenhagen in queue', {
              brandubh: number.format(health.queue.by_ruleset.brandubh ?? 0),
              tablut: number.format(health.queue.by_ruleset.tablut ?? 0),
              copenhagen: number.format(health.queue.by_ruleset.copenhagen ?? 0),
            })}
          </p>
          {health.worker.last_checked_at && (
            <p>
              {t('Worker last checked {time}', {
                time: leagueDate(health.worker.last_checked_at, locale),
              })}
            </p>
          )}
          {health.worker.last_success_at && (
            <p>
              {t('Worker last succeeded {time}', {
                time: leagueDate(health.worker.last_success_at, locale),
              })}
            </p>
          )}
          {health.worker.last_error && (
            <p role="status">
              {t(
                health.worker.last_error === 'deployment_locked'
                  ? 'The league worker is waiting for the deployment lock.'
                  : 'The last worker check failed. Results will be retried.',
              )}
            </p>
          )}
          {health.rebuilds.length > 0 && (
            <ul>
              {health.rebuilds.map((entry) => (
                <li key={entry.season_id}>
                  {t('Rebuild in progress for {season}', { season: entry.season_id })}
                </li>
              ))}
            </ul>
          )}
          <div className="page-toolbar">
            <button className="button secondary" type="button" onClick={refresh}>
              {t('Refresh league health')}
            </button>
            <button
              className="button secondary"
              type="button"
              onClick={() =>
                open({
                  kind: health.settings.paused ? 'resume' : 'pause',
                  version: health.settings.version,
                })
              }
            >
              {t(health.settings.paused ? ACTION_LABELS.resume : ACTION_LABELS.pause)}
            </button>
            {season?.stored && (
              <button
                className="button secondary"
                type="button"
                disabled={
                  season.status === 'rebuilding' ||
                  health.rebuilds.some((entry) => entry.season_id === season.id)
                }
                onClick={() => open({ kind: 'rebuild', season })}
              >
                {t(ACTION_LABELS.rebuild)}
              </button>
            )}
          </div>
          {health.queue.stale.length > 0 && (
            <LeagueTable label={t('Stale league queue entries')}>
              <thead>
                <tr>
                  <th scope="col">{t('Ruleset')}</th>
                  <th scope="col">{t('Expired')}</th>
                  <th scope="col">{t('Action')}</th>
                </tr>
              </thead>
              <tbody>
                {health.queue.stale.map((entry) => (
                  <tr key={entry.request_id}>
                    <th scope="row">{t(RULESET_LABELS[entry.ruleset])}</th>
                    <td>
                      <time dateTime={entry.expires_at}>
                        {leagueDate(entry.expires_at, locale)}
                      </time>
                    </td>
                    <td>
                      <button
                        className="button secondary"
                        type="button"
                        onClick={() => open({ kind: 'cancel', entry })}
                      >
                        {t(ACTION_LABELS.cancel)}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </LeagueTable>
          )}
          <details onToggle={(event) => setAuditOpen(event.currentTarget.open)}>
            <summary>{t('Private audit history')}</summary>
            {auditOpen && <AdminAudit accountId={accountId} changes={changes} />}
          </details>
        </>
      )}
      {target && (
        <Modal title={t(ACTION_LABELS[target.kind])} onClose={close} initialFocus="textarea">
          <form onSubmit={(event) => void submit(event)}>
            <fieldset disabled={busy} className="league-admin-form">
              {target.kind === 'rebuild' && (
                <p>
                  {t(
                    'Rebuild {season} from official results? Standings are unavailable until recovery finishes.',
                    { season: leagueMonth(target.season.starts_at, locale) },
                  )}
                </p>
              )}
              <label className="league-filter">
                {t('Reason')}
                <textarea name="reason" required maxLength={ADMIN_REASON_LIMIT} />
              </label>
              {failure != null && (
                <p className="form-error" role="alert">
                  {failure instanceof ApiError && failure.code === 'league_reason_required'
                    ? t('Enter a reason for this change.')
                    : leagueError(failure, t)}
                </p>
              )}
              <div className="dialog-actions">
                <button className="button secondary" type="button" onClick={close}>
                  {t('Back')}
                </button>
                <button
                  className="button primary"
                  type="submit"
                  disabled={
                    failure instanceof ApiError && failure.code === 'league_version_changed'
                  }
                >
                  {t(busy ? 'Saving…' : ACTION_LABELS[target.kind])}
                </button>
              </div>
            </fieldset>
          </form>
        </Modal>
      )}
    </section>
  );
}
