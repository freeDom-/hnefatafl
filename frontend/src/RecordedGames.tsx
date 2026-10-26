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
  return (
    <ul className="recorded-games">
      {games.map((game) => (
        <li key={`${game.category}:${game.id}`}>
          <a
            href={link(game)}
            onClick={surfaceLink}
            aria-current={selected === game.id ? 'true' : undefined}
          >
            <span className="recorded-identity">
              <strong>
                {game.attackers ?? t('Unassigned')} · {game.defenders ?? t('Unassigned')}
              </strong>
              <small>
                {t(game.category === 'online' ? 'Online multiplayer' : 'Account single-player')}
                {game.owner ? ` · ${game.owner}` : ''} ·{' '}
                {t(ruleNames[game.ruleset] ?? game.ruleset)}
              </small>
            </span>
            <span>
              {t(
                game.status === 'voided'
                  ? 'Voided'
                  : game.status === 'cancelled'
                    ? 'Cancelled'
                    : resultName(game.winner),
              )}
            </span>
            <time dateTime={game.recorded_at}>
              {new Date(game.recorded_at).toLocaleDateString(locale)}
            </time>
            <span>{t('Review')} ↗</span>
          </a>
        </li>
      ))}
    </ul>
  );
}
