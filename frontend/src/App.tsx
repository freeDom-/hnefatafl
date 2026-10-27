import SiteHeader from './SiteHeader';
import NewGame from './NewGame';
import { localizedRules } from './locales/rules-de';
import { t, useI18n } from './i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import Board, { coordinate, Crown } from './Board';
import { api } from './api';
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
import { useAuth } from './Auth';
import { canonicalLocation, navigate } from './navigation';

type Tab = 'dashboard' | 'online' | 'play' | 'archive' | 'leaderboard' | 'training' | 'tournaments';
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };
const sideGlyph = (side: Side) => (
  <span className={`mini-piece ${side === 'attackers' ? 'black' : 'ivory'}`} aria-hidden="true" />
);

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
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [paused, setPaused] = useState(false);
  const [speed, setSpeed] = useState(900);
  const [initializing, setInitializing] = useState(true);
  const aiLock = useRef(false);

  const currentGame = useRef<Game | null>(null);
  const loadedRoute = useRef<string | null>(null);
  currentGame.current = game;
  const analysis = useGameAnalysis(game, tab === 'play');

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
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (
      tab === 'dashboard' &&
      new URLSearchParams(location.search).get('setup') === 'single-player'
    )
      startSinglePlayer();
  }, [path, tab, startSinglePlayer]);
  useEffect(() => {
    if (tab === 'training' || tab === 'tournaments')
      void refreshOptions().catch((e) => setError(e.message));
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
        if (alive) setError(e.message);
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
    setError('');
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
        setError((e as Error).message);
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
  useEffect(() => {
    if (!game || !activeAI || paused || busy || tab !== 'play') return;
    const timer = window.setTimeout(() => void action('ai-move'), speed);
    return () => window.clearTimeout(timer);
  }, [game, activeAI, paused, busy, speed, action, tab]);

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
    setError('');
    localStorage.removeItem('hnefatafl-game');
  }, []);
  const activeRules = rulesets.find((r) => r.id === game?.ruleset);

  return (
    <>
      <SiteHeader account={account} logout={logout} current={tab} />
      <main>
        {error && (
          <div role="alert" className="error-banner">
            <span>{t(error)}</span>
            <button onClick={() => setError('')} aria-label={t('Dismiss error')}>
              ×
            </button>
          </div>
        )}
        {tab === 'dashboard' && <PersonalDashboard onNewSingle={startSinglePlayer} />}
        {tab === 'play' && (
          <>
            <section className="page-intro">
              <div>
                <h1>{t('Every move tells a saga.')}</h1>
                <p>{t('Singleplayer · saved to your account')}</p>
                <p>{t('One king. Two armies. A thousand ways to outwit your opponent.')}</p>
              </div>
              <button
                className="button primary"
                onClick={() => {
                  setNewOpen(true);
                  void refreshOptions().catch((e) => setError(e.message));
                }}
                disabled={initializing}
              >
                <span aria-hidden="true">＋</span> {t('New game')}
              </button>
            </section>
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
              <div className="game-layout">
                <aside className="game-sidebar">
                  <section className="panel game-details">
                    <div className="variant-heading">
                      <h2>
                        {game.ruleset === 'tablut'
                          ? t('Tablut')
                          : game.ruleset === 'copenhagen'
                            ? t('Copenhagen')
                            : t(activeRules?.name ?? game.ruleset)}
                      </h2>
                      <span className="badge">
                        {game.size} × {game.size}
                      </span>
                    </div>
                    <p>
                      {t(
                        activeRules?.description ??
                          'An ancient contest of escape and encirclement.',
                      )}
                    </p>
                    <button className="text-button" onClick={() => setRulesOpen(true)}>
                      {t('Read the rules')}
                      <span aria-hidden="true">↗</span>
                    </button>
                    <div className="divider" />
                    <div className="detail-row">
                      <span>{t('Game mode')}</span>
                      <strong>
                        {game.mode === 'human-human'
                          ? t('Pass & play')
                          : game.mode === 'human-ai'
                            ? t('Human vs AI')
                            : t('AI vs AI')}
                      </strong>
                    </div>
                    <div className="detail-row">
                      <span>{t('Move')}</span>
                      <strong>{game.cursor}</strong>
                    </div>
                    {game.seed !== undefined && (
                      <div className="detail-row">
                        <span>{t('Random seed')}</span>
                        <strong>{game.seed}</strong>
                      </div>
                    )}
                  </section>
                  <section className="panel armies">
                    {(['attackers', 'defenders'] as Side[]).map((side) => (
                      <div
                        className={`army ${game.turn === side && !game.winner ? 'to-move' : ''}`}
                        key={side}
                      >
                        <div className="army-symbol">
                          {game.players[side]?.rune ? (
                            <Rune rune={game.players[side].rune} />
                          ) : (
                            sideGlyph(side)
                          )}
                        </div>
                        <div>
                          <h3>{participantName(game.players[side], t(titles[side]))}</h3>
                          <p>
                            {t(titles[side])} ·{' '}
                            {game.players[side]?.kind === 'ai'
                              ? t(String(game.players[side].config.algorithm ?? 'Computer'))
                              : t('Human')}
                          </p>
                        </div>
                        {game.turn === side && !game.winner && (
                          <span className="turn-light" aria-label={t('Current turn')} />
                        )}
                      </div>
                    ))}
                    <div className="captured-row">
                      <span>{t('Captured')}</span>
                      <span>
                        {sideGlyph('attackers')} {game.captured?.attackers ?? 0}
                      </span>
                      <span>
                        {sideGlyph('defenders')} {game.captured?.defenders ?? 0}
                      </span>
                    </div>
                  </section>
                  {analysis.show && <AgentAnalysis game={game} {...analysis} />}
                  <div className="strategy-note">
                    <span aria-hidden="true">ᛉ</span>
                    <p>
                      {game.turn === 'attackers'
                        ? t('Close the circle. Cut off the king’s path to freedom.')
                        : t('Protect the king. Find the opening in the siege.')}
                    </p>
                  </div>
                </aside>
                <section className="battlefield" aria-label={t('Game')}>
                  <div
                    className={`turn-banner ${game.winner ? 'finished' : ''}`}
                    aria-live="polite"
                  >
                    <div>
                      {game.winner ? (
                        <>
                          <Crown />
                          <strong>
                            {game.winner === 'draw'
                              ? t('An evenly matched saga')
                              : t('{side} win', { side: t(titles[game.winner]) })}
                          </strong>
                          <span>{game.reason ? t(game.reason.replaceAll('_', ' ')) : ''}</span>
                        </>
                      ) : (
                        <>
                          {sideGlyph(game.turn)}
                          <strong>{t('{side} to move', { side: t(titles[game.turn]) })}</strong>
                          <span>
                            {busy
                              ? t('Thinking…')
                              : activeAI
                                ? paused
                                  ? t('Computer paused')
                                  : t('Computer is preparing a move')
                                : selected !== null
                                  ? t('Choose a highlighted square')
                                  : t('Select a piece to see its moves')}
                          </span>
                        </>
                      )}
                    </div>
                    {!game.winner && <span className="live-dot" />}
                  </div>
                  <Board
                    game={game}
                    selected={selected}
                    onSelect={selectSquare}
                    disabled={busy || activeAI || !!game.winner || !!game.recorded_winner}
                    hint={analysis.report?.hint?.move ?? null}
                  />
                  <div className="board-toolbar">
                    <div className="button-group">
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
                    </div>
                    <span className="board-help">
                      {game.winner
                        ? t('A new saga is one move away.')
                        : t('Move in straight lines. Capture by surrounding.')}
                    </span>
                    {!game.winner &&
                      !game.recorded_winner &&
                      game.creation_policy === 1 &&
                      (game.mode !== 'ai-ai' || account?.permissions.includes('all_data')) && (
                        <button
                          className="icon-button"
                          aria-label={t('Restart game')}
                          title={t('Restart')}
                          onClick={() => {
                            setPaused(true);
                            void action('restart');
                          }}
                          disabled={busy}
                        >
                          ⟲
                        </button>
                      )}
                  </div>
                  {game.mode !== 'human-human' && !game.winner && !game.recorded_winner && (
                    <div className="playback">
                      <button
                        className="button small"
                        onClick={() => setPaused((p) => !p)}
                        disabled={!!game.winner}
                      >
                        {paused ? t('▶ Resume AI') : t('Ⅱ Pause AI')}
                      </button>
                      <button
                        className="text-button"
                        onClick={() => void action('ai-move')}
                        disabled={!activeAI || busy || !paused}
                      >
                        {t('Next move')}
                      </button>
                      <label>
                        {t('Playback')}{' '}
                        <select
                          aria-label={t('AI playback speed')}
                          value={speed}
                          onChange={(e) => setSpeed(Number(e.target.value))}
                        >
                          <option value={1800}>{t('Relaxed')}</option>
                          <option value={900}>{t('Normal')}</option>
                          <option value={100}>{t('Fast')}</option>
                        </select>
                      </label>
                      {busy && (
                        <span className="thinking">
                          <i />
                          {t('Calculating')}
                        </span>
                      )}
                    </div>
                  )}
                </section>
                <aside className="panel history-panel">
                  <div className="panel-title">
                    <h2>{t('The saga so far')}</h2>
                    <span className="badge">{game.total_moves}</span>
                  </div>
                  <p className="subtle">{t('A record of every move')}</p>
                  <div className="history-columns">
                    <span>{t('MOVE')}</span>
                    <span>{t('JOURNEY')}</span>
                    <span>{t('SIDE')}</span>
                  </div>
                  <ol className="move-list">
                    {game.moves.map((move, i) => (
                      <li
                        key={i}
                        className={
                          i === game.cursor - 1 ? 'current' : i >= game.cursor ? 'future' : ''
                        }
                      >
                        <button
                          onClick={() => {
                            setPaused(true);
                            void action('seek', { cursor: i + 1 });
                          }}
                          disabled={busy}
                          aria-label={t('Replay to move {move}', { move: i + 1 })}
                        >
                          <span>{String(i + 1).padStart(2, '0')}</span>
                          <strong>
                            {move.notation ??
                              `${coordinate(move.source, game.size)} → ${coordinate(move.target, game.size)}`}
                          </strong>
                          {sideGlyph(move.side ?? (i % 2 ? t('defenders') : t('attackers')))}
                        </button>
                      </li>
                    ))}
                  </ol>
                  {game.moves.length === 0 && (
                    <div className="history-empty">
                      <span aria-hidden="true">ᛝ</span>
                      <p>
                        {t('Every saga starts')}
                        <br />
                        {t('with a first move.')}
                      </p>
                    </div>
                  )}
                  <button
                    className="history-start text-button"
                    onClick={() => {
                      setPaused(true);
                      void action('seek', { cursor: 0 });
                    }}
                    disabled={busy || game.cursor === 0}
                  >
                    {t('↤ Starting position')}
                  </button>
                  <div className="history-footer">
                    <span className="status-dot" /> {t('Game saved automatically')}
                  </div>
                  <AnalyticsSettingsPanel
                    game={game}
                    profiles={profiles}
                    models={models}
                    settings={analysis.settings}
                    onChange={analysis.updateSettings}
                    pending={analysis.settingsPending}
                    error={analysis.settingsError}
                  />
                </aside>
              </div>
            )}
          </>
        )}
        {tab === 'online' && <Online />}
        {tab === 'leaderboard' && <Leaderboard />}
        {tab === 'archive' && <Archive />}
        {tab === 'tournaments' && permissions.includes('tournaments') && (
          <Tournaments rulesets={rulesets} profiles={profiles} models={models} onError={setError} />
        )}
        {tab === 'training' && permissions.includes('training') && (
          <Training
            rulesets={rulesets}
            models={models}
            onModelsChanged={() => void refreshOptions().catch((e) => setError(e.message))}
            onGamesDeleted={removeGames}
            onError={setError}
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
            setPaused(false);
            setNewOpen(false);
            setInitializing(false);
            navigate(`/games/${next.id}`);
            setError('');
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
