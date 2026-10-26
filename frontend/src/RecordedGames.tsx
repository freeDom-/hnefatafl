import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import type { Side } from './types';
export type RecordedGame = {
  id: string;
  category: 'online' | 'account-single-player';
  ruleset: string;
  status: string;
  winner: Side | 'draw' | null;
  eligible: boolean;
  created_at: string;
  recorded_at: string;
  attackers: string | null;
  defenders: string | null;
  owner: string | null;
};
export type RecordedPage = {
  games: RecordedGame[];
  matching: number;
  limit: number;
  next_cursor: string | null;
  totals: {
    category: RecordedGame['category'];
    games: number;
    completed: number;
    attackers_wins: number;
    defenders_wins: number;
    draws: number;
  }[];
};
export const reviewUrl = (game: Pick<RecordedGame, 'id' | 'category'>) =>
  `/archive?review=${game.category}&selected=${encodeURIComponent(game.id)}`;
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
  name ?? t('Unassigned');
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
    return `${participantLabel(game.attackers, t)} (${t('Attackers')})`;
  if (game.winner === 'defenders')
    return `${participantLabel(game.defenders, t)} (${t('Defenders')})`;
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
          {games.map((game) => (
            <tr key={`${game.category}:${game.id}`}>
              <td>{t(statusNames[game.status] ?? game.status)}</td>
              <td>{t(ruleNames[game.ruleset] ?? game.ruleset)}</td>
              <td>{participantLabel(game.attackers, t)}</td>
              <td>{participantLabel(game.defenders, t)}</td>
              <td>{winnerLabel(game, t)}</td>
              <td>
                <a
                  href={link(game)}
                  onClick={surfaceLink}
                  aria-current={selected === game.id ? 'true' : undefined}
                  aria-label={t('Open review: {attacker} vs {defender}', {
                    attacker: participantLabel(game.attackers, t),
                    defender: participantLabel(game.defenders, t),
                  })}
                >
                  <time dateTime={game.recorded_at}>
                    {format.format(new Date(game.recorded_at))}
                  </time>
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
