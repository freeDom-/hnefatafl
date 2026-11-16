import PageHeading from './PageHeading';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import type {
  AccountHistory,
  PersonalOnlineHistory,
  PersonalProgress,
  LocalHistory as PersonalLocalHistory,
} from './personalTypes';
import { surfaceLink } from './surfaceNavigation';
import { navigate } from './navigation';
import {
  isLiveRecord,
  liveUrl,
  participantLabel,
  reviewUrl,
  rulesetName,
  winnerLabel,
  type RecordedGame,
  type RecordedPage,
} from './RecordedGames';
import RecordedReview from './RecordedReview';
import Leaderboard, { readStoredRuleset } from './Leaderboard';
import Board, { type BoardView } from './Board';
import LocalGameDialog from './LocalGameDialog';
import { runningGames } from './playModeGames';
import DashboardCountdown from './DashboardCountdown';
import type { Leaderboard as LeaderboardData, OnlineGame } from './onlineTypes';
import type { Profile } from './ProfilePage';
import type { Game, Side } from './types';
import './personalDashboard.css';

import type { PortraitCatalog, AchievementProgress } from './portraitTypes';
import Portrait from './Portrait';

/** The active game's authoritative state, discriminated by its API source. */
type ActiveBoard = { kind: 'online'; game: OnlineGame } | { kind: 'single'; game: Game };

const sideNames: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };
const statusNames: Record<string, string> = {
  waiting: 'Waiting',
  active: 'Active',
  in_progress: 'In progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
  voided: 'Voided',
};

function onlineBoardView(game: OnlineGame): BoardView {
  return {
    ruleset: game.ruleset,
    size: Math.max(1, Math.round(Math.sqrt(game.position.board.length))),
    board: game.position.board,
    winner: game.position.winner,
    reason: game.position.reason,
    moves: game.moves,
    cursor: game.position.ply,
    total_moves: game.moves.length,
    legal_moves: [],
    capturedKingSquare: game.terminal_king_square,
  };
}
function singleBoardView(game: Game): BoardView {
  return {
    ruleset: game.ruleset,
    size: game.size,
    board: game.board,
    winner: game.winner ?? game.recorded_winner ?? null,
    reason: game.reason,
    moves: game.moves,
    cursor: game.cursor,
    total_moves: game.total_moves,
    legal_moves: [],
  };
}

