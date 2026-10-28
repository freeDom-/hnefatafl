import { useI18n } from './i18n';
import type {
  PersonalStatisticGroup,
  PersonalStatisticMetrics,
  PersonalStatistics,
} from './personalTypes';
import { completedGames, statisticRuleNames } from './personalStatisticsData';
import './personalStatistics.css';

function ResultsTable({ group, caption }: { group: PersonalStatisticGroup; caption: string }) {
  const { t, locale } = useI18n();
  const percent = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 });
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const rows: [string, PersonalStatisticMetrics | undefined][] = [
    ['Overall', group],
    ['As attacker', group.by_side?.attackers],
    ['As defender', group.by_side?.defenders],
  ];
  return (
    <div className="statistics-table-scroll" role="region" aria-label={caption} tabIndex={0}>
      <table className="statistics-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {[
              'Side',
              'Games played',
              'Wins',
              'Losses',
              'Draws',
              'Win rate',
              'Average game length',
            ].map((label) => (
              <th key={label} scope="col">
                {t(label)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, row]) => (
            <tr key={label}>
              <th scope="row">{t(label)}</th>
              <td data-label={t('Games played')}>{row ? number.format(row.played) : '—'}</td>
              <td data-label={t('Wins')}>{row?.wins == null ? '—' : number.format(row.wins)}</td>
              <td data-label={t('Losses')}>
                {row?.losses == null ? '—' : number.format(row.losses)}
              </td>
              <td data-label={t('Draws')}>{row?.draws == null ? '—' : number.format(row.draws)}</td>
              <td data-label={t('Win rate')}>
                {row?.win_rate == null ? '—' : percent.format(row.win_rate)}
              </td>
              <td data-label={t('Average game length')}>
                {row?.average_game_length == null
                  ? '—'
                  : t('{value} moves', { value: number.format(row.average_game_length) })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PersonalStatisticsView({
  statistics,
  online = false,
}: {
  statistics: PersonalStatistics | undefined;
  online?: boolean;
}) {
  const { t, locale } = useI18n();
  const percent = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 });
  return (
    <div className="detailed-statistics">
      {!statistics?.groups.length && <p>{t('No games recorded yet.')}</p>}
      {statistics?.groups.map((group) => {
        const name = t(statisticRuleNames[group.ruleset] ?? group.ruleset);
        const completed = completedGames(group);
        const elo =
          online && group.played > 0 && group.rating != null && Number.isFinite(group.rating)
            ? Math.round(group.rating)
            : null;
        return (
          <section key={group.ruleset} className="panel statistics-ruleset-card" aria-label={name}>
            <div className="section-heading">
              <div>
                <h3>{name}</h3>
                <small>{t('{count} completed games', { count: completed })}</small>
              </div>
              <div className="statistics-headline">
                <strong>{group.win_rate === null ? '—' : percent.format(group.win_rate)}</strong>
                <span>{t('Win rate')}</span>
                {elo !== null && <small>{t('Elo {rating}', { rating: elo })}</small>}
              </div>
            </div>
            {completed > 0 && (
              <div
                className="outcome-bar"
                role="img"
                aria-label={t('{wins} wins, {losses} losses, {draws} draws', {
                  wins: group.wins ?? 0,
                  losses: group.losses ?? 0,
                  draws: group.draws ?? 0,
                })}
              >
                <span
                  className="outcome-wins"
                  style={{ width: `${(100 * (group.wins ?? 0)) / completed}%` }}
                />
                <span
                  className="outcome-losses"
                  style={{ width: `${(100 * (group.losses ?? 0)) / completed}%` }}
                />
                <span
                  className="outcome-draws"
                  style={{ width: `${(100 * (group.draws ?? 0)) / completed}%` }}
                />
              </div>
            )}
            <ResultsTable
              group={group}
              caption={t('{ruleset} results by side', { ruleset: name })}
            />
            {online && (
              <small>
                {t('Timed-out games')}: {group.timed_out ?? 0} ·{' '}
                {t('Games you lost because your own clock expired.')}
              </small>
            )}
            {!online && (
              <div className="statistics-opponents">
                <h4>{t('AI opponents')}</h4>
                {!group.agents?.length && <p>{t('No AI-opponent results recorded yet.')}</p>}
                {group.agents?.map((agent) => (
                  <details key={agent.id} className="opponent-results">
                    <summary>
                      <strong>{t(agent.name)}</strong>
                      <span>{t('{count} games', { count: agent.played })}</span>
                      <span>
                        {agent.win_rate === null ? '—' : percent.format(agent.win_rate)}{' '}
                        {t('Win rate')}
                      </span>
                    </summary>
                    <ResultsTable
                      group={agent}
                      caption={t('{agent} results by side', { agent: t(agent.name) })}
                    />
                  </details>
                ))}
              </div>
            )}
          </section>
        );
      })}
      {!online && (statistics?.coverage.unattributed_games ?? 0) > 0 && (
        <small>
          {t(
            '{count} historical games have no authoritative account seat and are not counted here.',
            { count: statistics!.coverage.unattributed_games },
          )}
        </small>
      )}
    </div>
  );
}
