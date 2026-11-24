import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { LEAGUE_TIER_DECOR } from './arenaAssets';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import LeagueAdministration from './LeagueAdministration';
import { LeagueCountdown, queueSnapshot, sampleTime, type ServerSample } from './LeagueQueue';
import LeagueQueue from './LeagueQueue';
import {
  LeagueHistory,
  LeagueReadState,
  LeagueSeasonSelector,
  LeagueStandings,
} from './LeagueTables';
import { leagueDate, leagueMonth, RULESET_LABELS, TIER_LABELS } from './leaguePresentation';
import type {
  CurrentLeague,
  LeaguePolicy,
  LeagueProgress,
  LeagueRuleset,
  LeagueSeason,
} from './leagueTypes';
import { useOnlinePoll } from './useOnlinePoll';
import './leagues.css';

function PersonalProgress({
  progress,
  policy,
}: {
  progress: LeagueProgress | null;
  policy: LeaguePolicy;
}) {
  const { t, locale } = useI18n();
  if (!progress)
    return (
      <p className="empty-state compact">
        {t('Standings will return after the season rebuild completes.')}
      </p>
    );
  const number = new Intl.NumberFormat(locale);
  const score = new Intl.NumberFormat(locale, { signDisplay: 'exceptZero' });
  const tier = progress.tier;
  const next = progress.next_tier;
  const earlier = progress.previous_tier;
  const direction =
    tier && earlier && tier !== earlier
      ? policy.tiers.findIndex((item) => item.id === tier) >
        policy.tiers.findIndex((item) => item.id === earlier)
        ? 'Promoted to {tier}'
        : 'Demoted to {tier}'
      : null;
  return (
    <>
      {tier ? (
        <div className="league-tier">
          <img src={LEAGUE_TIER_DECOR[tier]} alt="" width={128} height={128} />
          <div>
            <h3>{t(TIER_LABELS[tier])}</h3>
            {direction && (
              <p>
                {t(direction, { tier: t(TIER_LABELS[tier]) })}
                {progress.tier_changed_at && (
                  <>
                    {' · '}
                    <time dateTime={progress.tier_changed_at}>
                      {leagueDate(progress.tier_changed_at, locale)}
                    </time>
                  </>
                )}
              </p>
            )}
          </div>
        </div>
      ) : (
        <p>{t('Play a league game to begin your seasonal progress.')}</p>
      )}
      <dl className="league-metrics">
        {(
          [
            ['Points', progress.points],
            ['Wins', progress.wins],
            ['Draws', progress.draws],
            ['Losses', progress.losses],
          ] as const
        ).map(([label, value]) => (
          <div key={label}>
            <dt>{t(label)}</dt>
            <dd>{number.format(value)}</dd>
          </div>
        ))}
      </dl>
      <p>
        {t('Season scoring · {win} win, {draw} draw, {loss} loss', {
          win: score.format(policy.win_points),
          draw: score.format(policy.draw_points),
          loss: score.format(policy.loss_points),
        })}
      </p>
      <div className="league-progression">
        {tier && next && (
          <label>
            {t('{tier} · {points} of {minimum} points', {
              tier: t(TIER_LABELS[next.id]),
              points: number.format(progress.points),
              minimum: number.format(next.minimum_points),
            })}
            <progress max={next.progress_total} value={next.progress_points} />
          </label>
        )}
        {tier && !next && <p>{t('Highest league tier reached.')}</p>}
        {progress.qualified ? (
          <p>
            {progress.rank != null
              ? t('Current rank #{rank}', { rank: number.format(progress.rank) })
              : t('Qualified for standings')}
          </p>
        ) : (
          <>
            <p>{t('Unranked')}</p>
            <label>
              {t('Qualification · {played} of {required} eligible results', {
                played: number.format(progress.played),
                required: number.format(progress.qualifying_results),
              })}
              <progress max={progress.qualifying_results} value={progress.played} />
            </label>
          </>
        )}
      </div>
    </>
  );
}

type CurrentSnapshot = {
  model: CurrentLeague;
  clock: ServerSample;
  initialQueue: ReturnType<typeof queueSnapshot>;
};

