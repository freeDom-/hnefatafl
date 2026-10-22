import { useId } from 'react';
import { useI18n } from './i18n';
import Rune from './Rune';
import type { TournamentGame, TournamentPlayer } from './tournamentTypes';
import './TournamentWinMatrix.css';

/** Rows win against columns; tournament seats remain distinct even for the same agent. */
export function tournamentWinMatrix(
  playerCount: number,
  games: TournamentGame[],
  ruleset: string,
): number[][] {
  const wins = Array.from({ length: playerCount }, () => Array<number>(playerCount).fill(0));
  for (const game of games) {
    if (
      game.ruleset !== ruleset ||
      game.outcome !== 'completed' ||
      (game.winner !== 'attackers' && game.winner !== 'defenders')
    )
      continue;
    const attacker = game.attacker_player;
    // Only earlier two-player records omitted the defender's seat.
    const defender = game.defender_player ?? (playerCount === 2 ? 1 - attacker : -1);
    if (
      !Number.isInteger(attacker) ||
      !Number.isInteger(defender) ||
      attacker < 0 ||
      defender < 0 ||
      attacker >= playerCount ||
      defender >= playerCount ||
      attacker === defender
    )
      continue;
    const winner = game.winner === 'attackers' ? attacker : defender;
    const loser = game.winner === 'attackers' ? defender : attacker;
    wins[winner][loser]++;
  }
  return wins;
}

export default function TournamentWinMatrix({
  players,
  games,
  ruleset,
  battlefield,
}: {
  players: TournamentPlayer[];
  games: TournamentGame[];
  ruleset: string;
  battlefield: string;
}) {
  const { t, locale } = useI18n();
  const id = useId();
  const caption = t('Head-to-head wins · {battlefield}', { battlefield });
  const wins = tournamentWinMatrix(players.length, games, ruleset);
  const name = (player: TournamentPlayer) =>
    player.model_name || (player.profile_name ? t(player.profile_name) : player.name);
  const opponent = (player: TournamentPlayer, index: number) =>
    `${t('Opponent {number}', { number: index + 1 })} · ${name(player)}`;
  const heading = (player: TournamentPlayer, index: number) => (
    <>
      <small>{t('Opponent {number}', { number: index + 1 })}</small>
      <span>{name(player)}</span>
    </>
  );
  return (
    <div className="tournament-win-matrix-section">
      <div className="tournament-win-matrix-title" aria-hidden="true">
        {caption}
      </div>
      <div
        className="tournament-win-matrix-scroll"
        role="region"
        aria-label={t('{battlefield} head-to-head results', { battlefield })}
        tabIndex={0}
      >
        <table
          className="tournament-win-matrix"
          data-ruleset={ruleset}
          aria-describedby={`${id}-help`}
          style={{ minWidth: `${140 + players.length * 100}px` }}
        >
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th scope="col">{t('Wins against →')}</th>
              {players.map((player, index) => (
                <th scope="col" id={`${id}-column-${index}`} key={index}>
                  {heading(player, index)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {players.map((player, row) => {
              const rune = player.agent_rune || player.rune;
              return (
                <tr key={row}>
                  <th scope="row" id={`${id}-row-${row}`}>
                    {heading(player, row)}
                  </th>
                  {players.map((_, column) => (
                    <td
                      key={column}
                      data-row-player={row}
                      data-column-player={column}
                      headers={`${id}-row-${row} ${id}-column-${column}`}
                      className={
                        row === column ? 'matrix-self' : wins[row][column] ? 'has-wins' : ''
                      }
                      aria-label={
                        row === column
                          ? t(rune ? 'Rune of {opponent}' : 'No rune recorded for {opponent}', {
                              opponent: opponent(player, row),
                            })
                          : undefined
                      }
                    >
                      {row === column ? (
                        rune ? (
                          <Rune rune={rune} />
                        ) : (
                          <span aria-hidden="true">—</span>
                        )
                      ) : (
                        wins[row][column].toLocaleString(locale)
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="tournament-win-matrix-help" id={`${id}-help`}>
        {t(
          "Cells show the row opponent's wins against the column opponent. Draws and unfinished games do not count as wins.",
        )}
      </p>
    </div>
  );
}