function DashboardHome({
  accountId,
  onNewSingle,
}: {
  accountId: string;
  onNewSingle?: () => void;
}) {
  const { account } = useAuth();
  const { t, locale } = useI18n();
  const [localOpen, setLocalOpen] = useState(false);
  const [storedRuleset] = useState(readStoredRuleset);
  const loadAccount = useCallback(
    (signal: AbortSignal) =>
      api<AccountHistory>(
        '/personal/single-player?limit=1&status=in_progress',
        undefined,
        undefined,
        signal,
      ),
    [],
  );
  const loadOnline = useCallback(
    (signal: AbortSignal) =>
      api<PersonalOnlineHistory>('/personal/online?limit=25', undefined, undefined, signal),
    [],
  );
  const loadRecent = useCallback(
    (signal: AbortSignal) =>
      api<RecordedPage>('/history/games?dashboard=true', undefined, undefined, signal),
    [],
  );
  const loadLocal = useCallback(
    (signal: AbortSignal) =>
      api<PersonalLocalHistory>(
        '/personal/local?limit=5&status=in_progress',
        undefined,
        undefined,
        signal,
      ),
    [],
  );
  const loadProgress = useCallback(
    (signal: AbortSignal) =>
      api<PersonalProgress>('/personal/progress', undefined, undefined, signal),
    [],
  );
  const loadPortraits = useCallback(
    (signal: AbortSignal) =>
      api<PortraitCatalog>('/portraits/catalog', undefined, undefined, signal),
    [],
  );
  const loadAchievements = useCallback(
    (signal: AbortSignal) =>
      api<AchievementProgress>('/personal/achievements', undefined, undefined, signal),
    [],
  );
  const loadProfile = useCallback(
    (signal: AbortSignal) => api<Profile>('/profile', undefined, undefined, signal),
    [],
  );
  const single = useOnlinePoll(`dashboard-account:${accountId}`, loadAccount, {
    intervalMs: 60000,
  });
  const online = useOnlinePoll(`dashboard-online:${accountId}`, loadOnline, { intervalMs: 10000 });
  const recent = useOnlinePoll(`dashboard-recent:${accountId}`, loadRecent, { intervalMs: 60000 });
  const local = useOnlinePoll(`dashboard-local:${accountId}`, loadLocal, { intervalMs: 60000 });
  const progress = useOnlinePoll(`dashboard-progress:${accountId}`, loadProgress, {
    intervalMs: 0,
  });
  const portraits = useOnlinePoll(`dashboard-portraits:${accountId}`, loadPortraits, {
    intervalMs: 0,
  });
  const achievements = useOnlinePoll(`dashboard-achievements:${accountId}`, loadAchievements, {
    intervalMs: 0,
  });
  const profile = useOnlinePoll(`dashboard-profile:${accountId}`, loadProfile, { intervalMs: 0 });
  const running = runningGames({
    single: single.data ?? undefined,
    online: online.data ?? undefined,
    local: local.data ?? undefined,
  });
  // Your-turn games come first; the first entry is the hero's active match.
  const needsYou = [
    ...running.filter((game) => game.yourTurn),
    ...running.filter((g) => !g.yourTurn),
  ];
  const active = needsYou[0];
  const focusRuleset = active?.ruleset ?? storedRuleset;
  const focusName = t(rulesetName(focusRuleset));
  const activeId = active?.id ?? '';
  const activeOnline = active?.href.startsWith('/online/games/') ?? false;
  const loadActiveBoard = useCallback(
    (signal: AbortSignal): Promise<ActiveBoard | null> => {
      if (!activeId) return Promise.resolve(null);
      return activeOnline
        ? api<OnlineGame>(`/personal/online/${activeId}`, undefined, undefined, signal).then(
            (game) => ({ kind: 'online' as const, game }),
          )
        : api<Game>(`/games/${activeId}`, undefined, undefined, signal).then((game) => ({
            kind: 'single' as const,
            game,
          }));
    },
    [activeId, activeOnline],
  );
  const board = useOnlinePoll(
    `dashboard-board:${accountId}:${activeId || 'none'}`,
    loadActiveBoard,
    { intervalMs: activeId ? 5000 : 0 },
  );
  const loadStanding = useCallback(
    (signal: AbortSignal) =>
      api<LeaderboardData>(
        `/leaderboard?ruleset=${focusRuleset}&limit=1&offset=0`,
        undefined,
        undefined,
        signal,
      ),
    [focusRuleset],
  );
  const standing = useOnlinePoll(`dashboard-standing:${accountId}:${focusRuleset}`, loadStanding, {
    intervalMs: 0,
  });
  const lastBoardVersion = useRef<{ id: string; version: number } | null>(null);
  const lastBoardResult = useRef<string | null>(null);
  const boardVersion = board.data?.game.version;
  const boardKind = board.data?.kind;
  const boardMode = board.data?.kind === 'single' ? board.data.game.mode : null;
  const boardCompleted =
    board.data?.kind === 'online'
      ? board.data.game.status === 'completed' || board.data.game.status === 'voided'
      : Boolean(
          board.data?.game.winner ||
            (board.data?.kind === 'single' && board.data.game.recorded_winner),
        );
  // A stable terminal key for the active hero, null while it is unfinished. A
  // changed non-null value is a completion or an authoritative correction, so an
  // ordinary move never invalidates progression.
  const boardResult = (() => {
    const current = board.data;
    if (!current) return null;
    if (current.kind === 'online') {
      const game = current.game;
      if (game.status !== 'completed' && game.status !== 'voided') return null;
      return JSON.stringify([
        'online',
        game.id,
        game.status,
        game.position.winner ?? null,
        game.position.reason ?? null,
        game.result?.winner ?? null,
      ]);
    }
    const game = current.game;
    const winner = game.winner ?? game.recorded_winner ?? null;
    if (!winner) return null;
    return JSON.stringify([game.mode, game.id, winner]);
  })();
  useEffect(() => {
    if (!activeId || typeof boardVersion !== 'number') {
      lastBoardVersion.current = null;
      lastBoardResult.current = null;
      return;
    }
    const previousVersion = lastBoardVersion.current;
    lastBoardVersion.current = { id: activeId, version: boardVersion };
    const previousResult = lastBoardResult.current;
    lastBoardResult.current = boardResult;
    const versionChanged =
      previousVersion?.id === activeId && previousVersion.version !== boardVersion;
    const resultChanged = boardResult !== null && boardResult !== previousResult;
    if (!versionChanged && !resultChanged) return;
    // The five-second live board carries a server version. Only actual changes
    // invalidate its owning queue; unchanged clock snapshots do not reload stats.
    if (versionChanged) {
      if (boardKind === 'online') void online.refresh();
      else if (boardMode === 'local') void local.refresh();
      else void single.refresh();
    }
    if (resultChanged) {
      // Completion or correction of the active hero invalidates the authoritative
      // progression projections once. Local and vs-AI milestones only affect the
      // record strip and achievements, while rated online results also move the
      // rating/streak and standing projections.
      void recent.refresh();
      void achievements.refresh();
      if (boardKind === 'online') {
        void progress.refresh();
        void standing.refresh();
      }
    } else if (boardCompleted) {
      void recent.refresh();
    }
  }, [
    activeId,
    boardVersion,
    boardKind,
    boardMode,
    boardCompleted,
    boardResult,
    online.refresh,
    local.refresh,
    single.refresh,
    progress.refresh,
    standing.refresh,
    recent.refresh,
    achievements.refresh,
  ]);
  const resultSignature = online.data
    ? JSON.stringify(
        online.data.statistics?.groups?.map((group) => [
          group.ruleset,
          group.played,
          group.wins,
          group.losses,
          group.draws,
          group.rating,
          // Side-specific wins/losses catch corrections that keep the aggregate
          // win/loss totals but change which seat won.
          group.by_side
            ? (['attackers', 'defenders'] as const).map((side) => {
                const metrics = group.by_side?.[side];
                return metrics
                  ? [side, metrics.played, metrics.wins, metrics.losses, metrics.draws]
                  : [side, null];
              })
            : null,
        ]) ?? [],
      )
    : null;
  const previousResults = useRef<string | null>(null);
  useEffect(() => {
    const previous = previousResults.current;
    previousResults.current = resultSignature;
    if (previous === null || resultSignature === null || previous === resultSignature) return;
    // Also catches results/corrections in another game/client, including when the
    // bounded queue removes a completed hero before its next board poll arrives.
    // Achievements reuse this event-driven poll, so the unlocked-portrait count
    // reflects newly earned (or corrected) awards without another timer.
    void progress.refresh();
    void standing.refresh();
    void recent.refresh();
    void achievements.refresh();
    window.dispatchEvent(new Event('rated-results-changed'));
  }, [resultSignature, progress.refresh, standing.refresh, recent.refresh, achievements.refresh]);
  const onlineGroup = online.data?.statistics?.groups?.find(
    (group) => group.ruleset === focusRuleset,
  );
  const elo =
    (onlineGroup?.played ?? 0) > 0 &&
    onlineGroup?.rating != null &&
    Number.isFinite(onlineGroup.rating)
      ? Math.round(onlineGroup.rating)
      : null;
  const streak = progress.data?.streaks?.by_ruleset?.find(
    (entry) => entry.ruleset === focusRuleset,
  );
  // The progress projection always lists every pool; a pool with no rated games
  // is an explicit empty state rather than a zero that could read as a streak.
  const ratedStreak = streak && streak.rated_games > 0 ? streak : null;
  const catalogItems = portraits.data?.items ?? null;
  const unlockedPortraitCount = catalogItems
    ? catalogItems.filter(
        (item) =>
          item.unlock_achievement_id === null ||
          (achievements.data?.items?.some(
            (achievement) => achievement.id === item.unlock_achievement_id && achievement.unlocked,
          ) ??
            false),
      ).length
    : null;
  const portraitId = profile.data?.portrait_id ?? null;
  const portraitName = catalogItems?.find((item) => item.id === portraitId)?.name ?? null;
  const displayName = profile.data?.display_name?.trim() || account?.username || t('Unassigned');
  const standingRank = standing.data?.me ? Math.round(standing.data.me.rank) : null;
  const heroBoard = board.data
    ? board.data.kind === 'online'
      ? onlineBoardView(board.data.game)
      : singleBoardView(board.data.game)
    : null;
  const heroOnline = board.data?.kind === 'online' ? board.data.game : null;
  const heroSingle = board.data?.kind === 'single' ? board.data.game : null;
  // A waiting online game has no side on the clock yet, so the hero only names a
  // mover once the server reports the game active.
  const heroTurn: Side | null = heroOnline
    ? heroOnline.status === 'active'
      ? heroOnline.position.turn
      : null
    : heroSingle
      ? heroSingle.turn
      : null;
  const yourTurnCount =
    (online.data?.overview?.your_turn ?? 0) + (local.data?.totals?.in_progress ?? 0);
  const runningCount =
    (online.data?.overview?.your_turn ?? 0) +
    (online.data?.overview?.other_active ?? 0) +
    (online.data?.overview?.waiting ?? 0) +
    (local.data?.totals?.in_progress ?? 0) +
    (single.data?.totals?.in_progress ?? 0);
  const gamesLoading =
    single.phase === 'loading' || online.phase === 'loading' || local.phase === 'loading';
  const gamesStale =
    single.phase === 'stale' || online.phase === 'stale' || local.phase === 'stale';
  const heroAwaitsMove =
    active?.yourTurn === true &&
    (!heroSingle || (heroSingle.winner == null && heroSingle.recorded_winner == null)) &&
    (!heroOnline ||
      (heroOnline.status === 'active' &&
        heroOnline.position.turn ===
          online.data?.games.find((game) => game.id === activeId)?.own_side));
  const unverified = account?.email_verified === false;
  const number = new Intl.NumberFormat(locale);
  const percent = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 0 });
  return (
    <section className="personal-dashboard arena-dashboard">
      <PageHeading title={t('Dashboard')} />
      <header className="dashboard-intro">
        <div className="dashboard-intro-copy">
          <p className="dashboard-kicker">{t('THE ARENA')}</p>
          <p className="dashboard-intro-title">{t('Your place at the table.')}</p>
          <p className="dashboard-intro-subtitle">
            {t('Take your turn. Outwit your rivals. Earn your saga.')}
          </p>
        </div>
        <span className="dashboard-status-badge" role="status">
          {gamesLoading
            ? t('Loading your games…')
            : gamesStale
              ? t('Game totals unavailable or stale.')
              : yourTurnCount > 0
                ? t(
                    yourTurnCount === 1
                      ? '{count} game awaits your move'
                      : '{count} games await your move',
                    { count: number.format(yourTurnCount) },
                  )
                : t('No games await your move.')}
        </span>
      </header>
      {unverified && (
        <p role="alert" className="dashboard-verification-warning">
          {t('Verify your email address before creating or joining online games.')}{' '}
          <a href="/profile#email-verification" onClick={surfaceLink}>
            {t('Verify your email in your profile.')}
          </a>
        </p>
      )}
      <div className="dashboard-layout">
        <div className="dashboard-main">
          <section className="arena-hero arena-surface" aria-labelledby="dashboard-hero-heading">
            <div className="arena-hero-copy">
              <p className="arena-kicker">
                {active
                  ? `${t(heroAwaitsMove ? 'YOUR TURN' : (statusNames[heroOnline?.status ?? active.status] ?? 'Active'))} · ${focusName}`
                  : t('THE TABLE IS SET')}
              </p>
              <h2 id="dashboard-hero-heading" className="section-title arena-hero-headline">
                {active ? (
                  heroAwaitsMove ? (
                    <>
                      {t('One move closer to')}{' '}
                      <span className="arena-hero-emphasis">{t('victory.')}</span>
                    </>
                  ) : (
                    t('Your match is waiting.')
                  )
                ) : (
                  t('Start a new saga.')
                )}
              </h2>
              {active ? (
                <p className="arena-hero-meta">
                  <span>
                    {active.opponent
                      ? t('{me} vs {opponent}', { me: displayName, opponent: active.opponent })
                      : [focusName, active.detail ? t(active.detail) : null]
                          .filter(Boolean)
                          .join(' · ')}
                  </span>
                  {heroTurn && (
                    <>
                      {' · '}
                      <span>{t('{side} to move', { side: t(sideNames[heroTurn]) })}</span>
                    </>
                  )}
                  {heroOnline?.status === 'active' && heroTurn && heroOnline.deadline && (
                    <>
                      {' · '}
                      <DashboardCountdown
                        key={`${accountId}:${activeId}`}
                        deadline={heroOnline.deadline}
                      />
                    </>
                  )}
                </p>
              ) : (
                <p className="arena-hero-meta">
                  {gamesLoading
                    ? t('Loading your games…')
                    : t(
                        runningCount > 0
                          ? 'No running games in this preview.'
                          : 'No games are currently running.',
                      )}
                </p>
              )}
              <div className="arena-hero-actions">
                {active ? (
                  <a className="arena-primary-action" href={active.href} onClick={surfaceLink}>
                    {t('Continue game')}
                    <span className="sr-only">
                      {': '}
                      {t(active.label)}
                    </span>
                  </a>
                ) : (
                  <button
                    className="arena-primary-action"
                    type="button"
                    disabled={unverified}
                    onClick={() => navigate('/play?setup=online')}
                  >
                    {t('Play online')}
                  </button>
                )}
                <button
                  className="button secondary"
                  type="button"
                  onClick={() => setLocalOpen(true)}
                >
                  {t('Local 1v1')}
                </button>
                <button className="button secondary" type="button" onClick={onNewSingle}>
                  {t('Play vs AI')}
                </button>
              </div>
            </div>
            <div className="arena-hero-board">
              {heroBoard ? (
                <Board game={heroBoard} selected={null} onSelect={() => {}} readOnly disabled />
              ) : (
                <div className="arena-hero-empty" role="status">
                  {board.phase === 'loading' && active ? (
                    <p>{t('Loading your games…')}</p>
                  ) : (
                    <p>{t('No game on the table yet.')}</p>
                  )}
                </div>
              )}
            </div>
          </section>
          <section
            className="arena-surface dashboard-needs-you"
            aria-labelledby="needs-you-heading"
          >
            <div className="section-heading">
              <h2 id="needs-you-heading" className="section-title">
                {t('Games that need you')}
              </h2>
              <a href="/archive" onClick={surfaceLink}>
                {t('Game archive')} →
              </a>
            </div>
            {runningCount > needsYou.length && (
              <p>
                {t('{shown} of {total} running games shown.', {
                  shown: number.format(needsYou.length),
                  total: number.format(runningCount),
                })}
              </p>
            )}
            {gamesStale ? (
              <p role="alert">
                {t('History unavailable or stale. Retry to load current records.')}{' '}
                <button
                  className="button secondary"
                  onClick={() => {
                    void single.refresh();
                    void online.refresh();
                    void local.refresh();
                  }}
                >
                  {t('Retry')}
                </button>
              </p>
            ) : gamesLoading ? (
              <p>{t('Loading your games…')}</p>
            ) : !needsYou.length ? (
              <p>
                {t(
                  runningCount > 0
                    ? 'No running games in this preview.'
                    : 'No games are currently running.',
                )}
              </p>
            ) : (
              <ul className="dashboard-game-list">
                {needsYou.map((game) => (
                  <li key={game.id} className={game.yourTurn ? 'your-turn' : undefined}>
                    <span
                      className={`badge dashboard-game-status ${game.yourTurn ? 'waiting' : ''}`}
                    >
                      {game.yourTurn ? t('Your turn') : t(statusNames[game.status] ?? 'Active')}
                    </span>
                    <span className="dashboard-game-detail">
                      <strong>
                        {game.opponent
                          ? t('vs {opponent}', { opponent: game.opponent })
                          : t(game.label)}
                      </strong>
                      <small>
                        {t(game.label)}
                        {game.detail ? ` · ${t(game.detail)}` : ''}
                      </small>
                    </span>
                    <a className="button secondary" href={game.href} onClick={surfaceLink}>
                      {game.yourTurn || game.href.startsWith('/games/') ? t('Continue') : t('View')}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
        <aside className="dashboard-aside">
          <section
            className="arena-surface dashboard-profile"
            aria-labelledby="warrior-profile-heading"
          >
            <div className="section-heading">
              <h2 id="warrior-profile-heading" className="section-title">
                {t('Warrior profile')}
              </h2>
              <a href="/profile" onClick={surfaceLink}>
                {t('Customize')} →
              </a>
            </div>
            <div className="dashboard-profile-identity">
              <span className="dashboard-portrait" aria-hidden="true">
                {portraitId ? (
                  <Portrait id={portraitId} />
                ) : (
                  (Array.from(displayName)[0]?.toUpperCase() ?? '?')
                )}
              </span>
              <span className="dashboard-profile-name">
                <strong>{displayName}</strong>
                <small>{portraitName ?? t('No portrait chosen yet.')}</small>
              </span>
            </div>
            <p className="dashboard-profile-rank">
              {standing.phase === 'loading'
                ? t('Loading your standing…')
                : standingRank === null
                  ? t('You are not ranked in this pool yet.')
                  : t('{ruleset} · Rank #{rank}', {
                      ruleset: focusName,
                      rank: number.format(standingRank),
                    })}
            </p>
            {standing.phase === 'stale' && (
              <p role="alert">
                {t('Leaderboard unavailable or stale. Retry to load current standings.')}{' '}
                <button className="button secondary" onClick={() => void standing.refresh()}>
                  {t('Retry')}
                </button>
              </p>
            )}
          </section>
          <section className="dashboard-stats" aria-labelledby="dashboard-stats-heading">
            <div className="section-heading">
              <h2 id="dashboard-stats-heading" className="section-title">
                {t('Your standing')}
              </h2>
              <a href="/statistics" onClick={surfaceLink}>
                {t('Detailed statistics')}
              </a>
            </div>
            <div className="dashboard-stat-grid">
              <article className="arena-surface dashboard-stat" aria-labelledby="stat-elo-heading">
                <h3 id="stat-elo-heading">{t('{ruleset} Elo', { ruleset: focusName })}</h3>
                <strong>{elo === null ? '—' : number.format(elo)}</strong>
                <small>
                  {elo === null
                    ? t('No rated games in this pool yet.')
                    : t('{count} rated games', {
                        count: number.format(onlineGroup?.played ?? 0),
                      })}
                </small>
              </article>
              <article
                className="arena-surface dashboard-stat"
                aria-labelledby="stat-win-rate-heading"
              >
                <h3 id="stat-win-rate-heading">{t('Win rate')}</h3>
                <strong>
                  {onlineGroup?.win_rate == null ? '—' : percent.format(onlineGroup.win_rate)}
                </strong>
                <small>
                  {onlineGroup?.win_rate == null
                    ? t('No completed games yet.')
                    : t('{count} rated games', {
                        count: number.format(onlineGroup?.played ?? 0),
                      })}
                </small>
              </article>
              <article
                className="arena-surface dashboard-stat"
                aria-labelledby="stat-streak-heading"
              >
                <h3 id="stat-streak-heading">{t('Current streak')}</h3>
                <strong>
                  {ratedStreak
                    ? t('{count} wins', { count: number.format(ratedStreak.rated_win_current) })
                    : '—'}
                </strong>
                <small>
                  {ratedStreak
                    ? t('Highest: {count}', { count: number.format(ratedStreak.rated_win_best) })
                    : t('No rated win streak yet.')}
                </small>
              </article>
              <article
                className="arena-surface dashboard-stat"
                aria-labelledby="stat-portraits-heading"
              >
                <h3 id="stat-portraits-heading">
                  <a href="/portraits" onClick={surfaceLink}>
                    {t('Unlocked portraits')}
                  </a>
                </h3>
                <strong>
                  {unlockedPortraitCount === null || !catalogItems
                    ? '—'
                    : `${number.format(unlockedPortraitCount)} / ${number.format(catalogItems.length)}`}
                </strong>
                <small>{t('Earned from achievements.')}</small>
              </article>
            </div>
          </section>
          <Leaderboard compact fullStandings />
        </aside>
      </div>
      <section className="panel dashboard-recent" aria-labelledby="recent-heading">
        <div className="record-strip-row">
          <div className="record-strip-heading">
            <h2 id="recent-heading">{t('Recorded games')}</h2>
            <a href="/archive" onClick={surfaceLink}>
              {t('Game archive')} →<span className="sr-only">: {t('Recorded games')}</span>
            </a>
          </div>
          <DashboardHealth phase={recent.phase} refresh={recent.refresh} />
          {recent.phase === 'ready' && !recent.data?.games?.length ? (
            <p>{t('No completed games yet.')}</p>
          ) : (
            <RecentRecordStrip games={recent.data?.games ?? []} />
          )}
        </div>
      </section>
      {localOpen && <LocalGameDialog onClose={() => setLocalOpen(false)} />}
    </section>
  );
}
/**
 * The dashboard's recent-record strip: at most three records in one row, each a
 * single link that opens its replay (or the live board) in one action. The full
 * table stays in the Hall of records, and the strip keeps one height whether or
 * not the account has records, so the dashboard stays a one-screen composition.
 */
function RecentRecordStrip({ games }: { games: RecordedGame[] }) {
  const { t, locale } = useI18n();
  const format = new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric' });
  return (
    <ul className="record-strip">
      {games.slice(0, 3).map((game) => {
        const live = isLiveRecord(game);
        const attackers = participantLabel(game.attackers, t);
        const defenders = participantLabel(game.defenders, t);
        return (
          <li key={`${game.category}:${game.id}`}>
            <a
              className="record-strip-item"
              href={live ? liveUrl(game) : reviewUrl(game)}
              onClick={surfaceLink}
            >
              {/* The action prefix and the record share one element, with an explicit
                  space between them, so the link's accessible name reads as a
                  sentence instead of concatenating the two spans. */}
              <span className="record-strip-title">
                <span className="sr-only">{t(live ? 'Watch live' : 'Open review')}:</span>{' '}
                {t(rulesetName(game.ruleset))} · {attackers} vs {defenders}
              </span>{' '}
              <span className="record-strip-meta">
                {live && <span className="record-live">{t('Live')}</span>}
                {winnerLabel(game, t)} ·{' '}
                <time dateTime={game.recorded_at}>{format.format(new Date(game.recorded_at))}</time>
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
function DashboardHealth({ phase, refresh }: { phase: string; refresh: () => Promise<unknown> }) {
  const { t } = useI18n();
  return (
    <>
      {phase === 'loading' && <p role="status">{t('Loading your games…')}</p>}
      {phase === 'stale' && (
        <p role="alert">
          {t('History unavailable or stale. Retry to load current records.')}{' '}
          <button className="button secondary" onClick={() => void refresh()}>
            {t('Retry')}
          </button>
        </p>
      )}
    </>
  );
}
export default function PersonalDashboard({ onNewSingle }: { onNewSingle?: () => void }) {
  const { account } = useAuth();
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const changed = () => setPath(location.pathname);
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  if (!account) return null;
  const match = /^\/dashboard\/single-player\/([A-Za-z0-9_-]{1,100})$/.exec(path);
  return match ? (
    <RecordedReview
      key={account.id + ':' + match[1]}
      id={match[1]}
      category="account-single-player"
      personal
      accountId={account.id}
    />
  ) : (
    <DashboardHome key={account.id} accountId={account.id} onNewSingle={onNewSingle} />
  );
}
