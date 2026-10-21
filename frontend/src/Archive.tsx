import { t, useI18n, getLocale } from './i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import type { Game, Player, Ruleset, Stats, StatRow } from './types';
import { sortRulesets } from './sortRulesets';
import RecordsParameters, { ParameterValues } from './RecordsParameters';
import {
  matchesPlayer,
  matchesRecordedGame,
  sortStatRows,
  statPlayerDescription,
  type StatSort,
  type StatSortKey,
} from './recordTables';
import RecordsConfirm from './RecordsConfirm';
import './features.css';
import './archive.css';

const percent = (value?: number) =>
  `${((value ?? 0) * 100).toLocaleString(getLocale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
const date = (value?: string) =>
  value
    ? new Date(value).toLocaleDateString(getLocale(), {
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
  useI18n();
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
  const [recordName, setRecordName] = useState('');
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
  useEffect(() => setVisibleGames(10), [ruleset, mode, kind, recordName]);
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
    const request = generation.current;
    try {
      const game = await api<Game>(`/games/${id}`);
      if (request === generation.current) onLoad(game);
    } catch (e) {
      if (request === generation.current) onError((e as Error).message);
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
  const filteredGames = games.filter((game) => matchesRecordedGame(game, recordName));
  return (
    <>
      <section className="page-intro">
        <div>
          <p className="eyebrow">{t('EVERY SAGA LEAVES A TRACE')}</p>
          <h1>{t('Hall of records.')}</h1>
          <p>{t('Revisit your battles. Learn from your victories, and your defeats.')}</p>
        </div>
        <span className="decorative-rune" aria-hidden="true">
          ᛟ
        </span>
      </section>
      <div className="filters panel">
        <label>
          {t('Battlefield')}
          <select value={ruleset} onChange={(e) => setRuleset(e.target.value)}>
            <option value="">{t('All rulesets')}</option>
            {sortRulesets(rulesets).map((rule) => (
              <option key={rule.id} value={rule.id}>
                {t(rule.name)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('Game mode')}
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">{t('All modes')}</option>
            <option value="human-human">{t('Pass & play')}</option>
            <option value="human-ai">{t('Human vs AI')}</option>
            <option value="ai-ai">{t('AI vs AI')}</option>
          </select>
        </label>
        <label>
          {t('Player type')}
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">{t('Humans & computers')}</option>
            <option value="human">{t('Human players')}</option>
            <option value="ai">{t('AI opponents')}</option>
          </select>
        </label>
      </div>
      <div className="metric-grid">
        <Metric
          label={t('BATTLES RECORDED')}
          value={stats.total_games ?? games.length}
          detail={t('{count} completed games', { count: stats.completed_games ?? 0 })}
        />
        <Metric
          label={t('ATTACKER VICTORIES')}
          value={stats.attackers_wins ?? 0}
          detail={t('{rate} of completed games', { rate: percent(stats.attacker_win_rate) })}
        />
        <Metric
          label={t('DEFENDER VICTORIES')}
          value={stats.defenders_wins ?? 0}
          detail={t('{rate} of completed games', { rate: percent(stats.defender_win_rate) })}
        />
        <Metric
          label={t('AVERAGE GAME LENGTH')}
          value={Number(stats.average_game_length ?? stats.average_length ?? 0).toLocaleString(
            getLocale(),
            { minimumFractionDigits: 1, maximumFractionDigits: 1 },
          )}
          detail={t('{count} draws · {rate} of completed games', {
            count: stats.draws ?? 0,
            rate: percent(stats.draw_rate),
          })}
        />
      </div>
      {notice && (
        <p className="notice archive-notice" role="status">
          {t(notice)}
        </p>
      )}
      <section
        className="panel archive-statistics"
        aria-labelledby="archive-statistics-heading"
        aria-busy={loading}
      >
        <div className="archive-statistics-heading">
          <div>
            <p className="eyebrow">{t('THE STORY IN NUMBERS')}</p>
            <h2 id="archive-statistics-heading">{t('Statistics')}</h2>
            <p>
              {t(
                'Completed battles, compared at a glance. Filters apply to statistics and saved games.',
              )}
            </p>
          </div>
          <span className="archive-statistics-count">
            <strong>{stats.completed_games ?? 0}</strong> {t('completed')}
          </span>
        </div>
        <div className="archive-statistics-grid">
          <section
            aria-labelledby="archive-battlefield-heading"
            className="archive-statistics-block"
          >
            <h3 id="archive-battlefield-heading">{t('Results by battlefield')}</h3>
            <StatsTable rows={byRules} group="ruleset" rulesets={rulesets} />
          </section>
          <section
            aria-labelledby="archive-performance-heading"
            className="archive-statistics-block"
          >
            <h3 id="archive-performance-heading">{t('Player & AI performance')}</h3>
            <StatsTable rows={byPlayer} group="player" rulesets={rulesets} />
          </section>
        </div>
      </section>
      <div className="archive-layout">
        <details
          className="panel records records-history"
          open={historyOpen}
          onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
        >
          <summary aria-label={t('Recorded games')} className="archive-history-summary">
            <div>
              <p className="eyebrow">{t('YOUR SAVED BATTLES')}</p>
              <h2>{t('Recorded games')}</h2>
              <p>{t('Reopen a game or remove it from your records.')}</p>
            </div>
            <span className="badge">
              {recordName.trim() ? `${filteredGames.length} / ${games.length}` : games.length}
            </span>
            <span className="archive-history-chevron" aria-hidden="true">
              ⌄
            </span>
          </summary>
          <div className="record-name-filter archive-table-filter">
            <label>
              {t('Filter recorded games')}
              <input
                type="search"
                placeholder={t('Player or AI name')}
                value={recordName}
                onChange={(event) => setRecordName(event.target.value)}
              />
            </label>
            {recordName && (
              <button
                type="button"
                className="button secondary small"
                aria-label={t('Clear recorded game filter')}
                onClick={() => setRecordName('')}
              >
                {t('Clear')}
              </button>
            )}
          </div>
          {loading ? (
            <p className="empty-copy">{t('Loading your sagas…')}</p>
          ) : !games.length ? (
            <div className="empty-state compact">
              <span className="empty-rune">ᛝ</span>
              <h3>{t('A history yet to be written.')}</h3>
              <p>{t('Your saved games will appear here.')}</p>
            </div>
          ) : !filteredGames.length ? (
            <p className="empty-copy">{t('No recorded games match this name.')}</p>
          ) : (
            <div
              className="records-list"
              role="region"
              aria-label={t('Saved game list')}
              tabIndex={0}
            >
              {filteredGames.slice(0, visibleGames).map((game) => (
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
                          {t(
                            rulesets.find((rule) => rule.id === game.ruleset)?.name ?? game.ruleset,
                          )}
                        </strong>
                        <small>
                          {game.players?.attackers?.name ?? 'Attackers'} {t(' vs')}{' '}
                          {game.players?.defenders?.name ?? 'Defenders'}
                        </small>
                      </span>
                      <span className="record-result">
                        <strong>
                          {(game.recorded_winner ?? game.winner) === 'draw'
                            ? t('Draw')
                            : (game.recorded_winner ?? game.winner)
                              ? t('{side} win', {
                                  side:
                                    (game.recorded_winner ?? game.winner) === 'attackers'
                                      ? t('Attackers')
                                      : t('Defenders'),
                                })
                              : t('In progress')}
                        </strong>
                        <small>
                          {game.recorded_plies ?? game.total_moves ?? game.ply} {t(' moves ·')}{' '}
                          {date(game.created_at)}
                        </small>
                      </span>
                      <span aria-hidden="true">↗</span>
                    </button>
                    <button
                      type="button"
                      className="icon-button danger-text delete-record"
                      aria-label={t('Delete game {id}', { id: game.id })}
                      title={t('Delete recorded game')}
                      onClick={() => {
                        setDeleteGame(game);
                        setDeleteError('');
                      }}
                    >
                      ×
                    </button>
                  </div>
                  {game.seed !== undefined && (
                    <p className="record-game-seed">{t('Game seed {seed}', { seed: game.seed })}</p>
                  )}
                  {(['attackers', 'defenders'] as const)
                    .filter((side) => game.players[side]?.kind === 'ai')
                    .map((side) => (
                      <RecordsParameters
                        key={side}
                        config={game.players[side].config}
                        label={t('{side} AI parameters', {
                          side: side === 'attackers' ? t('Attacker') : t('Defender'),
                        })}
                      />
                    ))}
                </article>
              ))}
            </div>
          )}
          {filteredGames.length > visibleGames && (
            <div className="archive-load-more">
              <span>
                {t('Showing {shown} of {total} games', {
                  shown: Math.min(visibleGames, filteredGames.length),
                  total: filteredGames.length,
                })}
              </span>
              <button
                type="button"
                className="button secondary small"
                onClick={() => setVisibleGames((count) => count + 10)}
              >
                {t('Show 10 more games')}
              </button>
            </div>
          )}
        </details>
        <aside className="panel profiles-panel">
          <p className="eyebrow">{t('MAKE YOUR MARK')}</p>
          <h2>{t('Local players')}</h2>
          <p className="subtle">
            {t('Keep your identity across games and build a history of your own.')}
          </p>
          <form onSubmit={createPlayer} className="profile-form">
            <label>
              {t('Display name')}
              <input
                required
                maxLength={64}
                placeholder={t('e.g. Astrid')}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <button className="button primary" disabled={saving || !name.trim()}>
              {saving ? t('Saving…') : t('Create player')}
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
                  aria-label={t('Delete player {name}', { name: player.name })}
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
          title={t('Delete recorded game?')}
          busy={deleting}
          error={deleteError}
          onCancel={() => setDeleteGame(null)}
          onConfirm={() => void removeGame()}
        >
          <p>
            {t(
              'This permanently removes this game, its moves and result from the history. Statistics are recalculated from the remaining games. Player profiles and models are kept.',
            )}
          </p>
          <div className="archive-delete-preview">
            <strong>
              {deleteGame.players.attackers.name} {t(' vs ')}
              {deleteGame.players.defenders.name}
            </strong>
            <span>
              {t(
                rulesets.find((rule) => rule.id === deleteGame.ruleset)?.name ?? deleteGame.ruleset,
              )}{' '}
              · {deleteGame.recorded_plies ?? deleteGame.total_moves ?? deleteGame.ply}{' '}
              {t(' moves ·')} {date(deleteGame.created_at)}
            </span>
            <small>
              {t('Game ')}
              {deleteGame.id.slice(0, 8)}
            </small>
          </div>
        </RecordsConfirm>
      )}
      {deletePlayer && (
        <RecordsConfirm
          title={t('Delete player {name}?', { name: deletePlayer.name })}
          busy={deleting}
          error={deleteError}
          onCancel={() => setDeletePlayer(null)}
          onConfirm={() => void removePlayer()}
        >
          <p>
            {t(
              'This removes {name} from the player selection. Their saved games, name and statistics remain in the Hall of records.',
              { name: deletePlayer.name },
            )}
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
type StatColumn = { key: StatSortKey; label: string; help?: string };
const sharedColumns: StatColumn[] = [{ key: 'games', label: 'Played' }];

function StatsTable({
  rows,
  group,
  rulesets,
}: {
  rows: StatRow[];
  group: 'ruleset' | 'player';
  rulesets: Ruleset[];
}) {
  const [sort, setSort] = useState<StatSort | null>(() =>
    group === 'player' ? { key: 'outcomes', direction: 'descending' } : null,
  );
  const [filter, setFilter] = useState('');
  const [inspected, setInspected] = useState<StatRow | null>(null);
  if (!rows.length)
    return <p className="empty-copy">{t('Complete a game to see results here.')}</p>;
  const filtered = group === 'player' ? rows.filter((row) => matchesPlayer(row, filter)) : rows;
  const displayed = sortStatRows(filtered, sort, group, rulesets);
  const columns: StatColumn[] = [
    { key: 'name', label: group === 'ruleset' ? 'Ruleset' : 'Player' },
    ...sharedColumns,
    ...(group === 'ruleset'
      ? [
          { key: 'attacker_win_rate' as const, label: 'Attacker win' },
          { key: 'defender_win_rate' as const, label: 'Defender win' },
          { key: 'draw_rate' as const, label: 'Draw' },
        ]
      : [
          {
            key: 'outcomes' as const,
            label: 'W / D / L',
            help: 'Sort wins first, then draws, then losses',
          },
          {
            key: 'rates' as const,
            label: 'Win / draw / loss %',
            help: 'Sort win percentage first, then draw percentage, then loss percentage',
          },
        ]),
    { key: 'average_game_length', label: 'Avg. moves' },
  ];
  const changeSort = (key: StatSortKey) =>
    setSort((current) => ({
      key,
      direction:
        current?.key === key && current.direction === 'ascending' ? 'descending' : 'ascending',
    }));
  return (
    <>
      {group === 'player' && (
        <div className="archive-table-filter">
          <label>
            {t('Filter players')}
            <input
              type="search"
              placeholder={t('Player, profile or model name')}
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </label>
          {filter && (
            <button
              type="button"
              className="button secondary small"
              aria-label={t('Clear player filter')}
              onClick={() => setFilter('')}
            >
              {t('Clear')}
            </button>
          )}
          <span className="archive-filter-count" aria-live="polite">
            {t('{shown} of {total} players', { shown: displayed.length, total: rows.length })}
          </span>
        </div>
      )}
      <div
        className="table-scroll"
        role="region"
        aria-label={
          group === 'ruleset'
            ? t('Battlefield statistics table')
            : t('Player and AI statistics table')
        }
        tabIndex={0}
      >
        <table
          aria-label={
            group === 'ruleset' ? t('Results by battlefield') : t('Player & AI performance')
          }
        >
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={sort?.key === column.key ? sort.direction : 'none'}
                >
                  <button
                    type="button"
                    className="archive-sort-button"
                    aria-label={t('Sort by {column}', { column: t(column.label) })}
                    title={
                      column.help
                        ? t(column.help)
                        : t('Sort by {column}', { column: t(column.label) })
                    }
                    onClick={() => changeSort(column.key)}
                  >
                    {t(column.label)}
                    <span aria-hidden="true">
                      {sort?.key === column.key
                        ? sort.direction === 'ascending'
                          ? '↑'
                          : '↓'
                        : '↕'}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayed.map((row, i) => (
              <tr key={row.id ?? row.ruleset ?? i}>
                <th scope="row">
                  {group === 'ruleset' ? (
                    t(
                      rulesets.find((rule) => rule.id === row.ruleset)?.name ??
                        row.name ??
                        row.ruleset ??
                        '',
                    )
                  ) : (
                    <div className="stat-player-identity">
                      {row.kind === 'ai' ? (
                        <button
                          type="button"
                          className="stat-parameter-toggle"
                          aria-label={t('Show AI parameters for {name}', {
                            name: row.name ?? t('Computer'),
                          })}
                          title={t('Show AI parameters')}
                          aria-haspopup="dialog"
                          aria-expanded={inspected === row}
                          onClick={() => setInspected(row)}
                        >
                          <span aria-hidden="true">›</span>
                        </button>
                      ) : (
                        <span className="stat-parameter-spacer" aria-hidden="true" />
                      )}
                      <span className="stat-player-copy">
                        <span className="stat-player-name">{row.name ?? t('Unknown player')}</span>
                        <small title={statPlayerDescription(row)}>
                          {statPlayerDescription(row)}
                        </small>
                      </span>
                    </div>
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
                <td>
                  {Number(row.average_game_length ?? row.average_length ?? 0).toLocaleString(
                    getLocale(),
                    { minimumFractionDigits: 1, maximumFractionDigits: 1 },
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!displayed.length && <p className="empty-copy">{t('No players match this filter.')}</p>}
        <p className="table-caption">
          {group === 'player' ? t('W / D / L = wins, draws, losses. ') : ''}
          {t('Rates count completed games only. Select a column heading to change its order.')}
        </p>
      </div>
      {inspected && <AIParameterDialog row={inspected} onClose={() => setInspected(null)} />}
    </>
  );
}

function AIParameterDialog({ row, onClose }: { row: StatRow; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    return () => {
      dialog?.close();
      trigger?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal ai-parameter-dialog"
      aria-label={t('{name} AI parameters', { name: row.name ?? t('Computer') })}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="modal-top">
        <div>
          <p className="eyebrow">{t('RECORDED OPPONENT SETTINGS')}</p>
          <h2>{t('{name} AI parameters', { name: row.name ?? t('Computer') })}</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label={t('Close AI parameters')}
          onClick={onClose}
        >
          ×
        </button>
      </div>
      <p className="stat-dialog-description">{statPlayerDescription(row)}</p>
      {!!row.seed_count && (
        <p className="subtle">
          {t('Game seeds are recorded per game and are excluded from this comparison.')}
        </p>
      )}
      <div className="record-parameters">
        <ParameterValues config={row.config} variantId={row.variant_id} />
      </div>
      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose}>
          {t('Close')}
        </button>
      </div>
    </dialog>
  );
}
