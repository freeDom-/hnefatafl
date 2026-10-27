import { useCallback } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import type { Game } from './types';
import type { OnlineListing } from './onlineTypes';

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
};

/**
 * Compact switcher across active single-player and online games. Rendered only
 * when more than one accessible active game exists; terminal games are excluded.
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
  const loadSingle = useCallback(
    (signal: AbortSignal) => api<Game[]>('/games', undefined, undefined, signal),
    [],
  );
  const loadOnline = useCallback(
    (signal: AbortSignal) =>
      api<OnlineListing>(
        '/online/games?section=mine&limit=50&offset=0',
        undefined,
        undefined,
        signal,
      ),
    [],
  );
  const single = useOnlinePoll(`switcher-single:${accountId}`, loadSingle);
  const online = useOnlinePoll(`switcher-online:${accountId}`, loadOnline);
  const singleGames = Array.isArray(single.data) ? single.data : [];
  const onlineGames = Array.isArray(online.data?.games) ? online.data.games : [];
  const entries: ActiveGameEntry[] = [];
  for (const game of singleGames) {
    if (game.winner || game.recorded_winner) continue;
    entries.push({
      id: game.id,
      type: 'single',
      ruleset: game.ruleset,
      leg: null,
      yourTurn: false,
    });
  }
  for (const game of onlineGames) {
    if (game.status !== 'active') continue;
    entries.push({
      id: game.id,
      type: 'online',
      ruleset: game.ruleset,
      leg: game.match.mode === 'single' ? null : game.match.leg,
      yourTurn: game.participants[game.position.turn]?.account_id === accountId,
    });
  }
  if (entries.length <= 1) return null;
  return (
    <nav className="active-game-switcher" aria-label={t('Active games')}>
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
          {t(entry.type === 'online' ? 'Online game' : 'Single player game')}
          {entry.leg ? ` · ${t('Game {leg}', { leg: entry.leg })}` : ''}
          {' · '}
          {t(ruleNames[entry.ruleset] ?? entry.ruleset)}
          {entry.yourTurn ? ` · ${t('Your turn')}` : ''}
        </button>
      ))}
    </nav>
  );
}
