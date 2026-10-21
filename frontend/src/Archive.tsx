import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import type { Game, Player, Ruleset, Stats, StatRow } from './types';
import { sortRulesets } from './sortRulesets';
import RecordsParameters from './RecordsParameters';
import RecordsConfirm from './RecordsConfirm';
import './features.css';
import './archive.css';

const percent = (value?: number) => `${((value ?? 0) * 100).toFixed(1)}%`;
const date = (value?: string) =>
  value
    ? new Date(value).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    : '—';
export default function Archive({
  rulesets,
  onLoad,
  onError,
  players,
  refreshPlayers,
  onGameDeleted,
}: {
  rulesets: Ruleset[];
  onLoad: (game: Game) => void;
  onError: (error: string) => void;
  players: Player[];
  refreshPlayers: () => void;
  onGameDeleted?: (id: string) => void;
}) {
  const [games, setGames] = useState<Game[]>([]);
  const [stats, setStats] = useState<Stats>({});
  const [ruleset, setRuleset] = useState('');
  const [mode, setMode] = useState('');
  const [kind, setKind] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletePlayer, setDeletePlayer] = useState<Player | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [deleteGame, setDeleteGame] = useState<Game | null>(null);
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const [visibleGames, setVisibleGames] = useState(10);
  const [historyOpen, setHistoryOpen] = useState(true);
  const generation = useRef(0);
  const currentDelete = useRef<Game | null>(null);
  currentDelete.current = deleteGame;
  useEffect(() => {
    let alive = true;
    const request = ++generation.current;
    setLoading(true);
    const query = new URLSearchParams({
      ...(ruleset && { ruleset }),
      ...(mode && { mode }),
      ...(kind && { kind }),
    });
    Promise.all([api<Game[]>(`/games?${query}`), api<Stats>(`/stats?${query}`)])
      .then(([records, next]) => {
        if (alive && request === generation.current) {
          setGames(records);
          setStats(next);
        }
      })
      .catch((e) => {
        if (alive && request === generation.current) onError(e.message);
      })
      .finally(() => {
        if (alive && request === generation.current) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [ruleset, mode, kind, onError, revision]);
  useEffect(() => setVisibleGames(10), [ruleset, mode, kind]);
  const createPlayer = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api('/players', { name: name.trim() });
      setName('');
      refreshPlayers();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const load = async (id: string) => {
    try {
      onLoad(await api<Game>(`/games/${id}`));
    } catch (e) {
      onError((e as Error).message);
    }
  };
  const removePlayer = async () => {
    if (!deletePlayer) return;
    setDeleting(true);
    setDeleteError('');
    try {
      await api(`/players/${deletePlayer.id}`, undefined, 'DELETE');
      setDeletePlayer(null);
      refreshPlayers();
    } catch (error) {
      setDeleteError((error as Error).message);
    } finally {
      setDeleting(false);
    }
  };
  const removed = (id: string) => {
    generation.current++;
    setGames((saved) => saved.filter((game) => game.id !== id));
    setDeleteGame(null);
    onGameDeleted?.(id);
    setNotice('Game removed. Statistics now reflect the remaining records.');
    setRevision((value) => value + 1);
  };
  const removeGame = async () => {
    const game = currentDelete.current;
    if (!game) return;
    setDeleting(true);
    setDeleteError('');
    generation.current++;
    try {
      const result = await fetch(`/api/games/${game.id}?version=${game.version}`, {
        method: 'DELETE',
      });
      if (result.ok || result.status === 404) {
        removed(game.id);
        return;
      }
      if (result.status === 409) {
        try {
          const fresh = await api<Game>(`/games/${game.id}`);
          setDeleteGame(fresh);
          setDeleteError(
            'This game changed. Review the updated game below, then choose Delete again to remove it.',
          );
        } catch (error) {
          setDeleteError((error as Error).message);
        }
        setRevision((value) => value + 1);
        return;
      }
      const detail = await result.json().catch(() => ({ detail: result.statusText }));
      throw new Error(
        typeof detail.detail === 'string' ? detail.detail : 'The game could not be removed.',
      );
    } catch (error) {
      setDeleteError((error as Error).message);
      setRevision((value) => value + 1);
    } finally {
      setDeleting(false);
    }
  };
  const sizes = new Map(rulesets.map((rule) => [rule.id, rule.size]));
  const byRules = (Array.isArray(stats.by_ruleset) ? [...stats.by_ruleset] : []).sort(
    (a, b) => (sizes.get(a.ruleset ?? '') ?? Infinity) - (sizes.get(b.ruleset ?? '') ?? Infinity),
  );
  const byPlayer = stats.by_player ?? stats.players ?? [];
  return (
    <>
      <section className="page-intro">
        <div>
          <p className="eyebrow">EVERY SAGA LEAVES A TRACE</p>
          <h1>Hall of records.</h1>
          <p>Revisit your battles. Learn from your victories, and your defeats.</p>
        </div>
        <span className="decorative-rune" aria-hidden="true">
          ᛟ
        </span>
      </section>
      <div className="filters panel">
        <label>
          Battlefield
          <select value={ruleset} onChange={(e) => setRuleset(e.target.value)}>
            <option value="">All rulesets</option>
            {sortRulesets(rulesets).map((rule) => (
              <option key={rule.id} value={rule.id}>
                {rule.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Game mode
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">All modes</option>
            <option value="human-human">Pass & play</option>
            <option value="human-ai">Human vs AI</option>
            <option value="ai-ai">AI vs AI</option>
          </select>
        </label>
        <label>
          Player type
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Humans & computers</option>
            <option value="human">Human players</option>
            <option value="ai">AI opponents</option>
          </select>
        </label>
      </div>
      <div className="metric-grid">
        <Metric
          label="BATTLES RECORDED"
          value={stats.total_games ?? games.length}
          detail={`${stats.completed_games ?? 0} completed games`}
        />
        <Metric
          label="ATTACKER VICTORIES"
          value={stats.attackers_wins ?? 0}
          detail={`${percent(stats.attacker_win_rate)} of completed games`}
        />
        <Metric
          label="DEFENDER VICTORIES"
          value={stats.defenders_wins ?? 0}
          detail={`${percent(stats.defender_win_rate)} of completed games`}
        />
        <Metric
          label="AVERAGE GAME LENGTH"
          value={Number(stats.average_game_length ?? stats.average_length ?? 0).toFixed(1)}
          detail={`${stats.draws ?? 0} draws · ${percent(stats.draw_rate)} of completed games`}
        />
      </div>
      {notice && (
        <p className="notice archive-notice" role="status">
          {notice}
        </p>
      )}
      <section
        className="panel archive-statistics"
        aria-labelledby="archive-statistics-heading"
        aria-busy={loading}
      >
        <div className="archive-statistics-heading">
          <div>
            <p className="eyebrow">THE STORY IN NUMBERS</p>
            <h2 id="archive-statistics-heading">Statistics</h2>
            <p>
              Completed battles, compared at a glance. Filters apply to statistics and saved games.
            </p>
          </div>
          <span className="archive-statistics-count">
            <strong>{stats.completed_games ?? 0}</strong> completed
          </span>
        </div>
        <div className="archive-statistics-grid">
          <section
            aria-labelledby="archive-battlefield-heading"
            className="archive-statistics-block"
          >
            <h3 id="archive-battlefield-heading">Results by battlefield</h3>
            <StatsTable rows={byRules} group="ruleset" />
          </section>
          <section
            aria-labelledby="archive-performance-heading"
            className="archive-statistics-block"
          >
            <h3 id="archive-performance-heading">Player & AI performance</h3>
            <StatsTable rows={byPlayer} group="player" />
          </section>
        </div>
      </section>
      <div className="archive-layout">
        <details
          className="panel records records-history"
          open={historyOpen}
          onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
        >
          <summary aria-label="Recorded games" className="archive-history-summary">
            <div>
              <p className="eyebrow">YOUR SAVED BATTLES</p>
              <h2>Recorded games</h2>
              <p>Reopen a game or remove it from your records.</p>
            </div>
            <span className="badge">{games.length}</span>
            <span className="archive-history-chevron" aria-hidden="true">
              ⌄
            </span>
          </summary>
          {loading ? (
            <p className="empty-copy">Loading your sagas…</p>
          ) : !games.length ? (
            <div className="empty-state compact">
              <span className="empty-rune">ᛝ</span>
              <h3>A history yet to be written.</h3>
              <p>Your saved games will appear here.</p>
            </div>
          ) : (
            <div className="records-list" role="region" aria-label="Saved game list" tabIndex={0}>
              {games.slice(0, visibleGames).map((game) => (
                <article className="record-entry" key={game.id} data-game-id={game.id}>
                  <div className="record-actions-row">
                    <button className="record" onClick={() => void load(game.id)}>
                      <span
                        className={`record-icon ${(game.recorded_winner ?? game.winner) ? 'complete' : ''}`}
                      >
                        {(game.recorded_winner ?? game.winner) ? 'ᛟ' : 'ᛏ'}
                      </span>
                      <span>
                        <strong>
                          {rulesets.find((rule) => rule.id === game.ruleset)?.name ?? game.ruleset}
                        </strong>
                        <small>
                          {game.players?.attackers?.name ?? 'Attackers'} vs{' '}
                          {game.players?.defenders?.name ?? 'Defenders'}
                        </small>
                      </span>
                      <span className="record-result">
                        <strong>
                          {(game.recorded_winner ?? game.winner) === 'draw'
                            ? 'Draw'
                            : (game.recorded_winner ?? game.winner)
                              ? `${(game.recorded_winner ?? game.winner) === 'attackers' ? 'Attackers' : 'Defenders'} win`
                              : 'In progress'}
                        </strong>
                        <small>
                          {game.recorded_plies ?? game.total_moves ?? game.ply} moves ·{' '}
                          {date(game.created_at)}
                        </small>
                      </span>
                      <span aria-hidden="true">↗</span>
                    </button>
                    <button
                      type="button"
                      className="icon-button danger-text delete-record"
                      aria-label={`Delete game ${game.id}`}
                      title="Delete recorded game"
                      onClick={() => {
                        setDeleteGame(game);
                        setDeleteError('');
                      }}
                    >
                      ×
                    </button>
                  </div>
                  {game.seed !== undefined && (
                    <p className="record-game-seed">Game seed {game.seed}</p>
                  )}
                  {(['attackers', 'defenders'] as const)
                    .filter((side) => game.players[side]?.kind === 'ai')
                    .map((side) => (
                      <RecordsParameters
                        key={side}
                        config={game.players[side].config}
                        label={`${side === 'attackers' ? 'Attacker' : 'Defender'} AI parameters`}
                      />
                    ))}
                </article>
              ))}
            </div>
          )}
          {games.length > visibleGames && (
            <div className="archive-load-more">
              <span>
                Showing {Math.min(visibleGames, games.length)} of {games.length} games
              </span>
              <button
                type="button"
                className="button secondary small"
                onClick={() => setVisibleGames((count) => count + 10)}
              >
                Show 10 more games
              </button>
            </div>
          )}
        </details>
        <aside className="panel profiles-panel">
          <p className="eyebrow">MAKE YOUR MARK</p>
          <h2>Local players</h2>
          <p className="subtle">Keep your identity across games and build a history of your own.</p>
          <form onSubmit={createPlayer} className="profile-form">
            <label>
              Display name
              <input
                required
                maxLength={64}
                placeholder="e.g. Astrid"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <button className="button primary" disabled={saving || !name.trim()}>
              {saving ? 'Saving…' : 'Create player'}
            </button>
          </form>
          <ul className="profile-list">
            {players.map((player, index) => (
              <li key={player.id}>
                <span className="avatar">{player.name.slice(0, 1).toUpperCase()}</span>
                <strong>{player.name}</strong>
                <small>#{String(index + 1).padStart(2, '0')}</small>
                <button
                  type="button"
                  className="icon-button danger-text delete-profile"
                  aria-label={`Delete player ${player.name}`}
                  onClick={() => {
                    setDeletePlayer(player);
                    setDeleteError('');
                  }}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
      {deleteGame && (
        <RecordsConfirm
          title="Delete recorded game?"
          busy={deleting}
          error={deleteError}
          onCancel={() => setDeleteGame(null)}
          onConfirm={() => void removeGame()}
        >
          <p>
            This permanently removes this game, its moves and result from the history. Statistics
            are recalculated from the remaining games. Player profiles and models are kept.
          </p>
          <div className="archive-delete-preview">
            <strong>
              {deleteGame.players.attackers.name} vs {deleteGame.players.defenders.name}
            </strong>
            <span>
              {rulesets.find((rule) => rule.id === deleteGame.ruleset)?.name ?? deleteGame.ruleset}{' '}
              · {deleteGame.recorded_plies ?? deleteGame.total_moves ?? deleteGame.ply} moves ·{' '}
              {date(deleteGame.created_at)}
            </span>
            <small>Game {deleteGame.id.slice(0, 8)}</small>
          </div>
        </RecordsConfirm>
      )}
      {deletePlayer && (
        <RecordsConfirm
          title={`Delete player ${deletePlayer.name}?`}
          busy={deleting}
          error={deleteError}
          onCancel={() => setDeletePlayer(null)}
          onConfirm={() => void removePlayer()}
        >
          <p>
            This removes <strong>{deletePlayer.name}</strong> from the player selection. Their saved
            games, name and statistics remain in the Hall of records.
          </p>
        </RecordsConfirm>
      )}
    </>
  );
}
export function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <div className="metric panel">
      <p className="eyebrow">{label}</p>
      <strong>{value}</strong>
      <span>{detail}</span>
    </div>
  );
}
function StatsTable({ rows, group }: { rows: StatRow[]; group: 'ruleset' | 'player' }) {
  if (!rows.length) return <p className="empty-copy">Complete a game to see results here.</p>;
  return (
    <div
      className="table-scroll"
      role="region"
      aria-label={
        group === 'ruleset' ? 'Battlefield statistics table' : 'Player and AI statistics table'
      }
      tabIndex={0}
    >
      <table>
        <thead>
          <tr>
            <th>{group === 'ruleset' ? 'Ruleset' : 'Player'}</th>
            <th>Played</th>
            {group === 'ruleset' ? (
              <>
                <th>Attacker win</th>
                <th>Defender win</th>
                <th>Draw</th>
              </>
            ) : (
              <>
                <th>W / D / L</th>
                <th>Win / draw / loss %</th>
              </>
            )}
            <th>Avg. moves</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.id ?? row.ruleset ?? i}>
              <th>
                {row.name ?? row.ruleset}
                <small>
                  {row.kind === 'ai'
                    ? `Computer${row.opponent_name ? ` · ${row.opponent_name}` : ''}`
                    : row.kind === 'human'
                      ? 'Human'
                      : ''}
                </small>
                {row.kind === 'ai' && (
                  <RecordsParameters config={row.config} variantId={row.variant_id} />
                )}
              </th>
              <td>{row.games ?? 0}</td>
              {group === 'ruleset' ? (
                <>
                  <td>{percent(row.attacker_win_rate)}</td>
                  <td>{percent(row.defender_win_rate)}</td>
                  <td>{percent(row.draw_rate)}</td>
                </>
              ) : (
                <>
                  <td className="ratio-cell">
                    {row.wins ?? 0} / {row.draws ?? 0} / {row.losses ?? 0}
                  </td>
                  <td className="ratio-cell">
                    {percent(row.win_rate)} / {percent(row.draw_rate)} / {percent(row.loss_rate)}
                  </td>
                </>
              )}
              <td>{Number(row.average_game_length ?? row.average_length ?? 0).toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="table-caption">
        {group === 'player' ? 'W / D / L = wins, draws, losses. ' : ''}Rates count completed games
        only.
      </p>
    </div>
  );
}
