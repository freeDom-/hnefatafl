import { localizedRules } from './locales/rules-de';
import { t, useI18n, LanguageSelect, getLocale } from './i18n';
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import Board, { coordinate, Crown } from './Board';
import { api } from './api';
import type { AIProfile, Game, Mode, Model, Player, PlayerConfig, Ruleset, Side } from './types';
import Archive from './Archive';
import Training from './Training';
import Tournaments from './Tournaments';
import { AppearanceSettings } from './Appearance';
import { sortRulesets } from './sortRulesets';
import {
  defaultAI,
  restoreGameSetup,
  rememberGameSetup,
  withoutSeed,
  profileConfiguration,
} from './gameSetupPreference';
import { selectStrongestModel } from './modelSelection';
import AgentAnalysis, { useGameAnalysis } from './AgentAnalysis';
import { AnalyticsSettingsPanel } from './AdvisorSelect';
import Rune, { participantName } from './Rune';
import { modelLabel } from './modelLabel';

type Tab = 'play' | 'archive' | 'training' | 'tournaments';
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };
const sideGlyph = (side: Side) => (
  <span className={`mini-piece ${side === 'attackers' ? 'black' : 'ivory'}`} aria-hidden="true" />
);

export default function App() {
  useI18n();
  const [tab, setTab] = useState<Tab>('play');
  const [rulesets, setRulesets] = useState<Ruleset[]>([]);
  const [profiles, setProfiles] = useState<AIProfile[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [players, setPlayers] = useState<Player[]>([]);
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
  const initialized = useRef(false);
  const currentGame = useRef<Game | null>(null);
  currentGame.current = game;
  const analysis = useGameAnalysis(game, tab === 'play');

  const refreshOptions = useCallback(async () => {
    const [rules, ais, people, savedModels] = await Promise.all([
      api<Ruleset[]>('/rulesets'),
      api<AIProfile[]>('/ai/profiles'),
      api<Player[]>('/players'),
      api<Model[]>('/models'),
    ]);
    setRulesets(rules);
    setProfiles(ais);
    setPlayers(people);
    setModels(savedModels);
    return rules;
  }, []);
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    refreshOptions()
      .then(async (rules) => {
        const storedId = localStorage.getItem('hnefatafl-game');
        if (storedId) {
          try {
            setGame(await api<Game>(`/games/${storedId}`));
            return;
          } catch {
            localStorage.removeItem('hnefatafl-game');
          }
        }
        if (rules.length) setNewOpen(true);
      })
      .catch((e) => setError(String(e.message)))
      .finally(() => setInitializing(false));
  }, [refreshOptions]);
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
  const loadGame = (saved: Game) => {
    currentGame.current = saved;
    setGame(saved);
    setSelected(null);
    setPaused(true);
    setTab('play');
    setError('');
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
      <header className="site-header">
        <a
          className="brand"
          href="#play"
          onClick={(event) => {
            event.preventDefault();
            setTab('play');
          }}
        >
          <span className="brand-emblem">
            <Crown />
          </span>
          <span>
            {t('HNEFATAFL')}
            <small>{t('THE KING’S TABLE')}</small>
          </span>
        </a>
        <nav aria-label={t('Main navigation')}>
          {(['play', 'archive', 'training', 'tournaments'] as Tab[]).map((item) => (
            <button
              type="button"
              key={item}
              onClick={() => {
                setTab(item);
                if (item === 'tournaments') void refreshOptions().catch((e) => setError(e.message));
              }}
              className={tab === item ? 'active' : ''}
              aria-current={tab === item ? 'page' : undefined}
            >
              {item === 'play'
                ? t('Play')
                : item === 'archive'
                  ? t('Hall of records')
                  : item === 'training'
                    ? t('AI workshop')
                    : t('Tournaments')}
            </button>
          ))}
        </nav>
        <div className="header-controls">
          <LanguageSelect />
          <AppearanceSettings />
        </div>
      </header>
      <main>
        {error && (
          <div role="alert" className="error-banner">
            <span>{t(error)}</span>
            <button onClick={() => setError('')} aria-label={t('Dismiss error')}>
              ×
            </button>
          </div>
        )}
        {tab === 'play' && (
          <>
            <section className="page-intro">
              <div>
                <h1>{t('Every move tells a saga.')}</h1>
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
                    {!game.winner && !game.recorded_winner && (
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
        {tab === 'archive' && (
          <Archive
            rulesets={rulesets}
            onLoad={loadGame}
            onError={setError}
            players={players}
            refreshPlayers={() => void refreshOptions().catch((e) => setError(e.message))}
            onGameDeleted={(id) => removeGames([id])}
          />
        )}
        {tab === 'tournaments' && (
          <Tournaments rulesets={rulesets} profiles={profiles} models={models} onError={setError} />
        )}
        {tab === 'training' && (
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
      {newOpen && (
        <NewGame
          rulesets={rulesets}
          profiles={profiles}
          models={models}
          players={players}
          initialRuleset={game?.ruleset}
          onClose={() => setNewOpen(false)}
          onCreated={(next) => {
            currentGame.current = next;
            setGame(next);
            setSelected(null);
            setPaused(false);
            setNewOpen(false);
            setTab('play');
            setError('');
          }}
        />
      )}
      {rulesOpen && activeRules && (
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

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onPointerDown={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        )
          onClose();
      }}
    >
      <div className="modal-top">
        <div>
          <h2>{title}</h2>
        </div>
        <button className="icon-button" aria-label={t('Close dialog')} onClick={onClose}>
          ×
        </button>
      </div>
      {children}
    </dialog>
  );
}

function AIFields({
  side,
  value,
  onChange,
  profiles,
  models,
  ruleset,
}: {
  side: Side;
  value: PlayerConfig;
  onChange: (value: PlayerConfig) => void;
  profiles: AIProfile[];
  models: Model[];
  ruleset: string;
}) {
  const update = (key: string, input: string | number) => {
    const config = withoutSeed({ ...value.config, [key]: input });
    if (key === 'algorithm') {
      delete config.model_id;
      delete config.evaluation;
      if (input === 'neural')
        config.model_id = selectStrongestModel(models, ruleset)?.model.id ?? '';
    }
    onChange({
      ...value,
      ...(key === 'algorithm' ? { profile_id: undefined, name: 'Computer' } : {}),
      ...((key === 'model_id' || key === 'algorithm') && config.algorithm === 'neural'
        ? { name: models.find((model) => model.id === config.model_id)?.name ?? 'Computer' }
        : {}),
      config,
    });
  };
  const selectProfile = (id: string) => {
    const profile = profiles.find((p) => p.id === id);
    if (!profile) {
      onChange({ ...value, profile_id: undefined });
      return;
    }
    const selectedModel =
      profile.algorithm === 'neural' ? selectStrongestModel(models, ruleset)?.model : undefined;
    onChange({
      ...value,
      name: selectedModel?.name ?? profile.name,
      profile_id: id,
      config: withoutSeed({
        time_limit: 1,
        ...profileConfiguration(profile),
        algorithm: profile?.algorithm ?? 'baseline',
        ...(profile?.algorithm === 'neural' ? { model_id: selectedModel?.id ?? '' } : {}),
      }),
    });
  };
  return (
    <div className="ai-fields">
      <label>
        {t('Opponent')}
        <select
          aria-label={t('{side} AI profile', { side: t(titles[side]) })}
          value={value.profile_id ?? ''}
          onChange={(e) => selectProfile(e.target.value)}
        >
          <option value="">{t('Custom opponent')}</option>
          {profiles
            .filter((p) => p.supported_rulesets.includes(ruleset))
            .map((p) => (
              <option value={p.id} key={p.id}>
                {p.rune ? `${p.rune} ` : ''}
                {t(p.name)} · {t(p.difficulty)}
              </option>
            ))}
        </select>
      </label>
      {['alpha_beta', 'mcts'].includes(value.profile_id ?? '') ? (
        <p className="subtle">
          {value.profile_id === 'alpha_beta'
            ? `${t('Search depth')}: 5 · ${t('Time limit (seconds)')}: 2`
            : `${t('Search iterations')}: 5000 · ${t('Temperature')}: 0 · ${t('Exploration')}: 1.4 · ${t('Time limit (seconds)')}: 2`}
        </p>
      ) : (
        <div className="form-grid">
          <label>
            {t('Algorithm')}
            <select
              aria-label={t('{side} algorithm', { side: t(titles[side]) })}
              value={String(value.config.algorithm ?? 'baseline')}
              onChange={(e) => update('algorithm', e.target.value)}
            >
              <option value="baseline">{t('Tactical heuristic')}</option>
              <option value="alpha_beta">{t('Alpha-beta search')}</option>
              <option value="mcts">{t('Monte Carlo tree search')}</option>
              <option value="neural">{t('Neural network')}</option>
            </select>
          </label>
          <label>
            {t('Time limit (seconds)')}
            <input
              type="number"
              min="0.05"
              max="15"
              step="0.05"
              value={Number(value.config.time_limit ?? 1)}
              onChange={(e) => update('time_limit', Number(e.target.value))}
            />
          </label>
          {value.config.algorithm === 'alpha_beta' && (
            <label>
              {t('Search depth')}
              <input
                type="number"
                min="1"
                max="5"
                value={Number(value.config.depth ?? 2)}
                onChange={(e) => update('depth', Number(e.target.value))}
              />
            </label>
          )}
          {['mcts', 'neural'].includes(String(value.config.algorithm)) && (
            <>
              <label>
                {t('Search iterations')}
                <input
                  type="number"
                  min="1"
                  max="5000"
                  value={Number(value.config.iterations ?? 64)}
                  onChange={(e) => update('iterations', Number(e.target.value))}
                />
              </label>
              <label>
                {t('Exploration')}
                <input
                  type="number"
                  min="0"
                  max="10"
                  step="0.1"
                  value={Number(value.config.exploration ?? 1.4)}
                  onChange={(e) => update('exploration', Number(e.target.value))}
                />
              </label>
              <label>
                {t('Temperature')}
                <input
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  value={Number(value.config.temperature ?? 0)}
                  onChange={(e) => update('temperature', Number(e.target.value))}
                />
              </label>
            </>
          )}
        </div>
      )}
      {value.config.algorithm === 'neural' && (
        <label>
          {t('Trained model')}
          <select
            aria-label={t('{side} trained model', { side: t(titles[side]) })}
            value={String(value.config.model_id ?? '')}
            required
            onChange={(e) => update('model_id', e.target.value)}
          >
            <option value="">{t('Choose a compatible model')}</option>
            {models
              .filter((m) => m.ruleset === ruleset)
              .map((m) => (
                <option value={m.id} key={m.id}>
                  {modelLabel(m)}
                </option>
              ))}
          </select>
          {!models.some((m) => m.ruleset === ruleset) && (
            <small>{t('Train or import a model in the AI workshop first.')}</small>
          )}
        </label>
      )}
    </div>
  );
}

export function NewGame({
  rulesets,
  profiles,
  models,
  players,
  initialRuleset,
  onClose,
  onCreated,
}: {
  rulesets: Ruleset[];
  profiles: AIProfile[];
  models: Model[];
  players: Player[];
  initialRuleset?: string;
  onClose: () => void;
  onCreated: (game: Game) => void;
}) {
  useI18n();
  const [restored] = useState(() =>
    restoreGameSetup({ rulesets, profiles, models, players, initialRuleset }),
  );
  const [ruleset, setRuleset] = useState(restored.ruleset);
  const [mode, setMode] = useState<Mode>(restored.mode);
  const [humanSide, setHumanSide] = useState<Side>(restored.humanSide);
  const [fixedSeed, setFixedSeed] = useState(restored.fixedSeed);
  const [seed, setSeed] = useState(restored.seed);
  const [people, setPeople] = useState<Record<Side, PlayerConfig>>(restored.people);
  const [computers, setComputers] = useState<Record<Side, PlayerConfig>>(restored.computers);
  const [customAI, setCustomAI] = useState<Record<Side, boolean>>(restored.customAI);
  const priorRuleset = useRef(ruleset);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const recommendation = selectStrongestModel(models, ruleset);
  useEffect(() => {
    const changedRuleset = priorRuleset.current !== ruleset;
    priorRuleset.current = ruleset;
    const reset: Side[] = (['attackers', 'defenders'] as Side[]).filter((side) => {
      const configuration = computers[side].config;
      const removedModel =
        configuration.algorithm === 'neural' &&
        !models.some((model) => model.id === configuration.model_id && model.ruleset === ruleset);
      return changedRuleset || !customAI[side] || removedModel;
    });
    if (!reset.length) return;
    const nextDefault = defaultAI(models, ruleset, profiles);
    setComputers((previous) => ({
      ...previous,
      ...Object.fromEntries(reset.map((side) => [side, nextDefault])),
    }));
    if (changedRuleset || reset.some((side) => customAI[side]))
      setCustomAI((previous) => ({
        ...previous,
        ...Object.fromEntries(reset.map((side) => [side, false])),
      }));
    // Only option changes reset automatic choices; editing a budget keeps it.
  }, [ruleset, models, profiles]);
  useEffect(() => {
    if (
      !Object.values(people).some(
        (person) => person.player_id && !players.some((profile) => profile.id === person.player_id),
      )
    )
      return;
    setPeople(
      (previous) =>
        Object.fromEntries(
          Object.entries(previous).map(([side, person]) => [
            side,
            person.player_id && !players.some((profile) => profile.id === person.player_id)
              ? { ...person, name: '', player_id: undefined }
              : person,
          ]),
        ) as Record<Side, PlayerConfig>,
    );
  }, [players, people]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    const configured = Object.fromEntries(
      (['attackers', 'defenders'] as Side[]).map((side) => {
        const ai = mode === 'ai-ai' || (mode === 'human-ai' && side !== humanSide);
        return [
          side,
          ai ? { ...computers[side], config: withoutSeed(computers[side].config) } : people[side],
        ];
      }),
    );
    try {
      const created = await api<Game>('/games', {
        ruleset,
        mode,
        human_side: humanSide,
        players: configured,
        ...(fixedSeed ? { seed } : {}),
      });
      rememberGameSetup({
        ruleset,
        mode,
        humanSide,
        people,
        computers,
        advisorId: '',
        fixedSeed,
        seed,
      });
      onCreated(created);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t('Begin a new saga')} onClose={onClose}>
      <form onSubmit={submit} className="new-game-form">
        <fieldset>
          <legend>{t('01 · Choose your battlefield')}</legend>
          <div className="ruleset-options">
            {sortRulesets(rulesets).map((rule) => (
              <label key={rule.id} className={ruleset === rule.id ? 'chosen' : ''}>
                <input
                  type="radio"
                  name="ruleset"
                  value={rule.id}
                  checked={ruleset === rule.id}
                  onChange={() => setRuleset(rule.id)}
                />
                <span className="option-rune">
                  {rule.id === 'tablut' ? 'ᛏ' : rule.id === 'brandubh' ? 'ᛒ' : 'ᛟ'}
                </span>
                <strong>{t(rule.name)}</strong>
                <span>{t('{size} × {size} board', { size: rule.size })}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>{t('02 · Gather your players')}</legend>
          <div className="segmented">
            {(
              [
                { id: 'human-human', label: 'Pass & play' },
                { id: 'human-ai', label: 'Human vs AI' },
                { id: 'ai-ai', label: 'AI vs AI' },
              ] as const
            ).map((option) => (
              <button
                type="button"
                className={mode === option.id ? 'active' : ''}
                key={option.id}
                onClick={() => setMode(option.id)}
              >
                {t(option.label)}
              </button>
            ))}
          </div>
          {mode === 'human-ai' && (
            <label className="inline-label">
              {t('Your side')}
              <select value={humanSide} onChange={(e) => setHumanSide(e.target.value as Side)}>
                <option value="defenders">{t('Defenders · help the king escape')}</option>
                <option value="attackers">{t('Attackers · capture the king')}</option>
              </select>
            </label>
          )}
          <div className="player-configs">
            {(['attackers', 'defenders'] as Side[]).map((side) => {
              const ai = mode === 'ai-ai' || (mode === 'human-ai' && side !== humanSide);
              return (
                <div className="player-config" key={side}>
                  <h3>
                    {sideGlyph(side)} {t(titles[side])}
                    <small>{ai ? t('COMPUTER') : t('HUMAN')}</small>
                  </h3>
                  {ai ? (
                    <AIFields
                      side={side}
                      value={computers[side]}
                      onChange={(value) => {
                        setComputers((previous) => ({ ...previous, [side]: value }));
                        setCustomAI((previous) => ({ ...previous, [side]: true }));
                      }}
                      profiles={profiles}
                      models={models}
                      ruleset={ruleset}
                    />
                  ) : (
                    <>
                      <label>
                        {t('Human player')}
                        <select
                          aria-label={t('{side} human player', { side: t(titles[side]) })}
                          value={people[side].player_id ?? ''}
                          onChange={(e) => {
                            const player = players.find((p) => p.id === e.target.value);
                            setPeople((p) => ({
                              ...p,
                              [side]: {
                                ...p[side],
                                name: player?.name ?? '',
                                player_id: player?.id,
                              },
                            }));
                          }}
                        >
                          <option value="">{t('New player / enter name')}</option>
                          {players.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.rune ? `${p.rune} ` : ''}
                              {p.name}
                              {players.filter((profile) => profile.name === p.name).length > 1
                                ? ` · ${p.id.slice(0, 6)}`
                                : ''}
                            </option>
                          ))}
                        </select>
                      </label>
                      {!people[side].player_id && (
                        <label>
                          {t('Player name')}
                          <input
                            aria-label={t('{side} player name', { side: t(titles[side]) })}
                            required
                            maxLength={64}
                            value={people[side].name}
                            onChange={(e) =>
                              setPeople((p) => ({
                                ...p,
                                [side]: { ...p[side], name: e.target.value, player_id: undefined },
                              }))
                            }
                          />
                        </label>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
          {recommendation &&
            mode !== 'human-human' &&
            (['attackers', 'defenders'] as Side[]).some(
              (side) =>
                (mode === 'ai-ai' || side !== humanSide) &&
                !customAI[side] &&
                computers[side].config.model_id === recommendation.model.id,
            ) && (
              <p className="subtle model-recommendation">
                {recommendation.basis === 'evaluation'
                  ? t(
                      'Best measured model selected: {name} ({score}% across {games} baseline games).',
                      {
                        name: recommendation.model.name,
                        score: ((recommendation.score ?? 0) * 100).toLocaleString(getLocale(), {
                          minimumFractionDigits: 1,
                          maximumFractionDigits: 1,
                        }),
                        games: recommendation.games,
                      },
                    )
                  : t('No comparable evaluation; latest model selected: {name}.', {
                      name: recommendation.model.name,
                    })}{' '}
                {t('You can choose another opponent or adjust its settings.')}
              </p>
            )}
          <p className="subtle game-seed-note">
            {t(
              'A fresh random seed is generated for every new game and restart. The seed is saved with the game for inspection.',
            )}
          </p>
          <label className="inline-label">
            <input
              type="checkbox"
              checked={fixedSeed}
              onChange={(event) => setFixedSeed(event.target.checked)}
            />
            {t('Use a fixed seed')}
          </label>
          {fixedSeed && (
            <label>
              {t('Game seed')}
              <input
                type="number"
                aria-label={t('Game seed')}
                aria-describedby="fixed-seed-help"
                min={0}
                max={4294967295}
                step={1}
                required
                value={seed}
                onChange={(event) => setSeed(Number(event.target.value))}
              />
              <small id="fixed-seed-help">
                {t(
                  'Use the same seed and settings to repeat an experiment. Restart uses a new seed.',
                )}
              </small>
            </label>
          )}
        </fieldset>
        {error && (
          <p className="form-error" role="alert">
            {t(error)}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            {t('Cancel')}
          </button>
          <button type="submit" className="button primary" disabled={busy || !rulesets.length}>
            {busy ? t('Preparing…') : t('To the table')} <span aria-hidden="true">↗</span>
          </button>
        </div>
      </form>
    </Modal>
  );
}
