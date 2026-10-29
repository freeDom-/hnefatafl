import SiteHeader from './SiteHeader';
import NewGame from './NewGame';
import { localizedRules } from './locales/rules-de';
import { t, useI18n } from './i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import Board, { coordinate } from './Board';
import { api, errorMessage } from './api';
import { notify } from './notifications';
import type { AIProfile, Game, Model, Ruleset, Side } from './types';
import Archive from './Archive';
import Training from './Training';
import Tournaments from './Tournaments';
import AgentAnalysis, { useGameAnalysis } from './AgentAnalysis';
import { AnalyticsSettingsPanel } from './AdvisorSelect';
import Rune, { participantName } from './Rune';

import Online from './Online';
import Modal from './Modal';
import PersonalDashboard from './PersonalDashboard';
import StatisticsPage from './StatisticsPage';
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

type Tab =
  | 'statistics'
  | 'dashboard'
  | 'online'
  | 'play'
  | 'archive'
  | 'leaderboard'
  | 'training'
  | 'tournaments';
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };

export default function App() {
  useI18n();
  const { account, logout } = useAuth();
  const permissions = account?.permissions ?? [];
  const [path, setPath] = useState(canonicalLocation);
  const tab: Tab = path.startsWith('/online/games/')
    ? 'online'
    : path.split('?')[0] === '/statistics'
      ? 'statistics'
      : path.startsWith('/games/')
        ? 'play'
        : path.split('?')[0] === '/archive'
          ? 'archive'
          : path.split('?')[0] === '/leaderboard'
            ? 'leaderboard'
            : path.startsWith('/training') && permissions.includes('training')
              ? 'training'
              : path.startsWith('/tournaments') && permissions.includes('tournaments')
                ? 'tournaments'
                : 'dashboard';
  useEffect(() => {
    const changed = () => setPath(canonicalLocation());
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  const [rulesets, setRulesets] = useState<Ruleset[]>([]);
  const [profiles, setProfiles] = useState<AIProfile[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [liveGame, setGame] = useState<Game | null>(null);
  const playHistory = usePlayHistory(liveGame);
  const game = playHistory.game;
  const reviewingRef = playHistory.cursorRef;
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [aiRetryDelay, setAiRetryDelay] = useState(900);
  const [initializing, setInitializing] = useState(true);
  const aiLock = useRef(false);

  const currentGame = useRef<Game | null>(null);
  const loadedRoute = useRef<string | null>(null);
  currentGame.current = liveGame;
  const analysis = useGameAnalysis(
    game,
    tab === 'play',
    playHistory.reviewing,
    '/games',
    playHistory.reviewing,
  );

  const reportError = useCallback((message: unknown) => {
    notify.error(errorMessage(message));
  }, []);

  const refreshOptions = useCallback(async () => {
    const [rules, ais, savedModels] = await Promise.all([
      api<Ruleset[]>('/rulesets'),
      api<AIProfile[]>('/ai/profiles'),
      api<Model[]>('/models'),
    ]);
    setRulesets(rules);
    setProfiles(ais);
    setModels(savedModels);
    return rules;
  }, []);
  const startSinglePlayer = useCallback(() => {
    void Promise.all([api<Ruleset[]>('/rulesets'), api<AIProfile[]>('/ai/profiles')])
      .then(([rules, ais]) => {
        setRulesets(rules);
        setProfiles(ais);
        setNewOpen(true);
      })
      .catch((e) => reportError(e));
  }, [reportError]);
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
    if (!liveGame || !activeAI || busy || playHistory.reviewing || tab !== 'play') return;
    const timer = window.setTimeout(() => void action('ai-move'), aiRetryDelay);
    return () => window.clearTimeout(timer);
  }, [liveGame, activeAI, busy, playHistory.reviewing, aiRetryDelay, action, tab]);

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
            game.mode === 'human-human'
              ? t('Pass & play')
              : game.mode === 'human-ai'
                ? t('Human vs AI')
                : t('AI vs AI'),
        },
        { label: t('Move'), value: game.cursor },
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
        name: participantName(game.players[side], t(titles[side])),
        detail: `${t(titles[side])} · ${
          game.players[side]?.kind === 'ai'
            ? t(String(game.players[side].config.algorithm ?? 'Computer'))
            : t('Human')
        }`,
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

  return (
    <>
      <SiteHeader account={account} logout={logout} current={tab} />
      <main>
        {tab === 'statistics' && <StatisticsPage />}
        {tab === 'dashboard' && <PersonalDashboard onNewSingle={startSinglePlayer} />}
        {tab === 'play' && (
          <PlayPage className="play-page" heading={t('Single player game')}>
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
                  {t('Back to dashboard')}
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
        )}
        {tab === 'online' && <Online />}
        {tab === 'leaderboard' && <Leaderboard />}
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
      <footer className="site-footer">
        <span>{t('ᛟ HNEFATAFL')}</span>
        <p>{t('An ancient game. An open horizon.')}</p>
        <span>{t('Made for thoughtful moves.')}</span>
      </footer>
      {tab !== 'online' && newOpen && (
        <NewGame
          rulesets={rulesets}
          profiles={profiles}
          initialRuleset={
            rulesets.find((rule) => rule.id === new URLSearchParams(location.search).get('ruleset'))
              ?.id ?? game?.ruleset
          }
          onClose={() => setNewOpen(false)}
          onCreated={(next) => {
            currentGame.current = next;
            loadedRoute.current = next.id;
            setGame(next);
            setSelected(null);
            setAiRetryDelay(900);
            setNewOpen(false);
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
    </>
  );
}

export { default as Modal } from './Modal';

export { default as NewGame } from './NewGame';