function LeaguePage({ accountId, canAdmin }: { accountId: string; canAdmin: boolean }) {
  const { t, locale } = useI18n();
  const [ruleset, setRuleset] = useState<LeagueRuleset>('copenhagen');
  const [selectedSeason, setSelectedSeason] = useState('');
  const [displayedSeason, setDisplayedSeason] = useState<LeagueSeason | null>(null);
  const [cached, setCached] = useState<CurrentSnapshot | null>(null);
  const [historyActivity, setHistoryActivity] = useState(0);
  const [adminActivity, setAdminActivity] = useState(0);
  const load = useCallback(
    async (signal: AbortSignal): Promise<CurrentSnapshot> => {
      const model = await api<CurrentLeague>(
        `/leagues/current?ruleset=${ruleset}`,
        undefined,
        undefined,
        signal,
      );
      return {
        model,
        clock: sampleTime(model.server_time),
        initialQueue: queueSnapshot(model.queue),
      };
    },
    [ruleset],
  );
  const current = useOnlinePoll(`league-current:${accountId}:${ruleset}`, load, { intervalMs: 0 });
  useEffect(() => {
    if (current.data) setCached(current.data);
  }, [current.data]);
  const snapshot = current.data ?? cached;
  const season = snapshot?.model.season;
  const policy = snapshot?.model.policy;
  const viewedSeason = selectedSeason || season?.id;
  const scope = viewedSeason ? { accountId, ruleset, season: viewedSeason } : null;
  const recordScope = scope && `${scope.season}:${scope.ruleset}`;

  const refresh = current.refresh;
  const refreshCurrent = useCallback(() => void refresh(), [refresh]);
  const gameChanged = useCallback(
    (active: boolean) => {
      setHistoryActivity((previous) => previous + 1);
      if (!active) refreshCurrent();
    },
    [refreshCurrent],
  );
  const administrationChanged = useCallback(() => {
    setAdminActivity((previous) => previous + 1);
    setHistoryActivity((previous) => previous + 1);
    refreshCurrent();
  }, [refreshCurrent]);
  useEffect(() => {
    window.addEventListener('progression-changed', refreshCurrent);
    return () => window.removeEventListener('progression-changed', refreshCurrent);
  }, [refreshCurrent]);
  useEffect(() => {
    if (
      season?.status === 'rebuilding' &&
      displayedSeason?.id === season.id &&
      displayedSeason.status !== 'rebuilding' &&
      displayedSeason.revision > season.revision
    )
      refreshCurrent();
  }, [displayedSeason, refreshCurrent, season]);

  const rebuildTarget =
    displayedSeason?.id === viewedSeason
      ? displayedSeason
      : season?.id === viewedSeason
        ? season
        : null;
  return (
    <section className="leagues-page" aria-labelledby="leagues-heading">
      <header className="arena-surface league-panel league-heading">
        <h1 id="leagues-heading">{t('Leagues')}</h1>
        <label className="league-filter">
          {t('Ruleset')}
          <select
            value={ruleset}
            onChange={(event) => setRuleset(event.currentTarget.value as LeagueRuleset)}
          >
            {(policy?.rulesets ?? (Object.keys(RULESET_LABELS) as LeagueRuleset[])).map((id) => (
              <option value={id} key={id}>
                {t(RULESET_LABELS[id])}
              </option>
            ))}
          </select>
        </label>
      </header>
      <div className="league-overview">
        <section
          className="arena-surface league-panel"
          aria-labelledby="league-progression-heading"
        >
          <h2 id="league-progression-heading">{t('Personal progression')}</h2>
          {season && snapshot && (
            <p>
              {t('Current season · {season}', { season: leagueMonth(season.starts_at, locale) })}
              {' · '}
              <LeagueCountdown
                sample={snapshot.clock}
                deadline={season.ends_at}
                onElapsed={refreshCurrent}
              />
            </p>
          )}
          <LeagueReadState
            error={current.error}
            loading={current.phase === 'loading'}
            retry={refreshCurrent}
          />
          {current.data && (
            <PersonalProgress
              progress={current.data.model.progress}
              policy={current.data.model.policy}
            />
          )}
        </section>
        {snapshot && policy && (
          <LeagueQueue
            accountId={accountId}
            ruleset={ruleset}
            initial={snapshot.initialQueue}
            policy={policy}
            refreshSignal={adminActivity}
            onGameChanged={gameChanged}
          />
        )}
      </div>
      {season && scope && (
        <>
          <section
            className="arena-surface league-panel"
            aria-labelledby="league-standings-heading"
          >
            <header className="league-heading">
              <h2 id="league-standings-heading">{t('League standings')}</h2>
              <LeagueSeasonSelector
                accountId={accountId}
                current={season}
                selected={selectedSeason}
                onSelect={setSelectedSeason}
              />
            </header>
            <LeagueStandings
              key={recordScope}
              scope={scope}
              revision={season.id === scope.season ? season.revision : -1}
              activity={adminActivity}
              onSeasonLoaded={setDisplayedSeason}
            />
          </section>
          <LeagueHistory
            key={recordScope}
            scope={scope}
            currentSeason={season.id === scope.season}
            activity={historyActivity}
            onSettled={refreshCurrent}
          />
        </>
      )}
      {canAdmin && (
        <LeagueAdministration
          accountId={accountId}
          season={rebuildTarget ?? null}
          onChanged={administrationChanged}
        />
      )}
    </section>
  );
}

export default function Leagues() {
  const { account } = useAuth();
  const { t } = useI18n();
  if (!account?.permissions.includes('play'))
    return (
      <p className="arena-surface empty-state" role="status">
        {t('Your account cannot access leagues.')}
      </p>
    );
  return (
    <LeaguePage
      key={account.id}
      accountId={account.id}
      canAdmin={account.permissions.includes('accounts')}
    />
  );
}
