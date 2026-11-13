import PageHeading from './PageHeading';
import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { Lobby } from './Online';
import { useOnlinePoll } from './useOnlinePoll';
import type { AccountHistory, PersonalOnlineHistory, PersonalProgress } from './personalTypes';
import { surfaceLink } from './surfaceNavigation';
import { navigate } from './navigation';
import RecordedGames, { rulesetName, type RecordedPage } from './RecordedGames';
import RecordedReview from './RecordedReview';
import Leaderboard, { readStoredRuleset } from './Leaderboard';
import Board, { type BoardView } from './Board';
import LocalGameDialog from './LocalGameDialog';
import { runningGames, type LocalHistory } from './playModeGames';
import { RemainingTime, remainingSeconds, useNow } from './remainingTime';
import type { Leaderboard as LeaderboardData, OnlineGame } from './onlineTypes';
import type { Profile } from './ProfilePage';
import type { Game, Side } from './types';
import './personalDashboard.css';

/** Authenticated portrait catalog metadata; the asset mapping stays bundled here. */
type PortraitCatalog = {
  default_portrait_id: string;
  items: { id: string; name: string; unlock_achievement_id: string | null }[];
};
/** Own achievement progress, used to count genuinely unlocked portraits. */
type AchievementProgress = {
  items: { id: string; unlocked: boolean }[];
  summary: { unlocked: number; total: number };
};
/** The active game's authoritative state, discriminated by its API source. */
type ActiveBoard = { kind: 'online'; game: OnlineGame } | { kind: 'single'; game: Game };

/**
 * Only catalog ids with a bundled portrait render a picture; the frozen contract
 * keeps asset mapping frontend-controlled, so an unknown id falls back to the
 * account initial rather than a broken image.
 */
const PORTRAIT_ASSETS = new Set([
  'shieldbearer',
  'shieldmaiden',
  'iron-helm',
  'wanderer',
  'skald',
  'fjord-scout',
  'raven-seer',
  'berserker',
  'one-eyed-jarl',
  'valkyrie',
  'einherjar',
]);
const sideNames: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };
const statusNames: Record<string, string> = {
  waiting: 'Waiting',
  active: 'Active',
  in_progress: 'In progress',
};

function portraitSrc(id: string) {
  return `/assets/arena-v2/assets/portraits/${id}.webp`;
}
/** The side to move for an online game, mirroring the online screen's clock rule. */
function onlineRemaining(game: OnlineGame, side: Side, now: number) {
  return game.status === 'active' && game.position.turn === side
    ? remainingSeconds(game.deadline, now)
    : Math.ceil(game.remaining_seconds?.[side] ?? game.time_per_move);
}
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
  const [creation, setCreation] = useState(0);
  const [localOpen, setLocalOpen] = useState(false);
  // The hero clock and the season-agnostic streak read refresh on their own; the
  // remaining-time formatter is coarse enough that a minute keeps it honest.
  const now = useNow(60000);
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
      api<LocalHistory>('/personal/local?limit=5&status=in_progress', undefined, undefined, signal),
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
  const single = useOnlinePoll(`dashboard-account:${accountId}`, loadAccount);
  const online = useOnlinePoll(`dashboard-online:${accountId}`, loadOnline);
  const recent = useOnlinePoll(`dashboard-recent:${accountId}`, loadRecent);
  const local = useOnlinePoll(`dashboard-local:${accountId}`, loadLocal);
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
  const heroRemaining = heroOnline && heroTurn ? onlineRemaining(heroOnline, heroTurn, now) : null;
  const yourTurnCount = needsYou.filter((game) => game.yourTurn).length;
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
          {single.phase === 'loading' && online.phase === 'loading'
            ? t('Loading your games…')
            : yourTurnCount > 0
              ? t('{count} games await your move', { count: number.format(yourTurnCount) })
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
                {active ? `${t('YOUR TURN')} · ${focusName}` : t('THE TABLE IS SET')}
              </p>
              <h2 id="dashboard-hero-heading" className="section-title arena-hero-headline">
                {active
                  ? active.yourTurn
                    ? t('One move closer to victory.')
                    : t('Your match is waiting.')
                  : t('Start a new saga.')}
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
                  {heroRemaining != null && (
                    <>
                      {' · '}
                      <RemainingTime seconds={heroRemaining} />
                    </>
                  )}
                </p>
              ) : (
                <p className="arena-hero-meta">
                  {single.phase === 'loading' || online.phase === 'loading'
                    ? t('Loading your games…')
                    : t('No games are currently running.')}
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
                    onClick={() => setCreation((value) => value + 1)}
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
            {single.phase === 'stale' || online.phase === 'stale' ? (
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
            ) : !needsYou.length ? (
              <p>{t('No games are currently running.')}</p>
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
                {portraitId && PORTRAIT_ASSETS.has(portraitId) ? (
                  <img src={portraitSrc(portraitId)} alt="" />
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
                <h3 id="stat-portraits-heading">{t('Unlocked portraits')}</h3>
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
      <div className="dashboard-overview">
        <Lobby
          accountId={accountId}
          embedded
          creationRequested={creation}
          open={(id) => navigate(`/online/games/${id}`)}
          createAction={
            <button
              className="button primary"
              type="button"
              disabled={unverified}
              onClick={() => setCreation((value) => value + 1)}
            >
              {t('New online game')}
            </button>
          }
        />
        <section className="panel dashboard-recent" aria-labelledby="recent-heading">
          <div className="section-heading">
            <h2 id="recent-heading">{t('Recorded games')}</h2>
            <a href="/archive" onClick={surfaceLink}>
              {t('View all')}
            </a>
          </div>
          <DashboardHealth phase={recent.phase} refresh={recent.refresh} />
          {recent.phase === 'ready' && !recent.data?.games?.length && (
            <p>{t('No completed games yet.')}</p>
          )}
          <RecordedGames games={recent.data?.games ?? []} />
        </section>
      </div>
      {localOpen && <LocalGameDialog onClose={() => setLocalOpen(false)} />}
    </section>
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
