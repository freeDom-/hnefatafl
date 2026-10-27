import { useI18n } from './i18n';
import { modeNames as onlineModeNames } from './Online';
import type { PersonalStatisticGroup, PersonalStatistics } from './personalTypes';

const ruleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
// Account matchups use the ruleset's own mode names; online reuses the lobby terms.
const modeNames: Record<string, string> = {
  'human-ai': 'Human vs AI',
  'ai-ai': 'AI vs AI',
  ...onlineModeNames,
};

/** Shared personal statistics relative to the signed-in account's human seat. */
export default function PersonalStatisticsView({
  statistics,
  online = false,
}: {
  statistics: PersonalStatistics | undefined;
  online?: boolean;
}) {
  const { t, locale } = useI18n();
  const percent = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 });
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const byRuleset = new Map<string, PersonalStatisticGroup[]>();
  for (const group of statistics?.groups ?? []) {
    const list = byRuleset.get(group.ruleset) ?? [];
    list.push(group);
    byRuleset.set(group.ruleset, list);
  }
  const rate = (value: number | null) => (value === null ? '—' : percent.format(value));
  const count = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : number.format(value);
  return (
    <div className="personal-statistics">
      {byRuleset.size === 0 && <p>{t('No games recorded yet.')}</p>}
      {[...byRuleset].map(([ruleset, list]) => (
        <section key={ruleset} className="statistics-ruleset">
          <h3>{t(ruleNames[ruleset] ?? ruleset)}</h3>
          {list.map((group) => (
            <div key={group.mode} className="statistics-group">
              <h4>{t(modeNames[group.mode] ?? group.mode)}</h4>
              <dl className="statistics-metrics">
                <div>
                  <dt>{t('Played')}</dt>
                  <dd>{number.format(group.played)}</dd>
                </div>
                <div>
                  <dt>{t('Wins')}</dt>
                  <dd>{count(group.wins)}</dd>
                </div>
                <div>
                  <dt>{t('Losses')}</dt>
                  <dd>{count(group.losses)}</dd>
                </div>
                <div>
                  <dt>{t('Draws')}</dt>
                  <dd>{count(group.draws)}</dd>
                </div>
                {online && (
                  <div>
                    <dt title={t('Games you lost because your own clock expired.')}>
                      {t('Timed-out games')}
                    </dt>
                    <dd>{count(group.timed_out)}</dd>
                  </div>
                )}
                <div>
                  <dt>{t('Win rate')}</dt>
                  <dd>{rate(group.win_rate)}</dd>
                </div>
                <div>
                  <dt>{t('Win rate as attacker')}</dt>
                  <dd>{rate(group.win_rate_attackers)}</dd>
                </div>
                <div>
                  <dt>{t('Win rate as defender')}</dt>
                  <dd>{rate(group.win_rate_defenders)}</dd>
                </div>
                <div>
                  <dt>{t('Average game length')}</dt>
                  <dd>
                    {group.average_game_length === null
                      ? '—'
                      : t('{value} moves', {
                          value: number.format(group.average_game_length),
                        })}
                  </dd>
                </div>
              </dl>
              {group.mode === 'ai-ai' && (
                <small>{t('AI vs AI games have no personal result.')}</small>
              )}
            </div>
          ))}
        </section>
      ))}
      {online && (
        <small>
          {t(
            'Timed-out games count only losses where your own clock expired, not wins from an opponent timeout.',
          )}
        </small>
      )}
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
