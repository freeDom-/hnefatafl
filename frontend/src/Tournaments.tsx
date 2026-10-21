import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import Board, { coordinate, Crown } from './Board';
import RecordsConfirm from './RecordsConfirm';
import RecordsParameters from './RecordsParameters';
import { sortRulesets } from './sortRulesets';
import type { AIProfile, Game, Model, Ruleset } from './types';
import type {
  Tournament,
  TournamentConfig,
  TournamentGame,
  TournamentPlayer,
  TournamentReplay,
} from './tournamentTypes';
import './features.css';
import './tournaments.css';

const date = (value: string) =>
  new Date(value).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
const statusLabel: Record<Tournament['status'], string> = {
  running: 'In progress',
  completed: 'Completed',
  cancelled: 'Stopped',
  failed: 'Interrupted',
};
const score = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(1));
const active = (tournament: Tournament) => tournament.status === 'running';
const champion = (tournament: Tournament) => {
  if (!tournament.result?.games.length) return 'The challenge is set';
  const [first, second] = tournament.result.scores;
  if (first === second) return 'Level on points';
  const name = tournament.config.players[first > second ? 0 : 1].name;
  return `${name} ${tournament.status === 'completed' ? 'wins on points' : 'leads on points'}`;
};
const outcome = (match: TournamentGame, players: TournamentPlayer[]) => {
  if (!match.winner) return 'Move limit reached';
  if (match.winner === 'draw') return 'Rule draw';
  const winner = match.winner === 'attackers' ? match.attacker_player : 1 - match.attacker_player;
  return `${players[winner].name} wins`;
};

