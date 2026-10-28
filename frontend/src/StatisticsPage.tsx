import { useCallback, useEffect, useState } from 'react';
import { useAuth } from './Auth';
import { api } from './api';
import { useI18n } from './i18n';
import { navigate } from './navigation';
import { useOnlinePoll } from './useOnlinePoll';
import Tabs from './Tabs';
import PersonalStatisticsView from './PersonalStatistics';
import type { AccountHistory, PersonalOnlineHistory } from './personalTypes';
import {
  statisticRuleNames,
  statisticRulesets,
  statisticsHref,
  statisticsLocation,
  type StatisticsMode,
} from './personalStatisticsData';
import './personalStatistics.css';

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
      api<AccountHistory | PersonalOnlineHistory>(
        mode === 'multiplayer'
          ? '/personal/online?section=history&limit=1'
          : '/personal/single-player?limit=1',
        undefined,
        undefined,
        signal,
      ),
    [mode],
  );
  const poll = useOnlinePoll(`statistics:${account?.id}:${mode}`, load);
  const statistics = poll.data?.statistics;
  const filtered = statistics && {
    ...statistics,
    groups: statistics.groups.filter((group) => ruleset === 'all' || group.ruleset === ruleset),
  };
  return (
    <section className="statistics-page">
      <div className="section-heading">
        <div>
          <h1>{t('Your statistics')}</h1>
          <p>{t('Your results, from your side of the board.')}</p>
        </div>
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
      </div>
      <Tabs
        id="personal-statistics"
        label="Statistics game type"
        active={mode}
        items={[
          { id: 'single-player', label: 'Singleplayer' },
          { id: 'multiplayer', label: 'Multiplayer' },
        ]}
        onChange={(value) => navigate(statisticsHref(value as StatisticsMode, ruleset))}
      />
      <div
        id="personal-statistics-panel"
        role="tabpanel"
        aria-labelledby={`personal-statistics-tab-${mode}`}
      >
        <h2 className="sr-only">{t(mode === 'multiplayer' ? 'Multiplayer' : 'Singleplayer')}</h2>
        {poll.phase === 'loading' && <p role="status">{t('Loading your statistics…')}</p>}
        {poll.phase === 'stale' && (
          <p role="alert">
            {t('Statistics unavailable or stale. Retry to load current results.')}{' '}
            <button className="button secondary" onClick={() => void poll.refresh()}>
              {t('Retry')}
            </button>
          </p>
        )}
        {statistics && (
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
      </div>
    </section>
  );
}
