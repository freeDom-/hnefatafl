import type { AccountHistory, PersonalOnlineHistory } from './personalTypes';
import { rulesetName } from './RecordedGames';
import type { PlayModeGame } from './PlayModes';

/** Own local 1v1 games share the account-game summary shape. */
export type LocalHistory = {
  games: {
    id: string;
    ruleset: string;
    status: string;
    players?: { attackers: { name: string }; defenders: { name: string } };
  }[];
};
const gameModes: Record<string, string> = {
  'human-ai': 'Human vs AI',
  'ai-ai': 'AI vs AI',
  local: 'Local 1v1',
};
const gameStatuses: Record<string, string> = { waiting: 'Waiting', active: 'Active' };
const opposite = (side: 'attackers' | 'defenders') =>
  side === 'attackers' ? 'defenders' : 'attackers';
/** The named AI seat of a single-player record; absent for human-vs-human. */
const aiSeat = (players: unknown) =>
  Object.values((players ?? {}) as Record<string, { name: string; kind: string }>).find(
    (seat) => seat.kind === 'ai',
  )?.name;

/**
 * Turn the three account-owned game lists into continue entries. Every field is
 * server data: a game is only marked as awaiting this account when the server
 * says so, a single-player seat never claims it, and the opponent name is only
 * reported when the source record actually carries one.
 */
export function runningGames({
  single,
  online,
  local,
}: {
  single?: AccountHistory;
  online?: PersonalOnlineHistory;
  local?: LocalHistory;
}): PlayModeGame[] {
  const label = (ruleset: string | undefined) => rulesetName(ruleset ?? '');
  return [
    ...(single?.games ?? [])
      .filter((game) => game.status === 'in_progress')
      .map((game) => ({
        id: game.id,
        href: `/games/${game.id}`,
        ruleset: game.ruleset,
        label: label(game.ruleset),
        detail: gameModes[game.mode] ?? game.mode,
        status: game.status,
        opponent: aiSeat(game.players),
        yourTurn: false,
      })),
    ...(online?.games ?? [])
      .filter((game) => game.status === 'active' || game.status === 'waiting')
      .map((game) => ({
        id: game.id,
        href: `/online/games/${game.id}`,
        ruleset: game.ruleset,
        label: label(game.ruleset),
        detail: gameStatuses[game.status],
        status: game.status,
        opponent: game.own_side
          ? (game.players?.[opposite(game.own_side)] ?? undefined)
          : undefined,
        yourTurn: game.your_turn === true,
      })),
    ...(local?.games ?? [])
      .filter((game) => game.status === 'in_progress')
      .map((game) => ({
        id: game.id,
        href: `/games/${game.id}`,
        ruleset: game.ruleset,
        label: label(game.ruleset),
        detail: gameModes.local,
        status: game.status,
        // Both local seats are human, so a move is always waiting for a player.
        yourTurn: true,
      })),
  ];
}
