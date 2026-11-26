import { ArenaShell, SHELL_MAIN_ID, WorkshopShell } from './ArenaShell';
import { localizedRules } from './locales/rules-de';
import { t, useI18n } from './i18n';
import { ProgressionProvider } from './Progression';
import { PROGRESSION_POLL_MS } from './useAchievementNotifications';
import { useCallback, useEffect, useRef, useState, lazy, Suspense } from 'react';
import Board, { coordinate } from './Board';
import { api, errorMessage } from './api';
import { notify } from './notifications';
import type { AIProfile, Game, Ruleset, Side } from './types';
import { useGameAnalysis } from './useGameAnalysis';
import { deferredFeature } from './deferredFeature';
import Rune, { participantName } from './Rune';
import { accountAttribution } from './accountIdentity';

import Modal from './Modal';
import NewGame from './NewGame';
import PersonalDashboard from './PersonalDashboard';
import Leaderboard from './Leaderboard';
import ActiveGameSwitcher from './ActiveGameSwitcher';
import { useAuth } from './Auth';
import { canonicalLocation, navigate } from './navigation';
import PlayPage from './PlayPage';
import GameLayout from './GameLayout';
import GameDetailsPanel from './GameDetailsPanel';
import GameResult from './GameResult';
import ArmiesPanel from './ArmiesPanel';
import HistoryPanel from './HistoryPanel';
import Battlefield from './Battlefield';
import { sideGlyph } from './sideGlyph';
import { usePlayHistory } from './usePlayHistory';
import { useModelCatalogue } from './useModelCatalogue';
import { useNewGameSetup } from './useNewGameSetup';
import { PlayTabsProvider } from './PlayTabs';

const ReactMarkdown = deferredFeature(() => import('react-markdown'));
const Archive = deferredFeature(() => import('./Archive'));
const Training = deferredFeature(() => import('./Training'));
const Tournaments = deferredFeature(() => import('./Tournaments'));
const Online = deferredFeature(() => import('./Online'));
const Leagues = deferredFeature(() => import('./Leagues'));
const StatisticsPage = deferredFeature(() => import('./StatisticsPage'));
const ProfilePage = deferredFeature(() => import('./ProfilePage'));
const PlayLanding = deferredFeature(() => import('./PlayLanding'));
const AgentAnalysis = deferredFeature(() => import('./AgentAnalysis'));
const AnalyticsSettingsPanel = deferredFeature(() =>
  import('./AdvisorSelect').then((module) => ({ default: module.AnalyticsSettingsPanel })),
);
// Route-level code splits keep the gallery and tournament calendar off unrelated pages.
const PortraitHall = lazy(() => import('./PortraitHall'));
const PlayerTournaments = lazy(() => import('./PlayerTournaments'));

type Tab =
  | 'statistics'
  | 'dashboard'
  | 'online'
  | 'play'
  | 'archive'
  | 'leaderboard'
  | 'profile'
  | 'portraits'
  | 'training'
  | 'tournaments'
  | 'leagues'
  | 'player-tournaments';
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };

export default function App() {
  useI18n();
  const { account, logout } = useAuth();
  const permissions = account?.permissions ?? [];
  const [path, setPath] = useState(canonicalLocation);
  const tab: Tab =
    path.startsWith('/online/games/') || path.split('?')[0] === '/online'
      ? 'online'
      : /^\/player-tournaments(?:\/ptournament-[a-f0-9]{32})?$/.test(path.split('?')[0])
        ? 'player-tournaments'
        : path.split('?')[0] === '/portraits'
          ? 'portraits'
          : path.split('?')[0] === '/profile'
            ? 'profile'
            : path.split('?')[0] === '/statistics'
              ? 'statistics'
              : path.startsWith('/games/') || path.split('?')[0] === '/play'
                ? 'play'
                : path.split('?')[0] === '/archive'
                  ? 'archive'
                  : path.split('?')[0] === '/leaderboard'
                    ? 'leaderboard'
                    : path.startsWith('/training') && permissions.includes('training')
                      ? 'training'
                      : path.startsWith('/tournaments') && permissions.includes('tournaments')
                        ? 'tournaments'
                        : path.split('?')[0] === '/leagues'
                          ? 'leagues'
                          : 'dashboard';
  const playEntry = path.split('?')[0] === '/play';
  useEffect(() => {
    const changed = () => setPath(canonicalLocation());
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  useEffect(() => {
    if (
      /^\/(?:games\/(?!online-)[A-Za-z0-9_-]{1,100}|online\/games\/online-[0-9a-f]{32})$/.test(
        path.split('?')[0],
      )
    ) {
      try {
        localStorage.setItem('hnefatafl-play-route', path.split('?')[0]);
      } catch {
        /* Optional browser preference. */
      }
    }
  }, [path]);
  const [rulesets, setRulesets] = useState<Ruleset[]>([]);
  const [profiles, setProfiles] = useState<AIProfile[]>([]);
  const { models, refresh: refreshModels } = useModelCatalogue();
  const [liveGame, setGame] = useState<Game | null>(null);
  const playHistory = usePlayHistory(liveGame);
  const game = playHistory.game;
  const reviewingRef = playHistory.cursorRef;
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const {
    open: newOpen,
    start: startSetup,
    cancel: cancelSetup,
    owns: setupOwns,
    session: setupSession,
  } = useNewGameSetup();
  // The generation that opened the current dialog. A creation completion is
  // bound to it rather than to whichever dialog owns the latest generation.
  const activeSetupSession = newOpen ? setupSession() : 0;
  const [rulesOpen, setRulesOpen] = useState(false);
  const [aiRetryDelay, setAiRetryDelay] = useState(900);
  const [initializing, setInitializing] = useState(true);
  const aiLock = useRef(false);

  const currentGame = useRef<Game | null>(null);
  const loadedRoute = useRef<string | null>(null);
  currentGame.current = liveGame;
  const analysis = useGameAnalysis(
    game,
    tab === 'play' && !playEntry,
    playHistory.reviewing,
    '/games',
    playHistory.reviewing,
    account?.id ?? null,
  );

  const reportError = useCallback((message: unknown) => {
    notify.error(errorMessage(message));
  }, []);

  const refreshOptions = useCallback(async () => {
    const [rules, ais] = await Promise.all([
      api<Ruleset[]>('/rulesets'),
      api<AIProfile[]>('/ai/profiles'),
      refreshModels(),
    ]);
    setRulesets(rules);
    setProfiles(ais);
    return rules;
  }, [refreshModels]);
  const startSinglePlayer = useCallback(() => {
    const route = canonicalLocation();
    startSetup(
      async (signal) => {
        const [rules, ais] = await Promise.all([
          api<Ruleset[]>('/rulesets', undefined, undefined, signal),
          api<AIProfile[]>('/ai/profiles', undefined, undefined, signal),
        ]);
        // Do not publish options or open a stale setup dialog if the user moved
        // elsewhere while the options were loading. The hook publishes them
        // only after it verifies this operation still owns the generation.
        if (route !== canonicalLocation()) return null;
        return { rulesets: rules, profiles: ais };
      },
      ({ rulesets: rules, profiles: ais }) => {
        setRulesets(rules);
        setProfiles(ais);
      },
      reportError,
    );
  }, [startSetup, reportError]);
  // Any route or account change invalidates a pending setup operation and closes
  // the dialog it owned, including Browser Back/Forward. This is what stops an
  // obsolete response from reopening a dialog after navigating away and back.
  useEffect(() => {
    cancelSetup();
  }, [path, account?.id, cancelSetup]);
  useEffect(() => {
    if (
      tab === 'dashboard' &&
      new URLSearchParams(location.search).get('setup') === 'single-player'
    )
      startSinglePlayer();
  }, [path, tab, startSinglePlayer]);
  useEffect(() => {
    if (tab === 'training' || tab === 'tournaments')
      void refreshOptions().catch((e) => reportError(e));
  }, [tab, refreshOptions]);
  useEffect(() => {
    const match = /^\/games\/((?!online-)[A-Za-z0-9_-]{1,100})$/.exec(path.split('?')[0]);
    if (!match) {
      loadedRoute.current = null;
      return;
    }
    if (loadedRoute.current === match[1] && currentGame.current?.id === match[1]) return;
    loadedRoute.current = match[1];
    let alive = true;
    const controller = new AbortController();
    currentGame.current = null;
    setGame(null);
    setInitializing(true);
    void Promise.all([
      refreshOptions(),
      api<Game>(`/games/${match[1]}`, undefined, undefined, controller.signal),
    ])
      .then(([, saved]) => {
        if (alive) {
          setGame(saved);
          setSelected(null);
          setAiRetryDelay(900);
        }
      })
      .catch((e) => {
        if (alive) reportError(e);
      })
      .finally(() => {
        if (alive) setInitializing(false);
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [path, refreshOptions]);
  useEffect(() => {
    const match = /^\/games\/((?!online-)[A-Za-z0-9_-]{1,100})$/.exec(path.split('?')[0]);
    if (!match) return;
    const controller = new AbortController();
    let running = false;
    let revision = 0;
    // Refresh identity on existing wake signals, without polling or replacing a
    // newer board/move response with a background read of an older position.
    const refresh = async () => {
      revision++;
      if (running) return;
      running = true;
      let lastRead: number;
      do {
        const expected = revision;
        lastRead = expected;
        try {
          const fresh = await api<Game>(
            `/games/${match[1]}`,
            undefined,
            undefined,
            AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
          );
          if (controller.signal.aborted || expected !== revision) continue;
          setGame((current) => {
            if (
              controller.signal.aborted ||
              expected !== revision ||
              !current ||
              current.id !== fresh.id ||
              current.version > fresh.version
            )
              return current;
            const players = { ...current.players };
            let changed = false;
            for (const side of ['attackers', 'defenders'] as Side[]) {
              const saved = current.players[side];
              const next = fresh.players[side];
              if (
                saved.kind === 'human' &&
                next.kind === 'human' &&
                saved.account_id &&
                saved.account_id === next.account_id &&
                (saved.name !== next.name || saved.username !== next.username)
              ) {
                players[side] = { ...saved, name: next.name, username: next.username };
                changed = true;
              }
            }
            return changed ? { ...current, players } : current;
          });
        } catch {
          /* Keep the game usable offline; navigation or the next wake retries. */
        }
      } while (!controller.signal.aborted && lastRead !== revision);
      running = false;
    };
    const wake = () => {
      if (currentGame.current?.id === match[1]) void refresh();
    };
    const visible = () => {
      if (document.visibilityState === 'visible') wake();
    };
    window.addEventListener('focus', wake);
    window.addEventListener('online', wake);
    window.addEventListener('profile-changed', wake);
    document.addEventListener('visibilitychange', visible);
    return () => {
      controller.abort();
      window.removeEventListener('focus', wake);
      window.removeEventListener('online', wake);
      window.removeEventListener('profile-changed', wake);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [path, account?.id]);
  useEffect(() => {
    if (game) localStorage.setItem('hnefatafl-game', game.id);
  }, [game?.id]);

  const action = useCallback(async (name: string, body: Record<string, unknown> = {}) => {
    const active = currentGame.current;
    if (
      !active ||
      aiLock.current ||
      ((name === 'moves' || name === 'ai-move') && reviewingRef.current !== null)
    )
      return false;
    aiLock.current = true;
    setBusy(true);
    try {
      const next = await api<Game>(`/games/${active.id}/${name}`, {
        ...body,
        version: active.version,
      });
      if (currentGame.current?.id === active.id) {
        setGame(next);
        if (!active.recorded_winner && next.recorded_winner)
          window.dispatchEvent(new Event('progression-changed'));
        setAiRetryDelay(900);
        if (next.id !== active.id) {
          loadedRoute.current = next.id;
          navigate(`/games/${next.id}`);
        }
        setSelected(null);
        return true;
      }
      return false;
    } catch (e) {
      if (currentGame.current?.id === active.id) {
        reportError(e);
        try {
          const fresh = await api<Game>(`/games/${active.id}`);
          if (currentGame.current?.id === active.id) setGame(fresh);
        } catch {
          /* Preserve the game while a connection is unavailable. */
        }
        if (currentGame.current?.id === active.id && name === 'ai-move')
          setAiRetryDelay((delay) => Math.min(delay * 2, 30000));
      }
      return false;
    } finally {
      setBusy(false);
      aiLock.current = false;
    }
  }, []);
  const activeAI =
    !!liveGame &&
    !liveGame.winner &&
    !liveGame.recorded_winner &&
    Object.values(liveGame.players).every(
      (player) => !player.account_id || player.account_id === account?.id,
    ) &&
    liveGame.players[liveGame.turn]?.kind === 'ai';
  const outcome = game?.winner ?? game?.recorded_winner ?? null;
  useEffect(() => {
    if (!liveGame || !activeAI || busy || playHistory.reviewing || tab !== 'play' || playEntry)
      return;
    const timer = window.setTimeout(() => void action('ai-move'), aiRetryDelay);
    return () => window.clearTimeout(timer);
  }, [liveGame, activeAI, busy, playHistory.reviewing, aiRetryDelay, action, tab, playEntry]);

  const mayPlay =
    !!game &&
    !playHistory.reviewing &&
    game.players[game.turn]?.kind === 'human' &&
    (!game.players[game.turn].account_id || game.players[game.turn].account_id === account?.id);
  const returnToLive = async () => {
    const active = currentGame.current;
    if (!active) return;
    if (!playHistory.reviewing && active.cursor === active.total_moves) return;
    // Restore an old client's saved cursor without branching or changing its moves.
    if (
      active.cursor < active.total_moves &&
      !(await action('seek', { cursor: active.total_moves }))
    )
      return;
    if (active.cursor === active.total_moves) {
      try {
        const fresh = await api<Game>(`/games/${active.id}`);
        if (currentGame.current?.id !== active.id) return;
        setGame(fresh);
      } catch (error) {
        reportError(error);
        return;
      }
    }
    playHistory.showAt(null);
    setSelected(null);
  };
  const showHistory = (ply: number) => {
    if (!game || busy) return;
    if (ply === game.total_moves) void returnToLive();
    else {
      playHistory.showAt(ply);
      setSelected(null);
    }
  };

  const selectSquare = (index: number) => {
    if (!game || !mayPlay || game.winner || game.recorded_winner || busy || activeAI) return;
    if (index < 0) {
      setSelected(null);
      return;
    }
    if (
      selected !== null &&
      game.legal_moves.some((move) => move.source === selected && move.target === index)
    ) {
      void action('moves', { source: selected, target: index });
      return;
    }
    setSelected(
      game.legal_moves.some((move) => move.source === index)
        ? index === selected
          ? null
          : index
        : null,
    );
  };

  const removeGames = useCallback((ids: string[]) => {
    if (!currentGame.current || !ids.includes(currentGame.current.id)) return;
    // Invalidate the ref before any in-flight move can restore the removed game.
    currentGame.current = null;
    setGame(null);
    setSelected(null);
    setRulesOpen(false);
    localStorage.removeItem('hnefatafl-game');
  }, []);
  const activeRules = rulesets.find((r) => r.id === game?.ruleset);
  const gameMetadata = game
    ? [
        {
          label: t('Game mode'),
          value:
            game.mode === 'local'
              ? t('Local 1v1')
              : game.mode === 'human-human'
                ? t('Pass & play')
                : game.mode === 'human-ai'
                  ? t('Human vs AI')
                  : t('AI vs AI'),
        },
        ...(game.seed !== undefined ? [{ label: t('Random seed'), value: game.seed }] : []),
      ]
    : [];

  const gameDetails = game && (
    <GameDetailsPanel
      ruleset={
        game.ruleset === 'tablut'
          ? t('Tablut')
          : game.ruleset === 'copenhagen'
            ? t('Copenhagen')
            : t(activeRules?.name ?? game.ruleset)
      }
      size={game.size}
      header={
        <>
          <p className="game-description">
            {t(activeRules?.description ?? 'An ancient contest of escape and encirclement.')}
          </p>
          <button className="text-button" onClick={() => setRulesOpen(true)}>
            {t('Read the rules')}
            <span aria-hidden="true">↗</span>
          </button>
        </>
      }
      rows={gameMetadata}
      desktopRows={[]}
      footer={
        outcome !== null && <GameResult winner={outcome} reason={game.reason} labels={titles} />
      }
    />
  );
  const armies = game && (
    <ArmiesPanel
      rows={(['attackers', 'defenders'] as Side[]).map((side) => ({
        side,
        symbol: game.players[side]?.rune ? (
          <Rune rune={game.players[side].rune} />
        ) : (
          sideGlyph(side)
        ),
        name:
          game.players[side]?.kind === 'human'
            ? game.players[side].name || t('Unassigned')
            : participantName(game.players[side], t(titles[side])),
        detail: [
          accountAttribution(game.players[side]),
          `${t(titles[side])} · ${
            game.players[side]?.kind === 'ai'
              ? t(String(game.players[side].config.algorithm ?? 'Computer'))
              : t('Human')
          }`,
        ]
          .filter(Boolean)
          .join(' · '),
        toMove: game.turn === side && outcome === null,
      }))}
      captured={game.captured ?? { attackers: 0, defenders: 0 }}
      metadata={gameMetadata}
    />
  );
  const analyticsSettings = game && (
    <AnalyticsSettingsPanel
      game={game}
      profiles={profiles}
      models={models}
      settings={analysis.settings}
      onChange={analysis.updateSettings}
      pending={analysis.settingsPending}
      error={analysis.settingsError}
    />
  );
  const switcher = account && (
    <ActiveGameSwitcher
      accountId={account.id}
      currentId={game?.id ?? ''}
      open={(entry) =>
        navigate(entry.type === 'online' ? `/online/games/${entry.id}` : `/games/${entry.id}`)
      }
    />
  );
  const agentAnalysis = game && analysis.show ? <AgentAnalysis game={game} {...analysis} /> : null;
  const workshop = tab === 'training' || tab === 'tournaments';
  const shellCurrent = tab;
  const Shell = workshop ? WorkshopShell : ArenaShell;
  // Only the Dashboard observes account-wide result totals and can refresh the
  // shared read from its own polls. Game routes only see the current game, so a
  // local/single-player board keeps the low-frequency backstop for completions
  // that happen elsewhere; every other Arena route does the same.
  const progressionPoll = tab === 'dashboard' ? 0 : PROGRESSION_POLL_MS;

  return (
    <ProgressionProvider owner={account?.id} intervalMs={progressionPoll}>
      <PlayTabsProvider key={account?.id}>
        <Shell account={account} logout={logout} current={shellCurrent}>
          <main id={SHELL_MAIN_ID} tabIndex={-1}>
            {tab === 'portraits' && (
              <Suspense
                fallback={
                  <div className="empty-state">
                    <div className="spinner" />
                    <h2 role="status">{t('Loading your portraits…')}</h2>
                  </div>
                }
              >
                <PortraitHall />
              </Suspense>
            )}
            {tab === 'statistics' && <StatisticsPage />}
            {tab === 'dashboard' && <PersonalDashboard onNewSingle={startSinglePlayer} />}
            {tab === 'play' &&
              (playEntry ? (
                <PlayLanding onNewSingle={startSinglePlayer} creatingSingle={newOpen} />
              ) : (
                <PlayPage
                  className="play-page"
                  heading={t(game?.mode === 'local' ? 'Local 1v1 game' : 'Single player game')}
                >
                  {initializing ? (
                    <div className="empty-state">
                      <div className="spinner" />
                      <h2>{t('Setting the table…')}</h2>
                    </div>
                  ) : !game ? (
                    <section className="welcome-panel">
                      <div className="welcome-rune">ᛟ</div>
                      <h2>{t('The king awaits.')}</h2>
                      <p>
                        {t(
                          'Lead the encircling attackers, or guide the outnumbered king to freedom. Choose a historic ruleset and begin your first saga.',
                        )}
                      </p>
                      <button className="button primary" onClick={() => navigate('/dashboard')}>
                        {t('Back to the Great Hall')}
                        <span aria-hidden="true">↗</span>
                      </button>
                      <div className="variant-pills">
                        {rulesets.map((r) => (
                          <span key={r.id}>
                            {t(r.name)}{' '}
                            <small>
                              {r.size} × {r.size}
                            </small>
                          </span>
                        ))}
                      </div>
                    </section>
                  ) : (
                    <GameLayout
                      key={game.id}
                      switcher={switcher}
                      desktop={{
                        sidebar: (
                          <>
                            {gameDetails}
                            {armies}
                          </>
                        ),
                        analytics: (
                          <>
                            {agentAnalysis}
                            {analyticsSettings}
                          </>
                        ),
                        moveCount: game.total_moves,
                      }}
                      mobile={{
                        title:
                          game.ruleset === 'tablut'
                            ? t('Tablut')
                            : game.ruleset === 'copenhagen'
                              ? t('Copenhagen')
                              : t(activeRules?.name ?? game.ruleset),
                        subtitle: (
                          <>
                            {game.size} × {game.size} ·{' '}
                            {outcome
                              ? outcome === 'draw'
                                ? t('Draw')
                                : t('{side} win', { side: t(titles[outcome]) })
                              : t('{side} to move', { side: t(titles[game.turn]) })}{' '}
                            · {t('Move')} {game.cursor}
                          </>
                        ),
                        players: armies,
                        analytics: agentAnalysis,
                        settings: analyticsSettings,
                        moveCount: game.total_moves,
                      }}
                      sidebar={
                        <>
                          {gameDetails}
                          {armies}
                          {agentAnalysis}
                          <div className="strategy-note">
                            <span aria-hidden="true">ᛉ</span>
                            <p>
                              {game.turn === 'attackers'
                                ? t('Close the circle. Cut off the king’s path to freedom.')
                                : t('Protect the king. Find the opening in the siege.')}
                            </p>
                          </div>
                        </>
                      }
                      battlefield={
                        <Battlefield
                          label={t('Game')}
                          board={
                            <Board
                              game={game}
                              selected={selected}
                              onSelect={selectSquare}
                              disabled={busy || !mayPlay || !!game.winner || !!game.recorded_winner}
                              readOnly={playHistory.reviewing || !mayPlay}
                              hint={analysis.report?.hint?.move ?? null}
                            />
                          }
                        />
                      }
                      history={
                        <HistoryPanel
                          status={
                            playHistory.reviewing && (
                              <div className="history-review-status">
                                <span>{t('Read-only history')}</span>
                                <button
                                  className="button secondary small"
                                  onClick={() => void returnToLive()}
                                  disabled={busy}
                                >
                                  {t('Return to live board')}
                                </button>
                                {playHistory.loading && (
                                  <span role="status">{t('Loading history…')}</span>
                                )}
                                {playHistory.error && (
                                  <>
                                    <span role="alert">{t('Replay unavailable.')}</span>
                                    <button className="text-button" onClick={playHistory.retry}>
                                      {t('Retry')}
                                    </button>
                                  </>
                                )}
                              </div>
                            )
                          }
                          count={game.total_moves}
                          entries={game.moves.map((move, i) => ({
                            ply: i + 1,
                            notation:
                              move.notation ??
                              `${coordinate(move.source, game.size)} → ${coordinate(move.target, game.size)}`,
                            side: move.side ?? (i % 2 ? 'defenders' : 'attackers'),
                            current: i === game.cursor - 1,
                            future: i >= game.cursor,
                            onSeek: () => showHistory(i + 1),
                            disabled: busy,
                          }))}
                          startAction={{
                            onClick: () => showHistory(0),
                            disabled: busy || game.cursor === 0,
                          }}
                          footer={
                            <div className="history-footer">
                              <span className="status-dot" /> {t('Game saved automatically')}
                            </div>
                          }
                        >
                          {analyticsSettings}
                        </HistoryPanel>
                      }
                    />
                  )}
                </PlayPage>
              ))}
            {tab === 'online' && <Online />}
            {tab === 'leagues' && <Leagues />}
            {tab === 'leaderboard' && <Leaderboard />}
            {tab === 'player-tournaments' && (
              <Suspense
                fallback={
                  <div className="empty-state" role="status">
                    {t('Loading player tournaments…')}
                  </div>
                }
              >
                <PlayerTournaments />
              </Suspense>
            )}
            {tab === 'profile' && <ProfilePage />}
            {tab === 'archive' && <Archive />}
            {tab === 'tournaments' && permissions.includes('tournaments') && (
              <Tournaments
                rulesets={rulesets}
                profiles={profiles}
                models={models}
                onError={reportError}
              />
            )}
            {tab === 'training' && permissions.includes('training') && (
              <Training
                rulesets={rulesets}
                models={models}
                onModelsChanged={() => void refreshOptions().catch((e) => reportError(e))}
                onGamesDeleted={removeGames}
                onError={reportError}
              />
            )}
          </main>
          {tab !== 'online' && newOpen && (
            <NewGame
              rulesets={rulesets}
              profiles={profiles}
              models={models}
              onRefreshModels={refreshModels}
              initialRuleset={
                rulesets.find(
                  (rule) => rule.id === new URLSearchParams(location.search).get('ruleset'),
                )?.id ?? game?.ruleset
              }
              // Guard the form's own side effects (preferences, notifications)
              // by the operation that opened this dialog.
              isCurrentOperation={() => setupOwns(activeSetupSession)}
              onClose={cancelSetup}
              onCreated={(next) => {
                // Bind the completion to the setup operation that opened this
                // dialog, not to whichever dialog owns the latest generation.
                if (!setupOwns(activeSetupSession)) return;
                cancelSetup();
                currentGame.current = next;
                loadedRoute.current = next.id;
                setGame(next);
                setSelected(null);
                setAiRetryDelay(900);
                setInitializing(false);
                navigate(`/games/${next.id}`);
              }}
            />
          )}
          {tab !== 'online' && rulesOpen && activeRules && (
            <Modal
              title={t('{name} rules', { name: t(activeRules.name) })}
              onClose={() => setRulesOpen(false)}
            >
              <article className="rules-document">
                <ReactMarkdown>
                  {localizedRules(activeRules.id, activeRules.rules_markdown)}
                </ReactMarkdown>
              </article>
            </Modal>
          )}
        </Shell>
      </PlayTabsProvider>
    </ProgressionProvider>
  );
}

export { default as Modal } from './Modal';
