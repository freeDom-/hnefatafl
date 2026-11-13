import { useCallback, useEffect, useState } from 'react';
import { useAuth } from './Auth';
import { api } from './api';
import { useI18n } from './i18n';
import { navigate } from './navigation';
import { useOnlinePoll } from './useOnlinePoll';
import Tabs from './Tabs';
import PageHeading from './PageHeading';
import PersonalStatisticsView from './PersonalStatistics';
import LocalStatisticsView from './LocalStatistics';
import RatingHistory from './RatingHistory';
import type { AccountHistory, LocalHistory, PersonalOnlineHistory } from './personalTypes';
import {
  statisticRuleNames,
  statisticRulesets,
  statisticsHref,
  statisticsLocation,
  type StatisticsMode,
} from './personalStatisticsData';
import './personalStatistics.css';

const statisticsPath = (mode: StatisticsMode) =>
  mode === 'multiplayer'
    ? '/personal/online?section=history&limit=1'
    : mode === 'local'
      ? '/personal/local?limit=1'
      : '/personal/single-player?limit=1';

export default function StatisticsPage() {
  const { account } = useAuth();
  const { t } = useI18n();
  const [selection, setSelection] = useState(statisticsLocation);
  useEffect(() => {
    const changed = () => setSelection(statisticsLocation());
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  const { mode, ruleset } = selection;
  const load = useCallback(
    (signal: AbortSignal) =>
      api<AccountHistory | PersonalOnlineHistory | LocalHistory>(
        statisticsPath(mode),
        undefined,
        undefined,
        signal,
      ),
    [mode],
  );
  const poll = useOnlinePoll(`statistics:${account?.id}:${mode}`, load);
  const statistics = poll.data?.category === 'local' ? undefined : poll.data?.statistics;
  const local = poll.data?.category === 'local' ? poll.data : undefined;
  const filtered = statistics && {
    ...statistics,
    groups: statistics.groups.filter((group) => ruleset === 'all' || group.ruleset === ruleset),
  };
  return (
    <section className="statistics-page">
      <div className="page-toolbar">
        <PageHeading
          title={t('Your statistics')}
          subtitle={t('Your results, from your side of the board.')}
        />
        {mode !== 'local' && (
          <div className="statistics-ruleset-filter">
            <label htmlFor="statistics-ruleset">{t('Ruleset')}</label>
            <select
              id="statistics-ruleset"
              value={ruleset}
              onChange={(event) => navigate(statisticsHref(mode, event.target.value))}
            >
              <option value="all">{t('All rulesets')}</option>
              {statisticRulesets.map((id) => (
                <option key={id} value={id}>
                  {t(statisticRuleNames[id])}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      <Tabs
        id="personal-statistics"
        label="Statistics game type"
        active={mode}
        items={[
          { id: 'single-player', label: 'Singleplayer' },
          { id: 'multiplayer', label: 'Multiplayer' },
          { id: 'local', label: 'Local 1v1' },
        ]}
        onChange={(value) => navigate(statisticsHref(value as StatisticsMode, ruleset))}
      />
      <div
        id="personal-statistics-panel"
        role="tabpanel"
        aria-labelledby={`personal-statistics-tab-${mode}`}
      >
        <h2 className="sr-only">
          {t(
            mode === 'multiplayer'
              ? 'Multiplayer'
              : mode === 'local'
                ? 'Local 1v1'
                : 'Singleplayer',
          )}
        </h2>
        {poll.phase === 'loading' && <p role="status">{t('Loading your statistics…')}</p>}
        {poll.phase === 'stale' && (
          <p role="alert">
            {t('Statistics unavailable or stale. Retry to load current results.')}{' '}
            <button className="button secondary" onClick={() => void poll.refresh()}>
              {t('Retry')}
            </button>
          </p>
        )}
        {poll.data && mode === 'local' && <LocalStatisticsView history={local} />}
        {statistics && mode !== 'local' && (
          <>
            <p className="statistics-explanation">
              {t(
                'Win rate is wins divided by completed games, including draws. Length counts individual moves by either player.',
              )}
            </p>
            {mode === 'single-player' && (
              <p className="statistics-explanation">
                {t(
                  'Games played includes unfinished single-player games; they do not affect win rates or average length.',
                )}
              </p>
            )}
            <PersonalStatisticsView statistics={filtered} online={mode === 'multiplayer'} />
          </>
        )}
        {mode === 'multiplayer' && statistics && <RatingHistory ruleset={ruleset} />}
      </div>
    </section>
  );
}
