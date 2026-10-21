import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import Board, { coordinate, Crown } from './Board';
import { api } from './api';
import type { AIProfile, Game, Mode, Model, Player, PlayerConfig, Ruleset, Side } from './types';
import Archive from './Archive';
import Training from './Training';
import { AppearanceSettings } from './Appearance';
import { sortRulesets } from './sortRulesets';

type Tab = 'play' | 'archive' | 'training';
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };
const sideGlyph = (side: Side) => (
  <span className={`mini-piece ${side === 'attackers' ? 'black' : 'ivory'}`} aria-hidden="true" />
);

export default function App() {
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
  const activeAI = !!game && !game.winner && game.players[game.turn]?.kind === 'ai';
  useEffect(() => {
    if (!game || !activeAI || paused || busy || tab !== 'play') return;
    const timer = window.setTimeout(() => void action('ai-move'), speed);
    return () => window.clearTimeout(timer);
  }, [game, activeAI, paused, busy, speed, action, tab]);

  const selectSquare = (index: number) => {
    if (!game || game.winner || busy || activeAI) return;
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
    setGame(saved);
    setSelected(null);
    setPaused(true);
    setTab('play');
    setError('');
  };
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
            HNEFATAFL<small>THE KING’S TABLE</small>
          </span>
        </a>
        <nav aria-label="Main navigation">
          {(['play', 'archive', 'training'] as Tab[]).map((item) => (
            <button
              type="button"
              key={item}
              onClick={() => setTab(item)}
              className={tab === item ? 'active' : ''}
              aria-current={tab === item ? 'page' : undefined}
            >
              {item === 'play' ? 'Play' : item === 'archive' ? 'Hall of records' : 'AI workshop'}
            </button>
          ))}
        </nav>
        <AppearanceSettings />
        <span className="header-note">
          <span />A game of wit, not chance
        </span>
      </header>
      <main>
        {error && (
          <div role="alert" className="error-banner">
            <span>{error}</span>
            <button onClick={() => setError('')} aria-label="Dismiss error">
              ×
            </button>
          </div>
        )}
        {tab === 'play' && (
          <>
            <section className="page-intro">
              <div>
                <p className="eyebrow">STRATEGY, FORGED IN THE NORTH</p>
                <h1>Every move tells a saga.</h1>
                <p>One king. Two armies. A thousand ways to outwit your opponent.</p>
              </div>
              <button
                className="button primary"
                onClick={() => {
                  setNewOpen(true);
                  void refreshOptions().catch((e) => setError(e.message));
                }}
                disabled={initializing}
              >
                <span aria-hidden="true">＋</span> New game
              </button>
            </section>
            {initializing ? (
              <div className="empty-state">
                <div className="spinner" />
                <h2>Setting the table…</h2>
              </div>
            ) : !game ? (
              <section className="welcome-panel">
                <div className="welcome-rune">ᛟ</div>
                <p className="eyebrow">A TRADITION OF STRATEGY</p>
                <h2>The king awaits.</h2>
                <p>
                  Lead the encircling attackers, or guide the outnumbered king to freedom. Choose a
                  historic ruleset and begin your first saga.
                </p>
                <button className="button primary" onClick={() => setNewOpen(true)}>
                  Choose your game <span aria-hidden="true">↗</span>
                </button>
                <div className="variant-pills">
                  {rulesets.map((r) => (
                    <span key={r.id}>
                      {r.name}{' '}
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
                    <p className="eyebrow">THE BATTLEFIELD</p>
                    <div className="variant-heading">
                      <h2>
                        {game.ruleset === 'tablut'
                          ? 'Tablut'
                          : game.ruleset === 'copenhagen'
                            ? 'Copenhagen'
                            : (activeRules?.name ?? game.ruleset)}
                      </h2>
                      <span className="badge">
                        {game.size} × {game.size}
                      </span>
                    </div>
                    <p>
                      {activeRules?.description ?? 'An ancient contest of escape and encirclement.'}
                    </p>
                    <button className="text-button" onClick={() => setRulesOpen(true)}>
                      Read the rules <span aria-hidden="true">↗</span>
                    </button>
                    <div className="divider" />
                    <div className="detail-row">
                      <span>Game mode</span>
                      <strong>
                        {game.mode === 'human-human'
                          ? 'Pass & play'
                          : game.mode === 'human-ai'
                            ? 'Human vs AI'
                            : 'AI vs AI'}
                      </strong>
                    </div>
                    <div className="detail-row">
                      <span>Move</span>
                      <strong>{game.cursor}</strong>
                    </div>
                  </section>
                  <section className="panel armies">
                    <p className="eyebrow">THE TWO SIDES</p>
                    {(['attackers', 'defenders'] as Side[]).map((side) => (
                      <div
                        className={`army ${game.turn === side && !game.winner ? 'to-move' : ''}`}
                        key={side}
                      >
                        <div className="army-symbol">{sideGlyph(side)}</div>
                        <div>
                          <h3>{game.players[side]?.name || titles[side]}</h3>
                          <p>
                            {titles[side]} ·{' '}
                            {game.players[side]?.kind === 'ai'
                              ? (game.players[side].config.algorithm ?? 'Computer')
                              : 'Human'}
                          </p>
                        </div>
                        {game.turn === side && !game.winner && (
                          <span className="turn-light" aria-label="Current turn" />
                        )}
                      </div>
                    ))}
                    <div className="captured-row">
                      <span>Captured</span>
                      <span>
                        {sideGlyph('attackers')} {game.captured?.attackers ?? 0}
                      </span>
                      <span>
                        {sideGlyph('defenders')} {game.captured?.defenders ?? 0}
                      </span>
                    </div>
                  </section>
                  <div className="strategy-note">
                    <span aria-hidden="true">ᛉ</span>
                    <p>
                      {game.turn === 'attackers'
                        ? 'Close the circle. Cut off the king’s path to freedom.'
                        : 'Protect the king. Find the opening in the siege.'}
                    </p>
                  </div>
                </aside>
                <section className="battlefield" aria-label="Game">
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
                              ? 'An evenly matched saga'
                              : `${titles[game.winner]} win`}
                          </strong>
                          <span>{game.reason?.replaceAll('_', ' ')}</span>
                        </>
                      ) : (
                        <>
                          {sideGlyph(game.turn)}
                          <strong>{titles[game.turn]} to move</strong>
                          <span>
                            {busy
                              ? 'Thinking…'
                              : activeAI
                                ? paused
                                  ? 'Computer paused'
                                  : 'Computer is preparing a move'
                                : selected !== null
                                  ? 'Choose a highlighted square'
                                  : 'Select a piece to see its moves'}
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
                    disabled={busy || activeAI || !!game.winner}
                  />
                  <div className="board-toolbar">
                    <div className="button-group">
                      <button
                        className="icon-button"
                        aria-label="Undo move"
                        title="Undo"
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
                        aria-label="Redo move"
                        title="Redo"
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
                        ? 'A new saga is one move away.'
                        : 'Move in straight lines. Capture by surrounding.'}
                    </span>
                    <button
                      className="icon-button"
                      aria-label="Restart game"
                      title="Restart"
                      onClick={() => {
                        setPaused(true);
                        void action('restart');
                      }}
                      disabled={busy}
                    >
                      ⟲
                    </button>
                  </div>
                  {game.mode !== 'human-human' && (
                    <div className="playback">
                      <button
                        className="button small"
                        onClick={() => setPaused((p) => !p)}
                        disabled={!!game.winner}
                      >
                        {paused ? '▶ Resume AI' : 'Ⅱ Pause AI'}
                      </button>
                      <button
                        className="text-button"
                        onClick={() => void action('ai-move')}
                        disabled={!activeAI || busy || !paused}
                      >
                        Next move
                      </button>
                      <label>
                        Playback{' '}
                        <select
                          aria-label="AI playback speed"
                          value={speed}
                          onChange={(e) => setSpeed(Number(e.target.value))}
                        >
                          <option value={1800}>Relaxed</option>
                          <option value={900}>Normal</option>
                          <option value={100}>Fast</option>
                        </select>
                      </label>
                      {busy && (
                        <span className="thinking">
                          <i />
                          Calculating
                        </span>
                      )}
                    </div>
                  )}
                </section>
                <aside className="panel history-panel">
                  <div className="panel-title">
                    <h2>The saga so far</h2>
                    <span className="badge">{game.total_moves}</span>
                  </div>
                  <p className="subtle">A record of every move</p>
                  <div className="history-columns">
                    <span>MOVE</span>
                    <span>JOURNEY</span>
                    <span>SIDE</span>
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
                          aria-label={`Replay to move ${i + 1}`}
                        >
                          <span>{String(i + 1).padStart(2, '0')}</span>
                          <strong>
                            {move.notation ??
                              `${coordinate(move.source, game.size)} → ${coordinate(move.target, game.size)}`}
                          </strong>
                          {sideGlyph(move.side ?? (i % 2 ? 'defenders' : 'attackers'))}
                        </button>
                      </li>
                    ))}
                  </ol>
                  {game.moves.length === 0 && (
                    <div className="history-empty">
                      <span aria-hidden="true">ᛝ</span>
                      <p>
                        Every saga starts
                        <br />
                        with a first move.
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
                    ↤ Starting position
                  </button>
                  <div className="history-footer">
                    <span className="status-dot" /> Game saved automatically
                  </div>
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
          />
        )}
        {tab === 'training' && (
          <Training
            rulesets={rulesets}
            models={models}
            onModelsChanged={() => void refreshOptions().catch((e) => setError(e.message))}
            onError={setError}
          />
        )}
      </main>
      <footer className="site-footer">
        <span>ᛟ HNEFATAFL</span>
        <p>An ancient game. An open horizon.</p>
        <span>Made for thoughtful moves.</span>
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
        <Modal title={`${activeRules.name} rules`} onClose={() => setRulesOpen(false)}>
          <article className="rules-document">
            <ReactMarkdown>{activeRules.rules_markdown}</ReactMarkdown>
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
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal-top">
        <div>
          <p className="eyebrow">THE KING’S TABLE</p>
          <h2>{title}</h2>
        </div>
        <button className="icon-button" aria-label="Close dialog" onClick={onClose}>
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
  const update = (key: string, input: string | number) =>
    onChange({
      ...value,
      ...(key === 'algorithm' ? { profile_id: undefined, name: 'Computer' } : {}),
      config: { ...value.config, [key]: input },
    });
  const selectProfile = (id: string) => {
    const profile = profiles.find((p) => p.id === id);
    onChange({
      ...value,
      name: profile?.name ?? 'Computer',
      profile_id: id,
      config: {
        ...value.config,
        ...profile?.parameters,
        algorithm: profile?.algorithm ?? 'baseline',
      },
    });
  };
  return (
    <div className="ai-fields">
      <label>
        Opponent
        <select
          aria-label={`${titles[side]} AI profile`}
          value={value.profile_id ?? ''}
          onChange={(e) => selectProfile(e.target.value)}
        >
          <option value="">Custom opponent</option>
          {profiles
            .filter((p) => p.supported_rulesets.includes(ruleset))
            .map((p) => (
              <option value={p.id} key={p.id}>
                {p.name} · {p.difficulty}
              </option>
            ))}
        </select>
      </label>
      <div className="form-grid">
        <label>
          Algorithm
          <select
            aria-label={`${titles[side]} algorithm`}
            value={String(value.config.algorithm ?? 'baseline')}
            onChange={(e) => update('algorithm', e.target.value)}
          >
            <option value="baseline">Tactical heuristic</option>
            <option value="alpha_beta">Alpha-beta search</option>
            <option value="mcts">Monte Carlo tree search</option>
            <option value="neural">Neural network</option>
          </select>
        </label>
        <label>
          Time limit (seconds)
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
            Search depth
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
              Search iterations
              <input
                type="number"
                min="1"
                max="5000"
                value={Number(value.config.iterations ?? 64)}
                onChange={(e) => update('iterations', Number(e.target.value))}
              />
            </label>
            <label>
              Exploration
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
              Temperature
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
        <label>
          Random seed
          <input
            type="number"
            min="0"
            value={Number(value.config.seed ?? 42)}
            onChange={(e) => update('seed', Number(e.target.value))}
          />
        </label>
      </div>
      {value.config.algorithm === 'neural' && (
        <label>
          Trained model
          <select
            value={String(value.config.model_id ?? '')}
            required
            onChange={(e) => update('model_id', e.target.value)}
          >
            <option value="">Choose a compatible model</option>
            {models
              .filter((m) => m.ruleset === ruleset)
              .map((m) => (
                <option value={m.id} key={m.id}>
                  {m.name} {m.version}
                </option>
              ))}
          </select>
          {!models.some((m) => m.ruleset === ruleset) && (
            <small>Train or import a model in the AI workshop first.</small>
          )}
        </label>
      )}
    </div>
  );
}

function NewGame({
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
  const [ruleset, setRuleset] = useState(initialRuleset ?? 'tablut');
  const [mode, setMode] = useState<Mode>('human-human');
  const [humanSide, setHumanSide] = useState<Side>('defenders');
  const [people, setPeople] = useState<Record<Side, PlayerConfig>>({
    attackers: {
      name: 'Attacker',
      kind: 'human',
      config: { algorithm: 'baseline', time_limit: 1, seed: 42 },
    },
    defenders: {
      name: 'Defender',
      kind: 'human',
      config: { algorithm: 'baseline', time_limit: 1, seed: 43 },
    },
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
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
      (['attackers', 'defenders'] as Side[]).map((side) => [
        side,
        {
          ...people[side],
          kind: mode === 'ai-ai' || (mode === 'human-ai' && side !== humanSide) ? 'ai' : 'human',
        },
      ]),
    );
    try {
      onCreated(
        await api<Game>('/games', { ruleset, mode, human_side: humanSide, players: configured }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Begin a new saga" onClose={onClose}>
      <form onSubmit={submit} className="new-game-form">
        <fieldset>
          <legend>01 · Choose your battlefield</legend>
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
                <strong>{rule.name}</strong>
                <span>
                  {rule.size} × {rule.size} board
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>02 · Gather your players</legend>
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
                {option.label}
              </button>
            ))}
          </div>
          {mode === 'human-ai' && (
            <label className="inline-label">
              Your side
              <select value={humanSide} onChange={(e) => setHumanSide(e.target.value as Side)}>
                <option value="defenders">Defenders · help the king escape</option>
                <option value="attackers">Attackers · capture the king</option>
              </select>
            </label>
          )}
          <div className="player-configs">
            {(['attackers', 'defenders'] as Side[]).map((side) => {
              const ai = mode === 'ai-ai' || (mode === 'human-ai' && side !== humanSide);
              return (
                <div className="player-config" key={side}>
                  <h3>
                    {sideGlyph(side)} {titles[side]}
                    <small>{ai ? 'COMPUTER' : 'HUMAN'}</small>
                  </h3>
                  {ai ? (
                    <AIFields
                      side={side}
                      value={people[side]}
                      onChange={(value) => setPeople((p) => ({ ...p, [side]: value }))}
                      profiles={profiles}
                      models={models}
                      ruleset={ruleset}
                    />
                  ) : (
                    <>
                      <label>
                        Human player
                        <select
                          aria-label={`${titles[side]} human player`}
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
                          <option value="">New player / enter name</option>
                          {players.map((p) => (
                            <option key={p.id} value={p.id}>
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
                          Player name
                          <input
                            aria-label={`${titles[side]} player name`}
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
        </fieldset>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button primary" disabled={busy || !rulesets.length}>
            {busy ? 'Preparing…' : 'To the table'} <span aria-hidden="true">↗</span>
          </button>
        </div>
      </form>
    </Modal>
  );
}
