import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import type { Side } from './types';
export type RecordedCategory = 'online' | 'account-single-player' | 'local';
export type RecordedGame = {
  id: string;
  category: RecordedCategory;
  ruleset: string;
  status: string;
  winner: Side | 'draw' | null;
  eligible: boolean;
  created_at: string;
  recorded_at: string;
  attackers: string | null;
  defenders: string | null;
  owner: string | null;
  /** Immutable start-rating snapshots; null for older records or local games. */
  attacker_rating?: number | null;
  defender_rating?: number | null;
  /** Effective persisted Elo delta for the winner; null when unrated or voided. */
  winner_delta?: number | null;
};
export type RecordedPage = {
  games: RecordedGame[];
  matching: number;
  limit: number;
  next_cursor: string | null;
  totals: {
    category: RecordedCategory;
    games: number;
    completed: number;
    attackers_wins: number;
    defenders_wins: number;
    draws: number;
  }[];
};
export const reviewUrl = (game: Pick<RecordedGame, 'id' | 'category'>) =>
  `/archive?review=${game.category}&selected=${encodeURIComponent(game.id)}`;
/**
 * A nonterminal online record is still being played: it is watched, not
 * replayed, and it must never present itself as a completed result.
 */
export const isLiveRecord = (game: Pick<RecordedGame, 'category' | 'status'>) =>
  game.category === 'online' && (game.status === 'active' || game.status === 'waiting');
export const liveUrl = (game: Pick<RecordedGame, 'id'>) => `/online/games/${game.id}`;
export const resultName = (winner: RecordedGame['winner']) =>
  winner === 'draw'
    ? 'Draw'
    : winner === 'attackers'
      ? 'Attackers win'
      : winner === 'defenders'
        ? 'Defenders win'
        : 'Unfinished record';
const ruleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
/** Ruleset ids are the persisted value; record tables never invent a display name. */
export const rulesetName = (id: string) => ruleNames[id] ?? id;
export const categoryName = (category: RecordedCategory) =>
  category === 'online'
    ? 'Online multiplayer'
    : category === 'local'
      ? 'Local 1v1'
      : 'Singleplayer';
const statusNames: Record<string, string> = {
  voided: 'Voided',
  cancelled: 'Cancelled',
  active: 'Active',
  waiting: 'Waiting',
  in_progress: 'In progress',
  completed: 'Completed',
};
/**
 * Backend instants carry their own offset. The viewer's local timezone is
 * resolved here so the table always shows the user's time, never the server's.
 */
const dateTimeFormat = (locale: string) =>
  new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
export const participantLabel = (name: string | null, t: (key: string) => string) =>
  name || t('Unassigned');
/** Start Elo is shown only when a reliable snapshot exists; never backfilled. */
export const ratedParticipantLabel = (
  name: string | null,
  rating: number | null | undefined,
  t: (key: string, values?: Record<string, string | number>) => string,
) =>
  rating === null || rating === undefined
    ? participantLabel(name, t)
    : `${participantLabel(name, t)} (${Math.round(rating)})`;
/** A positive winner delta only; draws, voids and unrated records return null. */
export const winnerDelta = (game: RecordedGame): number | null => {
  if (game.status !== 'completed') return null;
  if (game.winner !== 'attackers' && game.winner !== 'defenders') return null;
  if (game.winner_delta === null || game.winner_delta === undefined) return null;
  return game.winner_delta > 0 ? game.winner_delta : null;
};
/**
 * Only a completed recorded result names a winner. Draws, unfinished,
 * cancelled and voided games never gain an invented or board-inferred winner.
 */
export function winnerLabel(
  game: RecordedGame,
  t: (key: string, values?: Record<string, string | number>) => string,
) {
  if (game.status !== 'completed') return '—';
  if (game.winner === 'draw') return t('Draw');
  if (game.winner === 'attackers')
    return `${participantLabel(game.attackers, t)} (${t('Attacker')})`;
  if (game.winner === 'defenders')
    return `${participantLabel(game.defenders, t)} (${t('Defender')})`;
  return '—';
}
export default function RecordedGames({
  games,
  selected,
  link = reviewUrl,
}: {
  games: RecordedGame[];
  selected?: string;
  link?: (game: RecordedGame) => string;
}) {
  const { t, locale } = useI18n();
  const format = dateTimeFormat(locale);
  return (
    <div className="recorded-games-table">
      <table className="recorded-games">
        <caption className="sr-only">{t('Recorded games')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('Status')}</th>
            <th scope="col">{t('Ruleset')}</th>
            <th scope="col">{t('Attacker')}</th>
            <th scope="col">{t('Defender')}</th>
            <th scope="col">{t('Winner')}</th>
            <th scope="col">{t('Date')}</th>
          </tr>
        </thead>
        <tbody>
          {games.map((game) => {
            const live = isLiveRecord(game);
            const attackers = participantLabel(game.attackers, t);
            const defenders = participantLabel(game.defenders, t);
            return (
              <tr
                key={`${game.category}:${game.id}`}
                className={selected === game.id ? 'selected' : undefined}
              >
                <td>
                  {t(statusNames[game.status] ?? game.status)}
                  {live && <span className="record-live">{t('Live')}</span>}
                </td>
                <td>{t(rulesetName(game.ruleset))}</td>
                <td>{ratedParticipantLabel(game.attackers, game.attacker_rating, t)}</td>
                <td>{ratedParticipantLabel(game.defenders, game.defender_rating, t)}</td>
                <td>
                  {winnerLabel(game, t)}
                  {(() => {
                    const delta = winnerDelta(game);
                    if (delta === null) return null;
                    const text = `+${Math.round(delta)}`;
                    return (
                      <span
                        className="winner-delta"
                        aria-label={t('{value} rating gain', { value: text })}
                      >
                        {' '}
                        {text}
                      </span>
                    );
                  })()}
                </td>
                <td>
                  <a
                    href={live ? liveUrl(game) : link(game)}
                    onClick={surfaceLink}
                    aria-current={selected === game.id ? 'true' : undefined}
                    aria-label={t(
                      live
                        ? 'Watch live: {attacker} vs {defender}'
                        : 'Open review: {attacker} vs {defender}',
                      { attacker: attackers, defender: defenders },
                    )}
                  >
                    <time dateTime={game.recorded_at}>
                      {format.format(new Date(game.recorded_at))}
                    </time>
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
