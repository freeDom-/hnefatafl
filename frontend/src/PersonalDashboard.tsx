import PageHeading from './PageHeading';
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
import PerformanceBadges from './PerformanceBadges';
import Leaderboard from './Leaderboard';
import GameCreationActions from './GameCreationActions';
import './personalDashboard.css';

function DashboardHome({
  accountId,
  onNewSingle,
}: {
  accountId: string;
  onNewSingle?: () => void;
}) {
  const { t } = useI18n();
  const { account: identity } = useAuth();
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
      <PageHeading title={t('Dashboard')} />
      {identity?.email_verified === false && (
        <p role="alert" className="dashboard-verification-warning">
          {t('Verify your email address before creating or joining online games.')}
        </p>
      )}
      <GameCreationActions
        onNewOnline={() => setCreation((value) => value + 1)}
        onNewSingle={onNewSingle}
      >
        {account.data?.games[0] && (
          <a
            className="button secondary"
            href={`/games/${account.data.games[0].id}`}
            onClick={surfaceLink}
          >
            {t('Resume single-player')}
          </a>
        )}
      </GameCreationActions>
      <div className="dashboard-overview">
        <section className="panel dashboard-performance" aria-labelledby="performance-heading">
          <div className="section-heading">
            <h2 id="performance-heading">{t('Your performance')}</h2>
            <a href="/statistics" onClick={surfaceLink}>
              {t('Detailed statistics')}
            </a>
          </div>
          <div className="performance-modes">
            <section aria-labelledby="personal-account-heading">
              <h3 id="personal-account-heading">
                {t('Singleplayer')} <small>{t('Win rate')}</small>
              </h3>
              <DashboardHealth phase={account.phase} refresh={account.refresh} />
              <PerformanceBadges statistics={account.data?.statistics} />
            </section>
            <section aria-labelledby="personal-online-heading">
              <h3 id="personal-online-heading">
                {t('Multiplayer')}{' '}
                <small>
                  {t('Win rate')}
                  {online.data?.statistics?.groups.some(
                    (group) => group.played > 0 && group.rating != null,
                  )
                    ? ' / Elo'
                    : ''}
                </small>
              </h3>
              <DashboardHealth phase={online.phase} refresh={online.refresh} />
              <PerformanceBadges statistics={online.data?.statistics} online />
            </section>
          </div>
        </section>
        <Lobby
          accountId={accountId}
          embedded
          creationRequested={creation}
          open={(id) => navigate(`/online/games/${id}`)}
        />
        <Leaderboard compact />
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
