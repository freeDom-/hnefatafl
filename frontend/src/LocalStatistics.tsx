import { useI18n } from './i18n';
import type { LocalHistory } from './personalTypes';
import './personalStatistics.css';

/**
 * Local 1v1 records for the signed-in account. Both seats belong to the same
 * account, so there is no "your side" split and no Elo: the owner-scoped totals
 * are shown exactly as the server returns them.
 */
export default function LocalStatisticsView({ history }: { history: LocalHistory | undefined }) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const totals = history?.totals;
  const completed = totals?.completed_games ?? 0;
  const rows: [string, number][] = [
    ['Games played', totals?.total_games ?? 0],
    ['Completed', completed],
    ['In progress', totals?.in_progress ?? 0],
    ['Attacker wins', totals?.attackers_wins ?? 0],
    ['Defender wins', totals?.defenders_wins ?? 0],
    ['Draws', totals?.draws ?? 0],
  ];
  return (
    <div className="detailed-statistics">
      <p className="statistics-explanation">
        {t(
          'Local 1v1 is played on one device and is never rated. Both seats belong to your account, so results are recorded by side rather than as your wins.',
        )}
      </p>
      {totals && totals.total_games === 0 && <p>{t('No local 1v1 games recorded yet.')}</p>}
      {totals && totals.total_games > 0 && (
        <section className="panel statistics-ruleset-card" aria-label={t('Local 1v1')}>
          <div className="section-heading">
            <div>
              <h3>{t('Local 1v1')}</h3>
              <small>{t('Non-Elo · same device')}</small>
            </div>
            <div className="statistics-headline">
              <strong>
                {completed > 0
                  ? t('{value} moves', { value: number.format(totals.average_game_length) })
                  : '—'}
              </strong>
              <span>{t('Average game length')}</span>
            </div>
          </div>
          {completed > 0 && (
            <div
              className="outcome-bar"
              role="img"
              aria-label={t('{attackers} attacker wins, {defenders} defender wins, {draws} draws', {
                attackers: totals.attackers_wins,
                defenders: totals.defenders_wins,
                draws: totals.draws,
              })}
            >
              <span
                className="outcome-wins"
                style={{ width: `${(100 * totals.attackers_wins) / completed}%` }}
              />
              <span
                className="outcome-losses"
                style={{ width: `${(100 * totals.defenders_wins) / completed}%` }}
              />
              <span
                className="outcome-draws"
                style={{ width: `${(100 * totals.draws) / completed}%` }}
              />
            </div>
          )}
          <div
            className="statistics-table-scroll"
            role="region"
            tabIndex={0}
            aria-label={t('Local 1v1 totals')}
          >
            <table className="statistics-table">
              <caption className="sr-only">{t('Local 1v1 totals')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('Record')}</th>
                  <th scope="col">{t('Games')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([label, value]) => (
                  <tr key={label}>
                    <th scope="row">{t(label)}</th>
                    <td data-label={t('Games')}>{number.format(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <small>{t('Local totals cover every ruleset and never affect Elo.')}</small>
        </section>
      )}
    </div>
  );
}