export default function Tournaments({
  rulesets,
  profiles,
  models,
  onError,
}: {
  rulesets: Ruleset[];
  profiles: AIProfile[];
  models: Model[];
  onError: (message: string) => void;
}) {
  const [tournaments, setTournaments] = useState<Tournament[]>([]);
  const [selected, setSelected] = useState<Tournament | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [deleting, setDeleting] = useState<Tournament | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [replay, setReplay] = useState<{ index: number; data: TournamentReplay } | null>(null);
  const [replaying, setReplaying] = useState<number | null>(null);
  const selectedId = useRef<string | null>(null);
  const requestNumber = useRef(0);
  const refreshNumber = useRef(0);
  const mutating = useRef(false);
  const mounted = useRef(true);
  const errorHandler = useRef(onError);
  errorHandler.current = onError;

  const select = useCallback(async (id: string) => {
    const request = ++requestNumber.current;
    selectedId.current = id;
    setDetailLoading(true);
    setReplay(null);
    try {
      const next = await api<Tournament>(`/tournaments/${id}`);
      if (!mounted.current || request !== requestNumber.current) return;
      setSelected(next);
    } catch (error) {
      if (mounted.current && request === requestNumber.current)
        errorHandler.current((error as Error).message);
    } finally {
      if (mounted.current && request === requestNumber.current) setDetailLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    if (mutating.current) return;
    const request = ++refreshNumber.current;
    const saved = await api<Tournament[]>('/tournaments');
    if (!mounted.current || request !== refreshNumber.current || mutating.current) return;
    setTournaments(saved);
    setLoading(false);
    const id = selectedId.current;
    if (!id && saved[0]) await select(saved[0].id);
    else if (id) {
      const current = await api<Tournament>(`/tournaments/${id}`);
      if (
        mounted.current &&
        selectedId.current === id &&
        request === refreshNumber.current &&
        !mutating.current
      )
        setSelected(current);
    }
  }, [select]);

  useEffect(() => {
    mounted.current = true;
    void refresh()
      .catch((error) => errorHandler.current(error.message))
      .finally(() => {
        if (mounted.current) setLoading(false);
      });
    return () => {
      mounted.current = false;
      requestNumber.current++;
      refreshNumber.current++;
    };
  }, [refresh]);
  useEffect(() => {
    if (!tournaments.some(active)) return;
    const timer = window.setInterval(
      () => void refresh().catch((error) => errorHandler.current(error.message)),
      1800,
    );
    return () => window.clearInterval(timer);
  }, [tournaments, refresh]);

  const stop = async () => {
    if (!selected) return;
    const id = selected.id;
    setBusy(true);
    mutating.current = true;
    refreshNumber.current++;
    try {
      await api(`/tournaments/${id}/cancel`, {});
      mutating.current = false;
      await refresh();
      setNotice('Tournament stopped. All finished games have been saved.');
    } catch (error) {
      onError((error as Error).message);
    } finally {
      mutating.current = false;
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    mutating.current = true;
    refreshNumber.current++;
    setDeleteError('');
    try {
      await api(`/tournaments/${deleting.id}`, undefined, 'DELETE');
      if (selectedId.current === deleting.id) {
        selectedId.current = null;
        requestNumber.current++;
        setSelected(null);
        setReplay(null);
      }
      setDeleting(null);
      mutating.current = false;
      await refresh();
      setNotice('Tournament and its saved results deleted. Your models and other games are kept.');
    } catch (error) {
      setDeleteError((error as Error).message);
    } finally {
      mutating.current = false;
      setBusy(false);
    }
  };
  const openReplay = async (index: number) => {
    if (!selected) return;
    const id = selected.id;
    setReplaying(index);
    try {
      const data = await api<TournamentReplay>(`/tournaments/${id}/games/${index}/replay`);
      if (selectedId.current === id) setReplay({ index, data });
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setReplaying(null);
    }
  };
  const completed = tournaments.filter((tournament) => tournament.status === 'completed').length;
  const running = tournaments.some(active);

  return (
    <>
      <section className="page-intro tournament-intro">
        <div>
          <p className="eyebrow">A MEETING OF MINDS</p>
          <h1>The tournament hall.</h1>
          <p>Set your strategists against each other. Every result becomes part of their saga.</p>
        </div>
        <button
          className="button primary"
          onClick={() => setNewOpen(true)}
          disabled={loading || running}
        >
          <span aria-hidden="true">＋</span> New tournament
        </button>
      </section>
      {notice && (
        <p role="status" className="notice tournament-notice">
          {notice}
        </p>
      )}
      <div className="tournament-layout">
        <aside className="panel tournament-history" aria-label="Saved tournaments">
          <div className="panel-title">
            <h2>Your tournaments</h2>
            <span className="badge">{tournaments.length}</span>
          </div>
          <p className="subtle">{completed} completed · saved on this server</p>
          {loading ? (
            <p className="empty-copy">Opening the hall…</p>
          ) : !tournaments.length ? (
            <div className="tournament-history-empty">
              <span aria-hidden="true">ᛟ</span>
              <p>Your first challenge awaits.</p>
              <small>Saved tournaments will appear here.</small>
            </div>
          ) : (
            <div className="tournament-saved-list">
              {tournaments.map((tournament) => (
                <button
                  key={tournament.id}
                  className={`tournament-saved ${selected?.id === tournament.id ? 'selected' : ''}`}
                  aria-label={`View tournament ${tournament.name}`}
                  aria-pressed={selected?.id === tournament.id}
                  onClick={() => void select(tournament.id)}
                >
                  <span className={`tournament-status ${tournament.status}`}>
                    {statusLabel[tournament.status]}
                  </span>
                  <strong>{tournament.name}</strong>
                  <small>
                    {tournament.config.players.map((player) => player.name).join(' vs ')}
                  </small>
                  <span className="tournament-saved-meta">
                    <small>{date(tournament.created_at)}</small>
                    <small>
                      {tournament.progress.completed_games} / {tournament.progress.total_games}{' '}
                      games
                    </small>
                  </span>
                </button>
              ))}
            </div>
          )}
        </aside>
        <div className="tournament-main" aria-busy={detailLoading}>
          {!selected ? (
            <section className="panel tournament-welcome">
              <span className="tournament-emblem" aria-hidden="true">
                <Crown />
              </span>
              <p className="eyebrow">TWO STRATEGISTS. BOTH SIDES.</p>
              <h2>Let the strongest ideas meet.</h2>
              <p>
                Compare algorithms, search settings or trained models in a balanced series.
                Opponents swap armies after every game.
              </p>
              <div className="tournament-promise">
                <span>
                  <strong>01</strong> Choose your rivals
                </span>
                <span>
                  <strong>02</strong> Set the battlefields
                </span>
                <span>
                  <strong>03</strong> Explore every result
                </span>
              </div>
              <button
                className="button secondary"
                onClick={() => setNewOpen(true)}
                disabled={loading || running}
              >
                Set up a tournament
              </button>
              <small>Saved automatically. Come back whenever you like.</small>
            </section>
          ) : (
            <section className="panel tournament-detail" aria-label="Tournament results">
              <div className="tournament-detail-heading">
                <div>
                  <span className={`tournament-status ${selected.status}`}>
                    {statusLabel[selected.status]}
                  </span>
                  <h2>{selected.name}</h2>
                  <p className="subtle">
                    {date(selected.created_at)} · {selected.config.games} games per battlefield ·
                    seed {selected.config.seed}
                  </p>
                </div>
                <div className="tournament-actions">
                  {active(selected) ? (
                    <button
                      className="button secondary"
                      disabled={busy || detailLoading}
                      onClick={() => void stop()}
                    >
                      {busy ? 'Stopping…' : 'Stop tournament'}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="icon-button danger-text"
                      aria-label={`Delete tournament ${selected.name}`}
                      title="Delete tournament"
                      disabled={busy || detailLoading}
                      onClick={() => {
                        setDeleting(selected);
                        setDeleteError('');
                      }}
                    >
                      ×
                    </button>
                  )}
                </div>
              </div>
              {selected.error && (
                <p role="alert" className="form-error">
                  {selected.error}
                </p>
              )}
              <div className="tournament-progress">
                <div>
                  <strong>
                    {selected.progress.completed_games} of {selected.progress.total_games} games
                    saved
                  </strong>
                  <span>
                    {active(selected)
                      ? 'Playing the next match…'
                      : `${selected.progress.completed_games === selected.progress.total_games ? 'Series finished' : 'Partial series saved'}`}
                  </span>
                </div>
                <progress
                  aria-label="Tournament progress"
                  max={selected.progress.total_games}
                  value={selected.progress.completed_games}
                />
              </div>
              <div className="tournament-standings">
                <div className="tournament-section-title">
                  <h3>Standings</h3>
                  <span>{champion(selected)}</span>
                </div>
                <div className="tournament-opponents">
                  {selected.config.players.map((player, index) => {
                    const standing = selected.result?.standings.find((row) => row.player === index);
                    const points = standing?.score ?? 0;
                    return (
                      <article className={`tournament-standing rival-${index + 1}`} key={index}>
                        <div className="tournament-standing-top">
                          <span className="tournament-avatar" aria-hidden="true">
                            {index ? 'ᛉ' : 'ᛏ'}
                          </span>
                          <div>
                            <small>OPPONENT {index + 1}</small>
                            <h3>{player.name}</h3>
                            <p>
                              {player.model_name ||
                                player.profile_name ||
                                profiles.find((profile) => profile.id === player.profile_id)
                                  ?.name ||
                                String(player.config.algorithm)}
                            </p>
                          </div>
                          <strong className="tournament-points">
                            {score(points)}
                            <small>points</small>
                          </strong>
                        </div>
                        <div className="tournament-score-track" aria-hidden="true">
                          <span
                            style={{
                              width: `${(100 * points) / Math.max(1, selected.progress.completed_games)}%`,
                            }}
                          />
                        </div>
                        <dl className="tournament-score-stats">
                          <div>
                            <dt>Wins</dt>
                            <dd>{standing?.wins ?? 0}</dd>
                          </div>
                          <div>
                            <dt>Draws</dt>
                            <dd>{standing?.draws ?? 0}</dd>
                          </div>
                          <div>
                            <dt>Losses</dt>
                            <dd>{standing?.losses ?? 0}</dd>
                          </div>
                          <div>
                            <dt>Unfinished</dt>
                            <dd>{standing?.truncated ?? 0}</dd>
                          </div>
                        </dl>
                        <RecordsParameters
                          config={player.config}
                          label={`Opponent ${index + 1} parameters`}
                          variantId={player.variant_id}
                        />
                      </article>
                    );
                  })}
                </div>
                <p className="tournament-scoring">
                  Win = 1 point · Rule draw = ½ point · Move limit reached = unfinished, 0 points.
                  Stopped games still in progress are discarded.
                </p>
              </div>
              {selected.result && (
                <>
                  <div className="tournament-summary">
                    <span>
                      <strong>{selected.result.average_game_length.toFixed(1)}</strong> average
                      moves
                    </span>
                    <span>
                      <strong>{selected.result.average_decision_time.toFixed(3)}s</strong> per
                      decision
                    </span>
                    <span>
                      <strong>{selected.result.truncated}</strong> unfinished
                    </span>
                  </div>
                  <div className="tournament-section-title">
                    <h3>Battlefield results</h3>
                    <span>Wins by opponent</span>
                  </div>
                  <div className="tournament-battlefields">
                    {sortRulesets(rulesets)
                      .filter((rule) => selected.config.rulesets.includes(rule.id))
                      .map((rule) => {
                        const tally = selected.result?.by_ruleset[rule.id];
                        return (
                          <article key={rule.id}>
                            <span className="tournament-board-size">
                              {rule.size}
                              <small>× {rule.size}</small>
                            </span>
                            <div>
                              <strong>{rule.name}</strong>
                              <small>{tally?.games ?? 0} games played</small>
                              <p>
                                {tally?.wins[0] ?? 0}–{tally?.wins[1] ?? 0} wins ·{' '}
                                {tally?.draws ?? 0} draws · {tally?.truncated ?? 0} unfinished
                              </p>
                            </div>
                          </article>
                        );
                      })}
                  </div>
                </>
              )}
              <div className="tournament-section-title">
                <h3>Match log</h3>
                <span>{selected.result?.games.length ?? 0} recorded games</span>
              </div>
              {!selected.result?.games.length ? (
                <p className="empty-copy">
                  {active(selected)
                    ? 'The first result will appear when its game finishes. You can leave this page; the tournament will keep running.'
                    : 'No complete game records were saved for this series.'}
                </p>
              ) : (
                <div className="tournament-matches">
                  {selected.result.games.map((match, index) => (
                    <article className="tournament-match" key={index}>
                      <span className="tournament-match-number">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <div>
                        <strong>{outcome(match, selected.config.players)}</strong>
                        <small>
                          {rulesets.find((rule) => rule.id === match.ruleset)?.name ??
                            match.ruleset}{' '}
                          · {match.plies} moves · {match.average_decision_time.toFixed(3)}s /
                          decision
                        </small>
                        <p>
                          <span className="mini-piece black" aria-hidden="true" />
                          {selected.config.players[match.attacker_player].name}{' '}
                          <span className="tournament-versus">vs</span>{' '}
                          <span className="mini-piece ivory" aria-hidden="true" />
                          {selected.config.players[1 - match.attacker_player].name}
                        </p>
                        <small className="tournament-match-reason">
                          {match.reason.replaceAll('_', ' ')}
                        </small>
                      </div>
                      <button
                        className="button secondary small"
                        aria-label={`Replay game ${index + 1}`}
                        disabled={replaying !== null || detailLoading}
                        onClick={() => void openReplay(index)}
                      >
                        {replaying === index ? 'Loading…' : 'Replay ↗'}
                      </button>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}
        </div>
      </div>
      {newOpen && (
        <TournamentSetup
          rulesets={rulesets}
          profiles={profiles}
          models={models}
          onClose={() => setNewOpen(false)}
          onCreated={(created) => {
            refreshNumber.current++;
            selectedId.current = created.id;
            requestNumber.current++;
            setSelected(created);
            setReplay(null);
            setNewOpen(false);
            setNotice('Tournament started. Results are saved after every finished game.');
            void refresh().catch((error) => onError(error.message));
          }}
        />
      )}
      {deleting && (
        <RecordsConfirm
          title={`Delete tournament ${deleting.name}?`}
          busy={busy}
          error={deleteError}
          onCancel={() => setDeleting(null)}
          onConfirm={() => void remove()}
        >
          <p>
            This permanently removes <strong>{deleting.name}</strong>, its results and saved
            tournament replays. Your models, training runs and other saved games are kept.
          </p>
        </RecordsConfirm>
      )}
      {replay && selected?.result && (
        <TournamentPlayback
          key={`${selected.id}:${replay.index}`}
          tournament={selected}
          index={replay.index}
          data={replay.data}
          rulesets={rulesets}
          onClose={() => setReplay(null)}
        />
      )}
    </>
  );
}

const defaultPlayers = (): TournamentPlayer[] => [
  {
    name: 'Shield bearer',
    profile_id: 'baseline',
    config: { algorithm: 'baseline', time_limit: 0.1 },
  },
  {
    name: 'Jarl',
    profile_id: 'alpha_beta',
    config: { algorithm: 'alpha_beta', depth: 2, time_limit: 0.1 },
  },
];

function TournamentSetup({
  rulesets,
  profiles,
  models,
  onClose,
  onCreated,
}: {
  rulesets: Ruleset[];
  profiles: AIProfile[];
  models: Model[];
  onClose: () => void;
  onCreated: (tournament: Tournament) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState('The Raven’s Challenge');
  const [config, setConfig] = useState<TournamentConfig>({
    players: defaultPlayers(),
    rulesets: ['brandubh'],
    games: 2,
    max_plies: 150,
    seed: 42,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  const updatePlayer = (index: number, changes: Partial<TournamentPlayer>) =>
    setConfig((previous) => ({
      ...previous,
      players: previous.players.map((player, i) =>
        i === index ? { ...player, ...changes } : player,
      ),
    }));
  const parameter = (index: number, key: string, value: string | number) =>
    updatePlayer(index, { config: { ...config.players[index].config, [key]: value } });
  const changeProfile = (index: number, id: string) => {
    const profile = profiles.find((item) => item.id === id);
    if (!profile) return;
    const previous = config.players[index];
    const previousProfile = profiles.find((item) => item.id === previous.profile_id);
    const keepName =
      previous.name !== previousProfile?.name &&
      previous.name !== (index ? 'Jarl' : 'Shield bearer');
    updatePlayer(index, {
      profile_id: id,
      name: keepName ? previous.name : profile.name,
      config: { ...profile.parameters, algorithm: profile.algorithm, time_limit: 0.1 },
    });
  };
  const compatible = models.filter(
    (model) => config.rulesets.length === 1 && config.rulesets[0] === model.ruleset,
  );
  const incompatibleModel = config.players.some(
    (player) =>
      player.config.algorithm === 'neural' &&
      !compatible.some((model) => model.id === player.config.model_id),
  );
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onCreated(await api<Tournament>('/tournaments', { name: name.trim(), ...config }));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={ref}
      className="modal tournament-setup"
      aria-label="Set up a tournament"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form onSubmit={(event) => void submit(event)}>
        <div className="modal-top">
          <div>
            <p className="eyebrow">A NEW CHALLENGE</p>
            <h2>Set up a tournament</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="Close tournament setup"
            onClick={onClose}
            disabled={busy}
          >
            ×
          </button>
        </div>
        <p className="tournament-setup-copy">
          Two opponents, equal turns as attackers and defenders. Choose a quick comparison or give
          your rivals room to think.
        </p>
        <label>
          Tournament name
          <input
            required
            maxLength={80}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <fieldset className="tournament-fields">
          <legend>Battlefields</legend>
          <div className="tournament-rulesets">
            {sortRulesets(rulesets).map((rule) => (
              <label className={config.rulesets.includes(rule.id) ? 'chosen' : ''} key={rule.id}>
                <input
                  type="checkbox"
                  aria-label={`${rule.name} ${rule.size} × ${rule.size}`}
                  checked={config.rulesets.includes(rule.id)}
                  onChange={(event) =>
                    setConfig((previous) => ({
                      ...previous,
                      rulesets: event.target.checked
                        ? [...previous.rulesets, rule.id]
                        : previous.rulesets.filter((id) => id !== rule.id),
                    }))
                  }
                />
                <strong>{rule.name}</strong>
                <small>
                  {rule.size} × {rule.size}
                </small>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="tournament-setup-opponents">
          {config.players.map((player, index) => {
            const prefix = `Opponent ${index + 1}`;
            const algorithm = player.config.algorithm;
            const field = (
              key: string,
              label: string,
              minimum: number,
              maximum: number,
              step = 1,
              fallback = minimum,
            ) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  aria-label={`${prefix} ${label.toLowerCase()}`}
                  min={minimum}
                  max={maximum}
                  step={step}
                  required
                  value={Number(player.config[key] ?? fallback)}
                  onChange={(event) => parameter(index, key, Number(event.target.value))}
                />
              </label>
            );
            return (
              <fieldset key={index} className="tournament-player">
                <legend>{prefix}</legend>
                <label>
                  Name
                  <input
                    aria-label={`${prefix} name`}
                    maxLength={80}
                    required
                    value={player.name}
                    onChange={(event) => updatePlayer(index, { name: event.target.value })}
                  />
                </label>
                <label>
                  Profile
                  <select
                    aria-label={`${prefix} profile`}
                    value={player.profile_id}
                    onChange={(event) => changeProfile(index, event.target.value)}
                  >
                    {profiles.map((profile) => (
                      <option value={profile.id} key={profile.id}>
                        {profile.name}
                      </option>
                    ))}
                  </select>
                </label>
                {algorithm === 'neural' && (
                  <label>
                    Trained model
                    <select
                      aria-label={`${prefix} trained model`}
                      required
                      value={String(player.config.model_id ?? '')}
                      onChange={(event) => parameter(index, 'model_id', event.target.value)}
                    >
                      <option value="">Select a compatible model</option>
                      {compatible.map((model) => (
                        <option value={model.id} key={model.id}>
                          {model.name}
                        </option>
                      ))}
                    </select>
                    <small>Trained models require one matching battlefield.</small>
                  </label>
                )}
                {field('time_limit', 'Time limit (seconds)', 0.01, 15, 0.01, 0.1)}
                <details className="tournament-advanced">
                  <summary>Search parameters</summary>
                  <div className="form-grid">
                    {algorithm === 'alpha_beta' && field('depth', 'Search depth', 1, 5, 1, 2)}
                    {['mcts', 'neural'].includes(String(algorithm)) && (
                      <>
                        {field('iterations', 'Search iterations', 1, 5000, 1, 80)}
                        {field('exploration', 'Exploration', 0, 10, 0.1, 1.4)}
                        {field('temperature', 'Temperature', 0, 5, 0.1, 0)}
                        {algorithm === 'mcts' &&
                          field('rollout_depth', 'Rollout depth', 0, 50, 1, 8)}
                      </>
                    )}
                    {algorithm === 'baseline' && (
                      <p className="subtle">
                        A tactical heuristic with no additional search controls.
                      </p>
                    )}
                  </div>
                </details>
              </fieldset>
            );
          })}
        </div>
        <div className="form-grid tournament-run-settings">
          <label>
            Games per battlefield
            <input
              type="number"
              min={2}
              max={100}
              step={2}
              required
              value={config.games}
              onChange={(event) =>
                setConfig((previous) => ({ ...previous, games: Number(event.target.value) }))
              }
            />
          </label>
          <label>
            Move limit per game
            <input
              type="number"
              min={1}
              max={1000}
              required
              value={config.max_plies}
              onChange={(event) =>
                setConfig((previous) => ({ ...previous, max_plies: Number(event.target.value) }))
              }
            />
          </label>
          <label>
            Tournament seed
            <input
              type="number"
              min={0}
              max={4294967295}
              required
              value={config.seed}
              onChange={(event) =>
                setConfig((previous) => ({ ...previous, seed: Number(event.target.value) }))
              }
            />
          </label>
          <div className="tournament-run-count">
            <strong>{config.games * config.rulesets.length}</strong>
            <span>games in this series</span>
          </div>
        </div>
        <p className="tournament-scoring">
          Use an even game count to balance sides. The tournament seed controls all opponents’ game
          and move seeds. Tight time limits can vary with hardware. Unfinished games score no
          points.
        </p>
        {(!config.rulesets.length || incompatibleModel) && (
          <p className="form-error">
            {!config.rulesets.length
              ? 'Choose at least one battlefield.'
              : 'Choose a compatible trained model and its single matching battlefield.'}
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="button primary"
            disabled={busy || !name.trim() || !config.rulesets.length || incompatibleModel}
          >
            {busy ? 'Starting…' : 'Start tournament'}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function TournamentPlayback({
  tournament,
  index,
  data,
  rulesets,
  onClose,
}: {
  tournament: Tournament;
  index: number;
  data: TournamentReplay;
  rulesets: Ruleset[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [cursor, setCursor] = useState(0);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  const match = tournament.result!.games[index];
  const state = data.states[cursor];
  const size =
    rulesets.find((rule) => rule.id === match.ruleset)?.size ?? Math.sqrt(state.board.length);
  const player = (playerIndex: number) => ({
    ...tournament.config.players[playerIndex],
    kind: 'ai' as const,
  });
  const board: Game = {
    ...state,
    id: tournament.id,
    size,
    moves: match.moves.map((move, i) => ({ ...move, side: data.states[i].turn })),
    cursor,
    total_moves: match.moves.length,
    legal_moves: [],
    mode: 'ai-ai',
    players: {
      attackers: player(match.attacker_player),
      defenders: player(1 - match.attacker_player),
    },
    version: 0,
    captured: { attackers: 0, defenders: 0 },
    created_at: tournament.created_at,
  };
  return (
    <dialog
      ref={ref}
      className="modal tournament-replay"
      aria-label={`Replay game ${index + 1}`}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="modal-top">
        <div>
          <p className="eyebrow">SAVED TOURNAMENT GAME</p>
          <h2>
            Game {index + 1} ·{' '}
            {rulesets.find((rule) => rule.id === match.ruleset)?.name ?? match.ruleset}
          </h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Close tournament replay"
          onClick={onClose}
        >
          ×
        </button>
      </div>
      <p className="tournament-replay-result">
        {outcome(match, tournament.config.players)} · {match.reason.replaceAll('_', ' ')}
      </p>
      <Board game={board} selected={null} onSelect={() => undefined} disabled />
      <div className="tournament-replay-controls">
        <button
          className="icon-button"
          aria-label="First tournament move"
          onClick={() => setCursor(0)}
          disabled={!cursor}
        >
          ↤
        </button>
        <button
          className="icon-button"
          aria-label="Previous tournament move"
          onClick={() => setCursor((value) => value - 1)}
          disabled={!cursor}
        >
          ←
        </button>
        <span aria-live="polite">
          Move {cursor} / {match.plies}
        </span>
        <button
          className="icon-button"
          aria-label="Next tournament move"
          onClick={() => setCursor((value) => value + 1)}
          disabled={cursor >= data.states.length - 1}
        >
          →
        </button>
        <button
          className="icon-button"
          aria-label="Last tournament move"
          onClick={() => setCursor(data.states.length - 1)}
          disabled={cursor >= data.states.length - 1}
        >
          ↦
        </button>
      </div>
      <label className="tournament-replay-range">
        Replay position
        <input
          type="range"
          min={0}
          max={data.states.length - 1}
          value={cursor}
          onChange={(event) => setCursor(Number(event.target.value))}
        />
      </label>
      <p className="subtle">
        {cursor
          ? `${coordinate(match.moves[cursor - 1].source, size)} → ${coordinate(match.moves[cursor - 1].target, size)}`
          : 'Starting position'}{' '}
        ·{' '}
        {state.winner
          ? state.winner === 'draw'
            ? 'Rule draw'
            : `${state.winner} win`
          : `${state.turn} to move`}
      </p>
    </dialog>
  );
}
