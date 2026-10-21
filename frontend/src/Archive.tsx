import { useEffect, useState, type FormEvent } from 'react';
import { api } from './api';
import type { Game, Player, Ruleset, Stats, StatRow } from './types';

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
}: {
  rulesets: Ruleset[];
  onLoad: (game: Game) => void;
  onError: (error: string) => void;
  players: Player[];
  refreshPlayers: () => void;
}) {
  const [games, setGames] = useState<Game[]>([]);
  const [stats, setStats] = useState<Stats>({});
  const [ruleset, setRuleset] = useState('');
  const [mode, setMode] = useState('');
  const [kind, setKind] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    const query = new URLSearchParams({
      ...(ruleset && { ruleset }),
      ...(mode && { mode }),
      ...(kind && { kind }),
    });
    Promise.all([api<Game[]>(`/games?${query}`), api<Stats>(`/stats?${query}`)])
      .then(([records, next]) => {
        if (alive) {
          setGames(records);
          setStats(next);
        }
      })
      .catch((e) => onError(e.message))
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [ruleset, mode, kind, onError]);
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
  const byRules = Array.isArray(stats.by_ruleset) ? stats.by_ruleset : [];
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
            {rulesets.map((rule) => (
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
      <div className="archive-layout">
        <section className="panel records">
          <div className="panel-title">
            <h2>Recorded games</h2>
            <span className="badge">{games.length}</span>
          </div>
          {loading ? (
            <p className="empty-copy">Loading your sagas…</p>
          ) : !games.length ? (
            <div className="empty-state compact">
              <span className="empty-rune">ᛝ</span>
              <h3>A history yet to be written.</h3>
              <p>Your saved games will appear here.</p>
            </div>
          ) : (
            <div className="records-list">
              {games.map((game) => (
                <button className="record" key={game.id} onClick={() => void load(game.id)}>
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
              ))}
            </div>
          )}
          <div className="section-separator" />
          <h3>Results by battlefield</h3>
          <StatsTable rows={byRules} group="ruleset" />
          <h3>Player & AI performance</h3>
          <StatsTable rows={byPlayer} group="player" />
        </section>
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
              </li>
            ))}
          </ul>
        </aside>
      </div>
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
    <div className="table-scroll">
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
