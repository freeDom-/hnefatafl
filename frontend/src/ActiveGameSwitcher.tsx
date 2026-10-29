import { useCallback } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import type { Game } from './types';
import type { OnlineListing } from './onlineTypes';
import { useCompactPlay, useDesktopPlay } from './mobilePlay';

const ruleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};

export type ActiveGameEntry = {
  id: string;
  type: 'single' | 'online';
  ruleset: string;
  leg: number | null;
  yourTurn: boolean;
  names?: string;
  waiting?: boolean;
};

/**
 * Shared running-game navigation. Tabbed play includes the current game,
 * waiting games, and loading/error states; the fallback strip appears only
 * when another accessible active game exists.
 */
export default function ActiveGameSwitcher({
  accountId,
  currentId,
  open,
}: {
  accountId: string;
  currentId: string;
  open: (entry: ActiveGameEntry) => void;
}) {
  const { t } = useI18n();
  const compact = useCompactPlay();
  const desktop = useDesktopPlay();
  const list = compact || desktop;
  const loadSingle = useCallback(
    (signal: AbortSignal) => api<Game[]>('/games', undefined, undefined, signal),
    [],
  );
  const loadOnline = useCallback(
    async (signal: AbortSignal) => {
      const games = new Map<string, OnlineListing['games'][number]>();
      for (let offset = 0; ; offset += 50) {
        const page = await api<OnlineListing>(
          `/online/games?section=mine&limit=50&offset=${offset}`,
          undefined,
          undefined,
          signal,
        );
        const batch = Array.isArray(page.games) ? page.games : [];
        for (const game of batch) games.set(game.id, game);
        if (!list || batch.length < 50) return [...games.values()];
      }
    },
    [list],
  );
  const single = useOnlinePoll(`switcher-single:${accountId}`, loadSingle);
  const online = useOnlinePoll(
    `switcher-online:${accountId}:${list ? 'list' : 'strip'}`,
    loadOnline,
  );
  const singleGames = Array.isArray(single.data) ? single.data : [];
  const onlineGames = online.data ?? [];
  const entries: ActiveGameEntry[] = [];
  for (const game of singleGames) {
    if (game.winner || game.recorded_winner) continue;
    entries.push({
      id: game.id,
      type: 'single',
      ruleset: game.ruleset,
      leg: null,
      yourTurn:
        list &&
        game.players?.[game.turn]?.kind === 'human' &&
        (!game.players[game.turn].account_id || game.players[game.turn].account_id === accountId),
      names: [game.players?.attackers?.name, game.players?.defenders?.name]
        .filter(Boolean)
        .join(' · '),
    });
  }
  for (const game of onlineGames) {
    if (game.status !== 'active' && !(list && game.status === 'waiting')) continue;
    entries.push({
      id: game.id,
      type: 'online',
      ruleset: game.ruleset,
      leg: game.match.mode === 'single' ? null : game.match.leg,
      yourTurn:
        game.status === 'active' && game.participants[game.position.turn]?.account_id === accountId,
      names: [game.participants.attackers?.name, game.participants.defenders?.name]
        .filter(Boolean)
        .join(' · '),
      waiting: game.status === 'waiting',
    });
  }
  const loading = single.phase === 'loading' || online.phase === 'loading';
  const failed = single.phase === 'stale' || online.phase === 'stale';
  if (!list && !entries.some((entry) => entry.id !== currentId)) return null;
  const navigation =
    entries.length > 0 ? (
      <nav
        className={`active-game-switcher${list ? ' running-game-list' : ''}`}
        aria-label={t(list ? 'My games' : 'Active games')}
      >
        {entries.map((entry) => (
          <button
            type="button"
            key={`${entry.type}:${entry.id}`}
            data-game-id={entry.id}
            data-game-type={entry.type}
            className="button secondary small"
            aria-current={entry.id === currentId ? 'page' : undefined}
            onClick={() => open(entry)}
          >
            <span>
              {t(entry.type === 'online' ? 'Online game' : 'Single player game')}
              {entry.leg ? ` · ${t('Game {leg}', { leg: entry.leg })}` : ''}
              {' · '}
              {t(ruleNames[entry.ruleset] ?? entry.ruleset)}
              {entry.yourTurn ? ` · ${t('Your turn')}` : ''}
              {list && entry.waiting ? ` · ${t('Waiting')}` : ''}
            </span>
            {list && entry.names && <small>{entry.names}</small>}
          </button>
        ))}
      </nav>
    ) : null;
  if (!list) return navigation;
  return (
    <div className="mobile-running-games">
      {loading && <p role="status">{t('Loading running games…')}</p>}
      {failed && (
        <div role="alert">
          <p>{t('Some running games could not be loaded.')}</p>
          <button
            className="button secondary small"
            onClick={() => {
              void single.refresh();
              void online.refresh();
            }}
          >
            {t('Retry')}
          </button>
        </div>
      )}
      {!loading && !failed && entries.length === 0 && <p>{t('No running games.')}</p>}
      {navigation}
    </div>
  );
}
