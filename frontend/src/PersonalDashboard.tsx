import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { Lobby } from './Online';
import { useOnlinePoll } from './useOnlinePoll';
import type { AccountHistory, PersonalOnlineHistory } from './personalTypes';
import { surfaceLink } from './surfaceNavigation';
import { navigate } from './navigation';
import RecordedGames, { type RecordedPage } from './RecordedGames';
import RecordedReview from './RecordedReview';
import './personalDashboard.css';

function DashboardHome({
  accountId,
  onNewSingle,
}: {
  accountId: string;
  onNewSingle?: () => void;
}) {
  const { t } = useI18n();
  const [creation, setCreation] = useState(0);
  const loadAccount = useCallback(
    (signal: AbortSignal) =>
      api<AccountHistory>(
        '/personal/single-player?limit=1&status=in_progress',
        undefined,
        undefined,
        signal,
      ),
    [],
  );
  const loadOnline = useCallback(
    (signal: AbortSignal) =>
      api<PersonalOnlineHistory>('/personal/online?limit=1', undefined, undefined, signal),
    [],
  );
  const loadRecent = useCallback(
    (signal: AbortSignal) =>
      api<RecordedPage>('/history/games?dashboard=true', undefined, undefined, signal),
    [],
  );
  const account = useOnlinePoll(`dashboard-account:${accountId}`, loadAccount);
  const online = useOnlinePoll(`dashboard-online:${accountId}`, loadOnline);
  const recent = useOnlinePoll(`dashboard-recent:${accountId}`, loadRecent);
  return (
    <section className="personal-dashboard">
      <h1>{t('Dashboard')}</h1>
      <div className="dashboard-actions">
        <button className="button primary" onClick={() => setCreation((value) => value + 1)}>
          {t('New online game')}
        </button>
        <button className="button secondary" onClick={onNewSingle}>
          {t('New single-player game')}
        </button>
        {account.data?.games[0] && (
          <a
            className="button secondary"
            href={`/games/${account.data.games[0].id}`}
            onClick={surfaceLink}
          >
            {t('Resume single-player')}
          </a>
        )}
      </div>
      <div className="dashboard-overview">
        <section className="panel personal-section" aria-labelledby="personal-account-heading">
          <h2 id="personal-account-heading">{t('Account single-player')}</h2>
          <DashboardHealth phase={account.phase} refresh={account.refresh} />
          <dl className="personal-totals">
            {(
              [
                ['total_games', 'Games'],
                ['completed_games', 'Completed'],
                ['in_progress', 'In progress'],
                ['attackers_wins', 'Attacker wins'],
                ['defenders_wins', 'Defender wins'],
                ['draws', 'Draws'],
              ] as const
            ).map(([key, label]) => (
              <div key={key}>
                <dt>{t(label)}</dt>
                <dd>{account.data?.totals[key] ?? '—'}</dd>
              </div>
            ))}
          </dl>
          <small>{t('Account totals describe recorded sides, not personal wins or losses.')}</small>
        </section>
        <section className="panel personal-section" aria-labelledby="personal-online-heading">
          <h2 id="personal-online-heading">{t('Online multiplayer')}</h2>
          <DashboardHealth phase={online.phase} refresh={online.refresh} />
          <dl className="personal-totals">
            {(
              [
                ['played', 'Played'],
                ['wins', 'Wins'],
                ['losses', 'Losses'],
                ['draws', 'Draws'],
                ['abandoned_games', 'Timed-out games'],
              ] as const
            ).map(([key, label]) => (
              <div key={key}>
                <dt>{t(label)}</dt>
                <dd>{online.data?.totals[key] ?? '—'}</dd>
              </div>
            ))}
          </dl>
          <small>
            {t(
              'Per-game eligible results only. Cancelled and voided games do not count; concession is not abandonment.',
            )}
          </small>
        </section>
        <section className="panel dashboard-recent" aria-labelledby="recent-heading">
          <div className="section-heading">
            <h2 id="recent-heading">{t('Recorded games')}</h2>
            <a href="/archive" onClick={surfaceLink}>
              {t('View all')}
            </a>
          </div>
          <DashboardHealth phase={recent.phase} refresh={recent.refresh} />
          {recent.phase === 'ready' && !recent.data?.games.length && (
            <p>{t('No completed games yet.')}</p>
          )}
          <RecordedGames games={recent.data?.games ?? []} />
        </section>
        <aside className="panel leaderboard-placeholder">
          <h2>{t('Leaderboard')}</h2>
          <p>{t('Upcoming in Phase 6')}</p>
        </aside>
        <Lobby
          accountId={accountId}
          embedded
          creationRequested={creation}
          open={(id) => navigate(`/online/games/${id}`)}
        />
      </div>
    </section>
  );
}
function DashboardHealth({ phase, refresh }: { phase: string; refresh: () => Promise<unknown> }) {
  const { t } = useI18n();
  return (
    <>
      {phase === 'loading' && <p role="status">{t('Loading your games…')}</p>}
      {phase === 'stale' && (
        <p role="alert">
          {t('History unavailable or stale. Retry to load current records.')}{' '}
          <button className="button secondary" onClick={() => void refresh()}>
            {t('Retry')}
          </button>
        </p>
      )}
    </>
  );
}
export default function PersonalDashboard({ onNewSingle }: { onNewSingle?: () => void }) {
  const { account } = useAuth();
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const changed = () => setPath(location.pathname);
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  if (!account) return null;
  const match = /^\/dashboard\/single-player\/([A-Za-z0-9_-]{1,100})$/.exec(path);
  return match ? (
    <RecordedReview
      key={account.id + ':' + match[1]}
      id={match[1]}
      category="account-single-player"
      personal
      accountId={account.id}
    />
  ) : (
    <DashboardHome key={account.id} accountId={account.id} onNewSingle={onNewSingle} />
  );
}
