import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import type { AccountHistory, PersonalOnlineHistory } from './personalTypes';
import { navigate } from './navigation';
import { surfaceLink } from './surfaceNavigation';
import PlayModes from './PlayModes';
import { runningGames, type LocalHistory } from './playModeGames';
import { Lobby } from './Online';
import PageHeading from './PageHeading';

export default function PlayLanding({
  onNewSingle,
  creatingSingle,
}: {
  onNewSingle: () => void;
  creatingSingle: boolean;
}) {
  const { account } = useAuth();
  const { t } = useI18n();
  // The legacy `?setup=online` deep link opens the online creation dialog and
  // suppresses the automatic resume, so the dialog keeps its own entry state.
  const [creation, setCreation] = useState(() =>
    new URLSearchParams(location.search).get('setup') === 'online' ? 1 : 0,
  );
  const load = useCallback(async (signal: AbortSignal) => {
    const [single, online, local] = await Promise.all([
      api<AccountHistory>(
        '/personal/single-player?limit=100&status=in_progress',
        undefined,
        undefined,
        signal,
      ),
      api<PersonalOnlineHistory>(
        '/personal/online?limit=100&section=current',
        undefined,
        undefined,
        signal,
      ),
      api<LocalHistory>(
        '/personal/local?limit=100&status=in_progress',
        undefined,
        undefined,
        signal,
      ),
    ]);
    return runningGames({ single, online, local });
  }, []);
  const games = useOnlinePoll(`play-home:${account?.id}`, load);
  const running = games.data ?? [];
  let remembered = '';
  try {
    remembered =
      localStorage.getItem('hnefatafl-play-route') ??
      `/games/${localStorage.getItem('hnefatafl-game') ?? ''}`;
  } catch {
    /* The server-owned list remains usable without browser storage. */
  }
  const target = running.some((game) => game.href === remembered) ? remembered : running[0]?.href;
  // A suppressed resume (open creation dialog) keeps the hierarchy visible.
  const resuming = games.phase === 'ready' && !!target && !creation && !creatingSingle;
  useEffect(() => {
    if (games.phase !== 'ready' || !target || creation || creatingSingle) return;
    // Resuming a saved game is an automatic redirect. It must never override a
    // newer user navigation, so only act while the Play entry is still current.
    // `location` is updated synchronously by `navigate`/`surfaceLink`, so this
    // guard is correct even before React commits the route change.
    if (location.pathname !== '/play') return;
    // Replace the Play entry so Browser Back returns to the page before Play
    // instead of landing on /play and resuming the game again.
    navigate(target, { replace: true });
  }, [games.phase, target, creation, creatingSingle]);
  return (
    <section className="personal-dashboard play-home arena-surface">
      <PageHeading title={t('Play')} />
      {games.phase === 'loading' || resuming ? (
        <p role="status">{t('Loading your games…')}</p>
      ) : games.phase === 'stale' ? (
        <div role="alert">
          <p>{t('Your running games could not be loaded.')}</p>
          <button className="button secondary" onClick={() => void games.refresh()}>
            {t('Retry')}
          </button>
        </div>
      ) : (
        <PlayModes
          onNewOnline={() => setCreation((value) => value + 1)}
          onNewSingle={onNewSingle}
          games={running}
        >
          {!running.length && (
            <p>
              <a href="/online" onClick={surfaceLink}>
                {t('Find online games in the lobby')}
              </a>
            </p>
          )}
        </PlayModes>
      )}
      {creation > 0 && account && (
        <Lobby
          accountId={account.id}
          embedded
          dialogOnly
          creationRequested={creation}
          onCloseCreation={() => setCreation(0)}
          open={(id) => navigate(`/online/games/${id}`)}
        />
      )}
    </section>
  );
}
