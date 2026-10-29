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
import Battlefield, { BoardToolbar } from './Battlefield';
import { sideGlyph } from './sideGlyph';

type Tab = 'dashboard' | 'online' | 'play' | 'archive' | 'leaderboard' | 'training' | 'tournaments';
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };

export default function App() {
  useI18n();
  const { account, logout } = useAuth();
  const permissions = account?.permissions ?? [];
  const [path, setPath] = useState(canonicalLocation);
  const tab: Tab = path.startsWith('/online/games/')
    ? 'online'
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
  const [game, setGame] = useState<Game | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [restartOpen, setRestartOpen] = useState(false);
  const [paused, setPaused] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const aiLock = useRef(false);

  const currentGame = useRef<Game | null>(null);
  const loadedRoute = useRef<string | null>(null);
  currentGame.current = game;
  const analysis = useGameAnalysis(game, tab === 'play');

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
    if (!active || aiLock.current) return;
    aiLock.current = true;
    setBusy(true);
    try {
      const next = await api<Game>(`/games/${active.id}/${name}`, {
        ...body,
        version: active.version,
      });
      if (currentGame.current?.id === active.id) {
        setGame(next);
        if (next.id !== active.id) {
          loadedRoute.current = next.id;
          navigate(`/games/${next.id}`);
        }
        setSelected(null);
      }
    } catch (e) {
      if (currentGame.current?.id === active.id) {
        reportError(e);
        try {
          const fresh = await api<Game>(`/games/${active.id}`);
          if (currentGame.current?.id === active.id) setGame(fresh);
        } catch {
          /* Preserve the game while a connection is unavailable. */
        }
        setPaused(true);
      }
    } finally {
      setBusy(false);
      aiLock.current = false;
    }
  }, []);
  const activeAI =
    !!game && !game.winner && !game.recorded_winner && game.players[game.turn]?.kind === 'ai';
  const outcome = game?.winner ?? game?.recorded_winner ?? null;
  useEffect(() => setRestartOpen(false), [path, game?.id]);
  useEffect(() => {
    if (!game || !activeAI || paused || busy || restartOpen || tab !== 'play') return;
    const timer = window.setTimeout(() => void action('ai-move'), 900);
    return () => window.clearTimeout(timer);
  }, [game, activeAI, paused, busy, restartOpen, action, tab]);

  const selectSquare = (index: number) => {
    if (!game || game.winner || game.recorded_winner || busy || activeAI) return;
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
    setPaused(true);
    setRulesOpen(false);
    localStorage.removeItem('hnefatafl-game');
  }, []);
  const activeRules = rulesets.find((r) => r.id === game?.ruleset);

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
          <p>{t(activeRules?.description ?? 'An ancient contest of escape and encirclement.')}</p>
          <button className="text-button" onClick={() => setRulesOpen(true)}>
            {t('Read the rules')}
            <span aria-hidden="true">↗</span>
          </button>
          <button
            className="button secondary"
            onClick={() => {
              setNewOpen(true);
              void refreshOptions().catch((e) => reportError(e));
            }}
            disabled={initializing}
          >
            {t('New game')}
          </button>
        </>
      }
      rows={[
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
      ]}
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
  const playback = game && game.mode !== 'human-human' && !game.winner && !game.recorded_winner && (
    <button
      className="button small"
      onClick={() => setPaused((p) => !p)}
      aria-busy={busy && activeAI}
    >
      {paused ? t('▶ Resume AI') : t('Ⅱ Pause AI')}
    </button>
  );
  const agentAnalysis = game && analysis.show ? <AgentAnalysis game={game} {...analysis} /> : null;

  return (
    <>
      <SiteHeader account={account} logout={logout} current={tab} />
      <main>
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
                <button className="button primary" onClick={() => setNewOpen(true)}>
                  {t('Choose your game')}
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
                        disabled={busy || activeAI || !!game.winner || !!game.recorded_winner}
                        hint={analysis.report?.hint?.move ?? null}
                      />
                    }
                    toolbar={
                      <BoardToolbar
                        leading={
                          <>
                            <button
                              className="icon-button"
                              aria-label={t('Undo move')}
                              title={t('Undo')}
                              onClick={() => {
                                setPaused(true);
                                void action('undo');
                              }}
                              disabled={busy || game.cursor === 0}
                            >
                              ↶
                            </button>
                            <button
                              className="icon-button"
                              aria-label={t('Redo move')}
                              title={t('Redo')}
                              onClick={() => {
                                setPaused(true);
                                void action('redo');
                              }}
                              disabled={busy || game.cursor >= game.total_moves}
                            >
                              ↷
                            </button>
                          </>
                        }
                        help={playback}
                        trailing={
                          !game.winner &&
                          !game.recorded_winner &&
                          game.creation_policy === 1 &&
                          (game.mode !== 'ai-ai' || account?.permissions.includes('all_data')) && (
                            <button
                              className="icon-button"
                              aria-label={t('Restart game')}
                              aria-haspopup="dialog"
                              title={t('Restart')}
                              onClick={() => setRestartOpen(true)}
                              disabled={busy}
                            >
                              ⟲
                            </button>
                          )
                        }
                      />
                    }
                  />
                }
                history={
                  <HistoryPanel
                    count={game.total_moves}
                    entries={game.moves.map((move, i) => ({
                      ply: i + 1,
                      notation:
                        move.notation ??
                        `${coordinate(move.source, game.size)} → ${coordinate(move.target, game.size)}`,
                      side: move.side ?? (i % 2 ? 'defenders' : 'attackers'),
                      current: i === game.cursor - 1,
                      future: i >= game.cursor,
                      onSeek: () => {
                        setPaused(true);
                        void action('seek', { cursor: i + 1 });
                      },
                      disabled: busy,
                    }))}
                    startAction={{
                      onClick: () => {
                        setPaused(true);
                        void action('seek', { cursor: 0 });
                      },
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
      {tab === 'play' && restartOpen && game && !outcome && (
        <Modal
          title={t('Restart game?')}
          onClose={() => setRestartOpen(false)}
          initialFocus=".restart-cancel"
        >
          <p className="confirmation-copy">
            {t('Start a new game with the same setup? The current game will be kept.')}
          </p>
          <div className="modal-actions">
            <button
              className="button secondary restart-cancel"
              onClick={() => setRestartOpen(false)}
            >
              {t('Cancel')}
            </button>
            <button
              className="button"
              disabled={busy}
              onClick={() => {
                setPaused(true);
                setRestartOpen(false);
                void action('restart');
              }}
            >
              {t('Confirm restart')}
            </button>
          </div>
        </Modal>
      )}
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
            setPaused(false);
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
