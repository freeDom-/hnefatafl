import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import type { AccountHistory, PersonalOnlineHistory } from './personalTypes';
import { navigate } from './navigation';
import { surfaceLink } from './surfaceNavigation';
import GameCreationActions from './GameCreationActions';
import { Lobby } from './Online';

export default function PlayLanding({
  onNewSingle,
  creatingSingle,
}: {
  onNewSingle: () => void;
  creatingSingle: boolean;
}) {
  const { account } = useAuth();
  const { t } = useI18n();
  const [creation, setCreation] = useState(0);
  const load = useCallback(async (signal: AbortSignal) => {
    const [single, online] = await Promise.all([
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
    ]);
    return [
      ...(single.games ?? [])
        .filter((game) => game.status === 'in_progress')
        .map((game) => `/games/${game.id}`),
      ...(online.games ?? [])
        .filter((game) => game.status === 'active' || game.status === 'waiting')
        .map((game) => `/online/games/${game.id}`),
    ];
  }, []);
  const games = useOnlinePoll(`play-home:${account?.id}`, load);
  let remembered = '';
  try {
    remembered =
      localStorage.getItem('hnefatafl-play-route') ??
      `/games/${localStorage.getItem('hnefatafl-game') ?? ''}`;
  } catch {
    /* The server-owned list remains usable without browser storage. */
  }
  const target = games.data?.includes(remembered) ? remembered : games.data?.[0];
  useEffect(() => {
    if (games.phase === 'ready' && target && !creation && !creatingSingle) navigate(target);
  }, [games.phase, target, creation, creatingSingle]);
  return (
    <section className="personal-dashboard play-home">
      <h1>{t('Play')}</h1>
      {games.phase === 'loading' || (games.phase === 'ready' && target) ? (
        <p role="status">{t('Loading your games…')}</p>
      ) : games.phase === 'stale' ? (
        <div role="alert">
          <p>{t('Your running games could not be loaded.')}</p>
          <button className="button secondary" onClick={() => void games.refresh()}>
            {t('Retry')}
          </button>
        </div>
      ) : (
        <>
          <p>{t('No games are currently running.')}</p>
          <p>
            <a href="/dashboard" onClick={surfaceLink}>
              {t('Find online games on Dashboard')}
            </a>
          </p>
          <GameCreationActions
            onNewOnline={() => setCreation((value) => value + 1)}
            onNewSingle={onNewSingle}
          />
        </>
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
