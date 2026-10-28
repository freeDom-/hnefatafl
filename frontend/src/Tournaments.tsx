import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, apiFetch, errorMessage } from './api';
import { notify } from './notifications';
import Board, { coordinate, Crown } from './Board';
import RecordsConfirm from './RecordsConfirm';
import RecordsParameters, { parameterSummary } from './RecordsParameters';
import { sortRulesets } from './sortRulesets';
import { suggestTournamentName } from './mythologicalNames';
import ModelTrainingSummary from './ModelTrainingSummary';
import TournamentWinMatrix from './TournamentWinMatrix';
import { selectStrongestModel } from './modelSelection';
import { modelOptionLabel } from './modelLabel';
import { profileConfiguration } from './profileConfiguration';
import {
  DEFAULT_ALPHA_BETA_SEARCH,
  TABLE_CAPACITY_MAX,
  TABLE_CAPACITY_MIN,
  effectiveSearch,
  presetSearch,
  searchFromConfig,
  searchPreset,
  type AlphaBetaPresetName,
  type AlphaBetaSearchSettings,
} from './alphaBetaSearch';
import ConfigImportButton from './ConfigImportButton';
import {
  readImportedTournamentConfig,
  TournamentConfigImportError,
} from './tournamentConfigImport';
import SearchWrapper from './SearchWrapper';
import HeuristicSelect, { heuristicFromConfig } from './HeuristicSelect';
import { getLocale, t, useI18n } from './i18n';
import type { AIProfile, Config, Game, Model, Ruleset } from './types';
import type {
  Tournament,
  TournamentConfig,
  TournamentDiagnostics,
  TournamentGame,
  TournamentPlayer,
  TournamentReplay,
} from './tournamentTypes';
import './features.css';
import './tournaments.css';

const date = (value: string, locale: string) =>
  new Date(value).toLocaleDateString(locale, {
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
const score = (value: number) => value.toLocaleString(getLocale(), { maximumFractionDigits: 1 });
const active = (tournament: Tournament) => tournament.status === 'running';
const playerName = (player: TournamentPlayer) =>
  player.model_name || (player.profile_name ? t(player.profile_name) : player.name);
const seatRune = (player: TournamentPlayer) =>
  player.agent_rune || player.rune ? (
    <span className="identity-rune" data-rune={player.agent_rune || player.rune} aria-hidden="true">
      {player.agent_rune || player.rune}
    </span>
  ) : null;
const champion = (tournament: Tournament) => {
  if (!tournament.result?.games.length) return t('The challenge is set');
  const best = Math.max(...tournament.result.scores);
  const leaders = tournament.config.players.filter(
    (_, index) => tournament.result!.scores[index] === best,
  );
  if (leaders.length === tournament.config.players.length) return t('Level on points');
  if (leaders.length > 1)
    return t(
      tournament.status === 'completed'
        ? '{names} finish level on points'
        : '{names} share the lead',
      { names: leaders.map(playerName).join(' & ') },
    );
  return t(tournament.status === 'completed' ? '{name} wins on points' : '{name} leads on points', {
    name: playerName(leaders[0]),
  });
};
const defender = (match: TournamentGame) => match.defender_player ?? 1 - match.attacker_player;
const outcome = (match: TournamentGame, players: TournamentPlayer[]) => {
  if (!match.winner) return t('Move limit reached');
  if (match.winner === 'draw') return t('Rule draw');
  const winner = match.winner === 'attackers' ? match.attacker_player : defender(match);
  return t('{name} wins', { name: playerName(players[winner]) });
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
  const { locale, language } = useI18n();
  const [tournaments, setTournaments] = useState<Tournament[]>([]);
  const [selected, setSelected] = useState<Tournament | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [suggestedName, setSuggestedName] = useState('');
  const [deleting, setDeleting] = useState<Tournament | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState('');
  const [replay, setReplay] = useState<{ index: number; data: TournamentReplay } | null>(null);
  const [replaying, setReplaying] = useState<number | null>(null);
  const [matchesOpen, setMatchesOpen] = useState(false);
  const selectedId = useRef<string | null>(null);
  const requestNumber = useRef(0);
  const refreshNumber = useRef(0);
  const mutating = useRef(false);
  const mounted = useRef(true);
  const errorHandler = useRef(onError);
  const previousNameSuggestion = useRef('');
  errorHandler.current = onError;

  const openSetup = () => {
    const next = suggestTournamentName(
      [...tournaments.map((tournament) => tournament.name), previousNameSuggestion.current],
      undefined,
      language,
    );
    previousNameSuggestion.current = next;
    setSuggestedName(next);
    setNewOpen(true);
  };

  const select = useCallback(async (id: string) => {
    const request = ++requestNumber.current;
    selectedId.current = id;
    // The match log starts collapsed for every newly opened tournament; a
    // same-tournament data refresh below keeps the current expansion state.
    setMatchesOpen(false);
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
        setMatchesOpen(false);
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
  const exportTournament = async (tournament: Tournament) => {
    setExporting(true);
    try {
      const response = await apiFetch(`/tournaments/${tournament.id}/export`);
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(
          typeof error?.detail === 'string' ? error.detail : t('Tournament export failed.'),
        );
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `hnefatafl-tournament-${tournament.id}.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setExporting(false);
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
          <h1>{t('The tournament hall.')}</h1>
          <p>
            {t('Set your strategists against each other. Every result becomes part of their saga.')}
          </p>
        </div>
        <button className="button primary" onClick={openSetup} disabled={loading || running}>
          <span aria-hidden="true">＋</span>
          {t('New tournament')}
        </button>
      </section>
      {notice && (
        <p role="status" className="notice tournament-notice">
          {t(notice)}
        </p>
      )}
      <div className="tournament-layout">
        <aside className="panel tournament-history" aria-label={t('Saved tournaments')}>
          <div className="panel-title">
            <h2>{t('Your tournaments')}</h2>
            <span className="badge">{tournaments.length}</span>
          </div>
          <p className="subtle">
            {t('{count} completed · saved on this server', { count: completed })}
          </p>
          {loading ? (
            <p className="empty-copy">{t('Opening the hall…')}</p>
          ) : !tournaments.length ? (
            <div className="tournament-history-empty">
              <span aria-hidden="true">ᛟ</span>
              <p>{t('Your first challenge awaits.')}</p>
              <small>{t('Saved tournaments will appear here.')}</small>
            </div>
          ) : (
            <div className="tournament-saved-list">
              {tournaments.map((tournament) => (
                <button
                  key={tournament.id}
                  className={`tournament-saved ${selected?.id === tournament.id ? 'selected' : ''}`}
                  aria-label={t('View tournament {name}', { name: tournament.name })}
                  aria-pressed={selected?.id === tournament.id}
                  onClick={() => void select(tournament.id)}
                >
                  <span className={`tournament-status ${tournament.status}`}>
                    {t(statusLabel[tournament.status])}
                  </span>
                  <strong>{tournament.name}</strong>
                  <small>
                    {tournament.config.players.map((player, index) => (
                      <span key={index}>
                        {index > 0 ? ` ${t('vs')} ` : ''}
                        {seatRune(player)}
                        {playerName(player)}
                      </span>
                    ))}
                  </small>
                  <span className="tournament-saved-meta">
                    <small>{date(tournament.created_at, locale)}</small>
                    <small>
                      {tournament.progress.completed_games} / {tournament.progress.total_games}{' '}
                      {t('games')}
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
              <h2>{t('Let the strongest ideas meet.')}</h2>
              <p>
                {t(
                  'Compare algorithms, search settings or trained models in a balanced series. Every pair meets on both sides of the board.',
                )}
              </p>
              <div className="tournament-promise">
                <span>
                  <strong>01</strong>
                  {t('Choose your rivals')}
                </span>
                <span>
                  <strong>02</strong>
                  {t('Set the battlefields')}
                </span>
                <span>
                  <strong>03</strong>
                  {t('Explore every result')}
                </span>
              </div>
              <button
                className="button secondary"
                onClick={openSetup}
                disabled={loading || running}
              >
                {t('Set up a tournament')}
              </button>
              <small>{t('Saved automatically. Come back whenever you like.')}</small>
            </section>
          ) : (
            <section className="panel tournament-detail" aria-label={t('Tournament results')}>
              <div className="tournament-detail-heading">
                <div>
                  <span className={`tournament-status ${selected.status}`}>
                    {t(statusLabel[selected.status])}
                  </span>
                  <h2>{selected.name}</h2>
                  <p className="subtle">
                    {date(selected.created_at, locale)} ·{' '}
                    {t('{games} games per pairing per battlefield · seed {seed}', {
                      games: selected.config.games,
                      seed: selected.config.seed,
                    })}
                  </p>
                </div>
                <div className="tournament-actions">
                  <button
                    type="button"
                    className="button secondary"
                    disabled={busy || detailLoading || exporting}
                    onClick={() => void exportTournament(selected)}
                  >
                    {t(exporting ? 'Exporting results…' : 'Export results')}
                  </button>
                  {active(selected) ? (
                    <button
                      className="button secondary"
                      disabled={busy || detailLoading}
                      onClick={() => void stop()}
                    >
                      {busy ? t('Stopping…') : t('Stop tournament')}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="button secondary danger-text"
                      disabled={busy || detailLoading}
                      onClick={() => {
                        setDeleting(selected);
                        setDeleteError('');
                      }}
                    >
                      {t('Delete tournament data')}
                    </button>
                  )}
                </div>
              </div>
              {selected.error && (
                <p role="alert" className="form-error">
                  {t(selected.error)}
                </p>
              )}
              <div className="tournament-progress">
                <div>
                  <strong>
                    {t('{done} of {total} games saved', {
                      done: selected.progress.completed_games,
                      total: selected.progress.total_games,
                    })}
                  </strong>
                  <span>
                    {active(selected)
                      ? t('Playing the next match…')
                      : `${selected.progress.completed_games === selected.progress.total_games ? t('Series finished') : t('Partial series saved')}`}
                  </span>
                </div>
                <progress
                  aria-label={t('Tournament progress')}
                  max={selected.progress.total_games}
                  value={selected.progress.completed_games}
                />
              </div>
              <div className="tournament-standings">
                <div className="tournament-section-title">
                  <h3>{t('Standings')}</h3>
                  <span>{champion(selected)}</span>
                </div>
                <div className="tournament-opponents">
                  {selected.config.players.map((player, index) => {
                    const standing = selected.result?.standings.find((row) => row.player === index);
                    const points = standing?.score ?? 0;
                    const model =
                      player.model ?? models.find((entry) => entry.id === player.config.model_id);
                    return (
                      <article className={`tournament-standing rival-${index + 1}`} key={index}>
                        <div className="tournament-standing-top">
                          <span
                            className="tournament-avatar"
                            aria-hidden="true"
                            data-rune={player.agent_rune || player.rune}
                          >
                            {player.agent_rune || player.rune || '·'}
                          </span>
                          <div>
                            <small>{t('OPPONENT {number}', { number: index + 1 })}</small>
                            <h3>{playerName(player)}</h3>
                            <p>
                              {player.config.model_id
                                ? `#${String(player.config.model_id).slice(0, 8)} · ${parameterSummary(player.config)}`
                                : parameterSummary(player.config)}
                            </p>
                          </div>
                          <strong className="tournament-points">
                            {score(points)}
                            <small>{t('points')}</small>
                          </strong>
                        </div>
                        <div className="tournament-score-track" aria-hidden="true">
                          <span
                            style={{
                              width: `${(100 * points) / Math.max(1, standing?.games ?? 0)}%`,
                            }}
                          />
                        </div>
                        <dl className="tournament-score-stats">
                          <div>
                            <dt>{t('Wins')}</dt>
                            <dd>{standing?.wins ?? 0}</dd>
                          </div>
                          <div>
                            <dt>{t('Draws')}</dt>
                            <dd>{standing?.draws ?? 0}</dd>
                          </div>
                          <div>
                            <dt>{t('Losses')}</dt>
                            <dd>{standing?.losses ?? 0}</dd>
                          </div>
                          <div>
                            <dt>{t('Unfinished')}</dt>
                            <dd>{standing?.truncated ?? 0}</dd>
                          </div>
                        </dl>
                        <TournamentDiagnosticsPanel diagnostics={standing?.diagnostics} />
                        {model && <ModelTrainingSummary model={model} compact />}
                        <RecordsParameters
                          config={player.config}
                          label={t('Opponent {number} parameters', { number: index + 1 })}
                          variantId={player.variant_id}
                        />
                      </article>
                    );
                  })}
                </div>
                <p className="tournament-scoring">
                  {t(
                    'Win = 1 point · Rule draw = ½ point · Move limit reached = unfinished, 0 points. Stopped games still in progress are discarded.',
                  )}
                </p>
              </div>
              {selected.result && (
                <>
                  <div className="tournament-summary">
                    <span>
                      <strong>
                        {selected.result.average_game_length.toLocaleString(locale, {
                          minimumFractionDigits: 1,
                          maximumFractionDigits: 1,
                        })}
                      </strong>
                      {t('average moves')}
                    </span>
                    <span>
                      <strong>
                        {selected.result.average_decision_time.toLocaleString(locale, {
                          minimumFractionDigits: 3,
                          maximumFractionDigits: 3,
                        })}
                        {t('s')}
                      </strong>
                      {t('per decision')}
                    </span>
                    <span>
                      <strong>{selected.result.truncated}</strong>
                      {t('unfinished')}
                    </span>
                  </div>
                  <div className="tournament-section-title">
                    <h3>{t('Battlefield results')}</h3>
                    <span>{t('Wins by opponent')}</span>
                  </div>
                  <div className="tournament-battlefields">
                    {sortRulesets(rulesets)
                      .filter((rule) => selected.config.rulesets.includes(rule.id))
                      .map((rule) => {
                        const tally = selected.result?.by_ruleset[rule.id];
                        return (
                          <article className="tournament-battlefield" key={rule.id}>
                            <div className="tournament-battlefield-summary">
                              <span className="tournament-board-size">
                                {rule.size}
                                <small>× {rule.size}</small>
                              </span>
                              <div>
                                <strong>{t(rule.name)}</strong>
                                <small>
                                  {t('{count} games played', { count: tally?.games ?? 0 })}
                                </small>
                                <ul className="tournament-battlefield-wins">
                                  {selected.config.players.map((player, index) => (
                                    <li key={index}>
                                      <span>
                                        {seatRune(player)}
                                        {playerName(player)}
                                      </span>
                                      <strong>
                                        {t('{count} wins', { count: tally?.wins[index] ?? 0 })}
                                      </strong>
                                    </li>
                                  ))}
                                </ul>
                                <p>
                                  {t('{draws} draws · {unfinished} unfinished', {
                                    draws: tally?.draws ?? 0,
                                    unfinished: tally?.truncated ?? 0,
                                  })}
                                </p>
                              </div>
                            </div>
                            <TournamentWinMatrix
                              players={selected.config.players}
                              games={selected.result?.games ?? []}
                              ruleset={rule.id}
                              battlefield={t(rule.name)}
                            />
                          </article>
                        );
                      })}
                  </div>
                </>
              )}
              <div className="tournament-section-title">
                <h3>
                  <button
                    type="button"
                    className="disclosure-heading"
                    aria-expanded={matchesOpen}
                    aria-controls="tournament-match-log"
                    onClick={() => setMatchesOpen((value) => !value)}
                  >
                    <span aria-hidden="true">{matchesOpen ? '−' : '＋'}</span> {t('Match log')}
                  </button>
                </h3>
                <span>
                  {t('{count} recorded games', { count: selected.result?.games.length ?? 0 })}
                </span>
              </div>
              <div id="tournament-match-log" hidden={!matchesOpen}>
                {!selected.result?.games.length ? (
                  <p className="empty-copy">
                    {active(selected)
                      ? t(
                          'The first result will appear when its game finishes. You can leave this page; the tournament will keep running.',
                        )
                      : t('No complete game records were saved for this series.')}
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
                            {t(
                              rulesets.find((rule) => rule.id === match.ruleset)?.name ??
                                match.ruleset,
                            )}{' '}
                            ·{' '}
                            {t('{moves} moves · {seconds}s / decision', {
                              moves: match.plies,
                              seconds: match.average_decision_time.toLocaleString(locale, {
                                minimumFractionDigits: 3,
                                maximumFractionDigits: 3,
                              }),
                            })}
                          </small>
                          <p>
                            <span className="mini-piece black" aria-hidden="true" />
                            {seatRune(selected.config.players[match.attacker_player])}
                            {playerName(selected.config.players[match.attacker_player])}{' '}
                            <span className="tournament-versus">{t('vs')}</span>{' '}
                            <span className="mini-piece ivory" aria-hidden="true" />
                            {seatRune(selected.config.players[defender(match)])}
                            {playerName(selected.config.players[defender(match)])}
                          </p>
                          <small className="tournament-match-reason">
                            {t(match.reason.replaceAll('_', ' '))}
                          </small>
                        </div>
                        <button
                          className="button secondary small"
                          aria-label={t('Replay game {number}', { number: index + 1 })}
                          disabled={replaying !== null || detailLoading}
                          onClick={() => void openReplay(index)}
                        >
                          {replaying === index ? t('Loading…') : t('Replay ↗')}
                        </button>
                      </article>
                    ))}
                  </div>
                )}
              </div>
            </section>
          )}
        </div>
      </div>
      {newOpen && (
        <TournamentSetup
          initialName={suggestedName}
          existingNames={tournaments.map((tournament) => tournament.name)}
          onSuggestedName={(name) => {
            previousNameSuggestion.current = name;
          }}
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
            setMatchesOpen(false);
            setNewOpen(false);
            setNotice('Tournament started. Results are saved after every finished game.');
            void refresh().catch((error) => onError(error.message));
          }}
        />
      )}
      {deleting && (
        <RecordsConfirm
          title={t('Delete tournament {name}?', { name: deleting.name })}
          busy={busy}
          error={t(deleteError)}
          onCancel={() => setDeleting(null)}
          onConfirm={() => void remove()}
        >
          <p>
            {t(
              'This permanently removes {name}, its results and saved tournament replays. Your models, training runs and other saved games are kept.',
              { name: deleting.name },
            )}
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

function AlphaBetaSearchFields({
  prefix,
  search,
  onPreset,
  onField,
}: {
  prefix: string;
  search: AlphaBetaSearchSettings;
  onPreset: (name: AlphaBetaPresetName) => void;
  onField: (patch: Partial<AlphaBetaSearchSettings>) => void;
}) {
  const { locale } = useI18n();
  const preset = searchPreset(search);
  // Legacy overrides every optimized flag, so the irrelevant switches and the
  // table capacity are disabled instead of showing active-looking settings that
  // the backend ignores.
  const legacy = search.legacy;
  const toggle = (
    key:
      | 'legacy'
      | 'transposition'
      | 'move_ordering'
      | 'symmetry_transposition'
      | 'symmetry_pruning',
    label: string,
    tooltip: string,
    disabled = false,
  ) => (
    <label key={key} title={t(tooltip)}>
      <input
        type="checkbox"
        aria-label={`${prefix} ${t(label).toLocaleLowerCase(locale)}`}
        checked={search[key]}
        disabled={disabled}
        onChange={(event) => onField({ [key]: event.target.checked })}
      />
      {t(label)}
    </label>
  );
  return (
    <fieldset className="tournament-search-optimizations">
      <legend>{t('Alpha-beta optimizations')}</legend>
      <label>
        {t('Search preset')}
        <select
          aria-label={`${prefix} ${t('search preset').toLocaleLowerCase(locale)}`}
          value={preset}
          onChange={(event) => onPreset(event.target.value as AlphaBetaPresetName)}
        >
          <option value="legacy">{t('Legacy alpha-beta')}</option>
          <option value="optimized">{t('Optimized default')}</option>
          <option value="symmetry">{t('Full symmetry')}</option>
          <option value="custom">{t('Custom')}</option>
        </select>
      </label>
      {legacy && (
        <p className="subtle">
          {t(
            'Legacy uses the original exact-value cache search. Every optimization switch and the table capacity below are ignored while it is selected.',
          )}
        </p>
      )}
      <div className="form-grid tournament-search-flags">
        {toggle(
          'legacy',
          'Legacy search',
          'Use the original exact-value cache search and ignore every optimized option below.',
        )}
        {toggle(
          'transposition',
          'Transposition table',
          'Reuse positions that were already searched during this game.',
          legacy,
        )}
        {toggle(
          'move_ordering',
          'Improved move ordering',
          'Try known-good moves earlier using the transposition table, principal variation, killers and history.',
          legacy,
        )}
        {toggle(
          'symmetry_transposition',
          'D4-canonical transpositions',
          'Share positions that are rotations or reflections of each other.',
          legacy,
        )}
        {toggle(
          'symmetry_pruning',
          'Symmetry move pruning',
          'Search one representative per symmetric move group.',
          legacy,
        )}
      </div>
      <label
        title={t('Bounded number of stored transposition entries. Larger tables use more memory.')}
      >
        {t('Transposition table capacity')}
        <input
          type="number"
          aria-label={`${prefix} ${t('transposition table capacity').toLocaleLowerCase(locale)}`}
          min={TABLE_CAPACITY_MIN}
          max={TABLE_CAPACITY_MAX}
          step={1}
          required
          disabled={legacy}
          value={search.table_capacity}
          onChange={(event) => onField({ table_capacity: Number(event.target.value) })}
        />
      </label>
    </fieldset>
  );
}

function TournamentDiagnosticsPanel({ diagnostics }: { diagnostics?: TournamentDiagnostics }) {
  const { locale } = useI18n();
  if (!diagnostics || !diagnostics.decisions) return null;
  const number = (value: number, digits = 0) =>
    value.toLocaleString(locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  const rows: [string, string][] = [];
  if (diagnostics.mean_completed_depth != null)
    rows.push([t('Mean completed depth'), number(diagnostics.mean_completed_depth, 2)]);
  if (diagnostics.mean_decision_time != null)
    rows.push([t('Mean decision time'), `${number(diagnostics.mean_decision_time, 3)}${t('s')}`]);
  if (diagnostics.mean_nodes != null)
    rows.push([t('Mean nodes'), number(Math.round(diagnostics.mean_nodes))]);
  if (diagnostics.mean_evaluations != null)
    rows.push([t('Mean evaluations'), number(Math.round(diagnostics.mean_evaluations))]);
  if (diagnostics.transposition_cutoffs != null)
    rows.push([t('Transposition cutoffs'), number(diagnostics.transposition_cutoffs)]);
  if (diagnostics.symmetry_moves_skipped != null)
    rows.push([t('Symmetry-pruned moves'), number(diagnostics.symmetry_moves_skipped)]);
  if (diagnostics.depth_cap_reached_fraction != null)
    rows.push([
      t('Reached depth cap'),
      `${Math.round(100 * diagnostics.depth_cap_reached_fraction)}%`,
    ]);
  if (!rows.length) return null;
  return (
    <details className="tournament-diagnostics">
      <summary>{t('Search diagnostics')}</summary>
      <dl className="tournament-score-stats">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

const fallbackPlayer = (index: number): TournamentPlayer =>
  index % 2
    ? {
        name: 'Jarl',
        profile_id: 'alpha_beta',
        config: { algorithm: 'alpha_beta', depth: 5, time_limit: 2 },
      }
    : {
        name: 'Baseline',
        profile_id: 'baseline',
        config: { algorithm: 'baseline', time_limit: 1 },
      };

function recommendedPlayer(
  models: Model[],
  profiles: AIProfile[],
  ruleset: string | undefined,
  fallback: TournamentPlayer,
): TournamentPlayer {
  const best = ruleset ? selectStrongestModel(models, ruleset)?.model : undefined;
  if (!best) return fallback;
  const profile = profiles.find((candidate) => candidate.algorithm === 'neural');
  return {
    name: best.name,
    profile_id: profile?.id ?? 'neural',
    config: {
      ...profile?.parameters,
      algorithm: 'neural',
      model_id: best.id,
      time_limit: 10,
    },
  };
}

function TournamentSetup({
  initialName,
  existingNames,
  onSuggestedName,
  rulesets,
  profiles,
  models,
  onClose,
  onCreated,
}: {
  initialName: string;
  existingNames: string[];
  onSuggestedName: (name: string) => void;
  rulesets: Ruleset[];
  profiles: AIProfile[];
  models: Model[];
  onClose: () => void;
  onCreated: (tournament: Tournament) => void;
}) {
  const { locale, language } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(initialName);
  const previousSuggestion = useRef(initialName);
  const manualOpponents = useRef<boolean[]>([false, false]);
  const [config, setConfig] = useState<TournamentConfig>(() => ({
    players: [0, 1].map((index) =>
      recommendedPlayer(models, profiles, 'brandubh', fallbackPlayer(index)),
    ),
    rulesets: ['brandubh'],
    games: 2,
    max_plies: 150,
    seed: 42,
  }));
  const [busy, setBusy] = useState(false);
  const [importingConfig, setImportingConfig] = useState(false);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  const battlefieldKey = config.rulesets.join(',');
  useEffect(() => {
    const ruleset = config.rulesets.length === 1 ? config.rulesets[0] : undefined;
    setConfig((previous) => {
      let changed = false;
      const players = previous.players.map((player, index) => {
        if (manualOpponents.current[index]) return player;
        const fallback = fallbackPlayer(index);
        const next = recommendedPlayer(models, profiles, ruleset, fallback);
        if (
          player.config.model_id === next.config.model_id &&
          player.config.algorithm === next.config.algorithm
        )
          return player;
        changed = true;
        return next;
      });
      return changed ? { ...previous, players } : previous;
    });
  }, [models, profiles, battlefieldKey]);
  const updatePlayer = (index: number, changes: Partial<TournamentPlayer>) =>
    setConfig((previous) => ({
      ...previous,
      players: previous.players.map((player, i) =>
        i === index ? { ...player, ...changes } : player,
      ),
    }));
  const parameter = (index: number, key: string, value: string | number) => {
    manualOpponents.current[index] = true;
    updatePlayer(index, { config: { ...config.players[index].config, [key]: value } });
  };
  const updateSearch = (index: number, patch: Partial<AlphaBetaSearchSettings>) => {
    manualOpponents.current[index] = true;
    const current = searchFromConfig(config.players[index].config) ?? DEFAULT_ALPHA_BETA_SEARCH;
    // Collapse legacy so an ignored switch can never create a misleading Custom
    // preset that disagrees with the effective backend configuration.
    updatePlayer(index, {
      config: {
        ...config.players[index].config,
        search: effectiveSearch({ ...current, ...patch }),
      },
    });
  };
  const applySearchPreset = (index: number, name: AlphaBetaPresetName) => {
    manualOpponents.current[index] = true;
    const current = searchFromConfig(config.players[index].config) ?? DEFAULT_ALPHA_BETA_SEARCH;
    updatePlayer(index, {
      config: {
        ...config.players[index].config,
        search: effectiveSearch(presetSearch(name, current)),
      },
    });
  };
  const addPlayer = () => {
    if (config.players.length >= 4) return;
    manualOpponents.current.push(false);
    setConfig((previous) => {
      if (previous.players.length >= 4) return previous;
      return {
        ...previous,
        players: [
          ...previous.players,
          recommendedPlayer(
            models,
            profiles,
            previous.rulesets.length === 1 ? previous.rulesets[0] : undefined,
            fallbackPlayer(previous.players.length),
          ),
        ],
      };
    });
  };
  const removePlayer = (index: number) => {
    if (config.players.length > 2) manualOpponents.current.splice(index, 1);
    setConfig((previous) =>
      previous.players.length <= 2
        ? previous
        : {
            ...previous,
            players: previous.players.filter((_, playerIndex) => playerIndex !== index),
          },
    );
  };
  const changeProfile = (index: number, id: string) => {
    const profile = profiles.find((item) => item.id === id);
    manualOpponents.current[index] = true;
    if (!profile) {
      const current = config.players[index].config;
      updatePlayer(index, {
        profile_id: undefined,
        name: 'Computer',
        config:
          current.algorithm === 'alpha_beta' && !searchFromConfig(current)
            ? { ...current, search: { ...DEFAULT_ALPHA_BETA_SEARCH } }
            : current,
      });
      return;
    }
    updatePlayer(index, {
      profile_id: id,
      config: {
        ...profileConfiguration(profile),
        ...(!['baseline', 'shield_maiden', 'alpha_beta', 'mcts'].includes(id)
          ? { time_limit: 10 }
          : {}),
        ...(profile.algorithm === 'neural' && config.rulesets.length === 1
          ? { model_id: selectStrongestModel(models, config.rulesets[0])?.model.id ?? '' }
          : {}),
      },
    });
  };
  const changeAlgorithm = (index: number, algorithm: string) => {
    manualOpponents.current[index] = true;
    const next: Config = { ...config.players[index].config, algorithm };
    delete next.heuristic_version;
    delete next.heuristic;
    delete next.model_id;
    delete next.evaluation;
    delete next.search_wrapper;
    delete next.search;
    if (algorithm === 'alpha_beta') next.search = { ...DEFAULT_ALPHA_BETA_SEARCH };
    if (algorithm === 'neural' && config.rulesets.length === 1)
      next.model_id = selectStrongestModel(models, config.rulesets[0])?.model.id ?? '';
    updatePlayer(index, { profile_id: undefined, name: 'Computer', config: next });
  };
  const compatible = models.filter(
    (model) => config.rulesets.length === 1 && config.rulesets[0] === model.ruleset,
  );
  const recommendation =
    config.rulesets.length === 1 ? selectStrongestModel(models, config.rulesets[0]) : null;
  const incompatibleModel = config.players.some(
    (player) =>
      player.config.algorithm === 'neural' &&
      !compatible.some((model) => model.id === player.config.model_id),
  );
  const importConfig = async (file: File) => {
    setImportingConfig(true);
    try {
      const imported = await readImportedTournamentConfig(file, profiles, models);
      // Keep every imported opponent until the user edits it; the battlefield
      // recommendation effect must not silently replace participants.
      manualOpponents.current = imported.config.players.map(() => true);
      setConfig(imported.config);
      if (imported.name) setName(imported.name);
      notify.success(t('Tournament configuration imported. Review the values before starting.'));
    } catch (error) {
      if (error instanceof TournamentConfigImportError && error.fields.length)
        notify.error(
          t('Invalid tournament configuration fields: {fields}.', {
            fields: error.fields.join(', '),
          }),
        );
      else notify.error(errorMessage(error));
    } finally {
      setImportingConfig(false);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      onCreated(
        await api<Tournament>('/tournaments', {
          name: name.trim(),
          ...config,
          players: config.players.map(({ profile_id, config: search }) => ({
            profile_id,
            config: search,
          })),
        }),
      );
    } catch (failure) {
      notify.error(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={ref}
      className="modal tournament-setup"
      aria-label={t('Set up a tournament')}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form onSubmit={(event) => void submit(event)}>
        <div className="modal-top">
          <div>
            <h2>{t('Set up a tournament')}</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={t('Close tournament setup')}
            onClick={onClose}
            disabled={busy}
          >
            ×
          </button>
        </div>
        <p className="tournament-setup-copy">
          {t(
            'Compare two to four opponents in a round-robin series. Every pair plays an equal number of games as attackers and defenders.',
          )}
        </p>
        <div className="tournament-import">
          <ConfigImportButton
            onImport={(file) => void importConfig(file)}
            busy={importingConfig}
            disabled={busy}
            label="Import configuration"
          />
          <span className="subtle">
            {t(
              'Import a portable JSON tournament configuration, then review and edit it before starting.',
            )}
          </span>
        </div>
        <div className="name-suggestion-field">
          <label htmlFor="tournament-name">{t('Tournament name')}</label>
          <div className="name-input-group">
            <input
              id="tournament-name"
              required
              maxLength={80}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <button
              type="button"
              className="icon-button"
              aria-label={t('Suggest tournament name')}
              title={t('Suggest a mythological name')}
              disabled={busy}
              onClick={() => {
                const next = suggestTournamentName(
                  [...existingNames, name, previousSuggestion.current],
                  undefined,
                  language,
                );
                previousSuggestion.current = next;
                setName(next);
                onSuggestedName(next);
              }}
            >
              ⚄
            </button>
          </div>
        </div>
        <fieldset className="tournament-fields">
          <legend>{t('Battlefields')}</legend>
          <div className="tournament-rulesets">
            {sortRulesets(rulesets).map((rule) => (
              <label className={config.rulesets.includes(rule.id) ? 'chosen' : ''} key={rule.id}>
                <input
                  type="checkbox"
                  aria-label={`${t(rule.name)} ${rule.size} × ${rule.size}`}
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
                <strong>{t(rule.name)}</strong>
                <small>
                  {rule.size} × {rule.size}
                </small>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="tournament-setup-opponents">
          {config.players.map((player, index) => {
            const prefix = t('Opponent {number}', { number: index + 1 });
            const description = profiles.find(
              (profile) => profile.id === player.profile_id,
            )?.description;
            const algorithm = player.config.algorithm;
            const neural = algorithm === 'neural';
            const wrapper = String(player.config.search_wrapper ?? 'mcts');
            const search = neural ? wrapper : String(algorithm);
            const alphaBetaOptimizations = !player.profile_id && algorithm === 'alpha_beta';
            const searchSettings = searchFromConfig(player.config) ?? DEFAULT_ALPHA_BETA_SEARCH;
            const fixedPreset = ['baseline', 'shield_maiden', 'alpha_beta', 'mcts'].includes(
              player.profile_id ?? '',
            );
            const selectedModel = models.find((model) => model.id === player.config.model_id);
            const field = (
              key: string,
              label: string,
              minimum: number,
              maximum: number,
              step = 1,
              fallback = minimum,
            ) => (
              <label key={key}>
                {t(label)}
                <input
                  type="number"
                  aria-label={`${prefix} ${t(label).toLocaleLowerCase(locale)}`}
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
                {config.players.length > 2 && (
                  <button
                    type="button"
                    className="icon-button tournament-remove-opponent"
                    aria-label={t('Remove opponent {number}', { number: index + 1 })}
                    disabled={busy}
                    onClick={() => removePlayer(index)}
                  >
                    ×
                  </button>
                )}
                <label>
                  {t('Profile')}
                  <select
                    aria-label={`${prefix} ${t('profile')}`}
                    value={player.profile_id ?? ''}
                    onChange={(event) => changeProfile(index, event.target.value)}
                  >
                    <option value="">{t('Custom opponent')}</option>
                    {profiles.map((profile) => (
                      <option value={profile.id} key={profile.id}>
                        {t(profile.name)}
                      </option>
                    ))}
                  </select>
                </label>
                {!player.profile_id && (
                  <label>
                    {t('Algorithm')}
                    <select
                      aria-label={`${prefix} ${t('Algorithm').toLocaleLowerCase(locale)}`}
                      value={String(algorithm)}
                      onChange={(event) => changeAlgorithm(index, event.target.value)}
                    >
                      <option value="baseline">{t('Tactical heuristic')}</option>
                      <option value="shield_maiden">{t('Shield maiden heuristic')}</option>
                      <option value="alpha_beta">{t('Alpha-beta search')}</option>
                      <option value="mcts">{t('Monte Carlo tree search')}</option>
                      <option value="neural">{t('Neural network')}</option>
                    </select>
                  </label>
                )}
                {algorithm === 'neural' && (
                  <label>
                    {t('Trained model')}
                    <select
                      aria-label={`${prefix} ${t('trained model')}`}
                      required
                      value={String(player.config.model_id ?? '')}
                      onChange={(event) => {
                        manualOpponents.current[index] = true;
                        parameter(index, 'model_id', event.target.value);
                      }}
                    >
                      <option value="">{t('Select a compatible model')}</option>
                      {compatible.map((model) => (
                        <option value={model.id} key={model.id}>
                          {modelOptionLabel(model, t)}
                        </option>
                      ))}
                    </select>
                    <small>{t('Trained models require one matching battlefield.')}</small>
                    {recommendation &&
                      player.config.model_id === recommendation.model.id &&
                      !manualOpponents.current[index] && (
                        <small className="model-recommendation">
                          {recommendation.basis === 'evaluation'
                            ? t(
                                'Best measured model selected: {name} ({score}% across {games} baseline games).',
                                {
                                  name: recommendation.model.name,
                                  score: ((recommendation.score ?? 0) * 100).toLocaleString(
                                    locale,
                                    { minimumFractionDigits: 1, maximumFractionDigits: 1 },
                                  ),
                                  games: recommendation.games,
                                },
                              )
                            : t('No comparable evaluation; latest model selected: {name}.', {
                                name: recommendation.model.name,
                              })}
                        </small>
                      )}
                  </label>
                )}
                {selectedModel && algorithm === 'neural' && (
                  <ModelTrainingSummary model={selectedModel} compact />
                )}
                {neural && (
                  <SearchWrapper
                    value={wrapper}
                    label={`${prefix} ${t('Search wrapper').toLocaleLowerCase(locale)}`}
                    onChange={(next) => parameter(index, 'search_wrapper', next)}
                  />
                )}
                {description && <p className="subtle">{t(description)}</p>}
                {alphaBetaOptimizations && (
                  <AlphaBetaSearchFields
                    prefix={prefix}
                    search={searchSettings}
                    onPreset={(name) => applySearchPreset(index, name)}
                    onField={(patch) => updateSearch(index, patch)}
                  />
                )}
                {!neural && (search === 'alpha_beta' || search === 'mcts') && (
                  <HeuristicSelect
                    value={heuristicFromConfig(player.config)}
                    label={`${prefix} ${t('Heuristic').toLocaleLowerCase(locale)}`}
                    onChange={(next) => parameter(index, 'heuristic', next)}
                  />
                )}
                {fixedPreset ? (
                  <p className="subtle">{parameterSummary(player.config)}</p>
                ) : (
                  <>
                    {search !== 'none' &&
                      field('time_limit', 'Time limit (seconds)', 0.01, 15, 0.01, 0.1)}
                    <details className="tournament-advanced">
                      <summary>{t('Search parameters')}</summary>
                      <div className="form-grid">
                        {search === 'alpha_beta' && field('depth', 'Search depth', 1, 5, 1, 2)}
                        {search === 'mcts' && (
                          <>
                            {field('iterations', 'Search iterations', 1, 5000, 1, 80)}
                            {field('exploration', 'Exploration', 0, 10, 0.1, 1.4)}
                            {algorithm === 'mcts' &&
                              field('rollout_depth', 'Rollout depth', 0, 50, 1, 8)}
                          </>
                        )}
                        {(search === 'mcts' || (neural && search === 'none')) &&
                          field('temperature', 'Temperature', 0, 5, 0.1, 0)}
                        {algorithm === 'baseline' && (
                          <p className="subtle">
                            {t('A tactical heuristic with no additional search controls.')}
                          </p>
                        )}
                      </div>
                    </details>
                  </>
                )}
              </fieldset>
            );
          })}
        </div>
        <div className="tournament-add-opponent">
          <span>
            {t('{count} of 4 opponents · every pairing swaps sides', {
              count: config.players.length,
            })}
          </span>
          <button
            type="button"
            className="button secondary small"
            onClick={addPlayer}
            disabled={busy || config.players.length >= 4}
          >
            {t('Add opponent')}
          </button>
        </div>
        <div className="form-grid tournament-run-settings">
          <label>
            {t('Games per battlefield')}
            <input
              type="number"
              aria-label={t('Games per battlefield')}
              aria-describedby="tournament-games-help"
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
            {t('Move limit per game')}
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
            {t('Tournament seed')}
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
            <strong>
              {(config.games *
                config.rulesets.length *
                (config.players.length * (config.players.length - 1))) /
                2}
            </strong>
            <span>
              {t('games across {count} pairings', {
                count: (config.players.length * (config.players.length - 1)) / 2,
              })}
            </span>
          </div>
        </div>
        <p id="tournament-games-help" className="tournament-scoring">
          {t(
            'The game count applies to each pairing on every selected battlefield. Use an even count to balance sides. The tournament seed controls all opponents’ game and move seeds. Tight time limits can vary with hardware. Unfinished games score no points.',
          )}
        </p>
        {(!config.rulesets.length || incompatibleModel) && (
          <p className="form-error">
            {!config.rulesets.length
              ? t('Choose at least one battlefield.')
              : t('Choose a compatible trained model and its single matching battlefield.')}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            {t('Cancel')}
          </button>
          <button
            className="button primary"
            disabled={busy || !name.trim() || !config.rulesets.length || incompatibleModel}
          >
            {busy ? t('Starting…') : t('Start tournament')}
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
  useI18n();
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
    moves: match.moves.map((move, i) => ({
      ...move,
      side: data.states[i].turn,
      captured: data.states[i].board.flatMap((piece, square) => {
        const enemy = data.states[i].turn === 'attackers' ? ['D', 'K'] : ['A'];
        return enemy.includes(piece) && data.states[i + 1].board[square] !== piece
          ? [{ square, piece }]
          : [];
      }),
    })),
    cursor,
    total_moves: match.moves.length,
    legal_moves: [],
    mode: 'ai-ai',
    players: {
      attackers: player(match.attacker_player),
      defenders: player(defender(match)),
    },
    version: 0,
    captured: { attackers: 0, defenders: 0 },
    created_at: tournament.created_at,
  };
  return (
    <dialog
      ref={ref}
      className="modal tournament-replay"
      aria-label={t('Replay game {number}', { number: index + 1 })}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="modal-top">
        <div>
          <h2>
            {t('Game {number}', { number: index + 1 })} ·{' '}
            {t(rulesets.find((rule) => rule.id === match.ruleset)?.name ?? match.ruleset)}
          </h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label={t('Close tournament replay')}
          onClick={onClose}
        >
          ×
        </button>
      </div>
      <p className="tournament-replay-result">
        {outcome(match, tournament.config.players)} · {t(match.reason.replaceAll('_', ' '))}
      </p>
      <p className="tournament-replay-players">
        {seatRune(tournament.config.players[match.attacker_player])}
        {playerName(tournament.config.players[match.attacker_player])}
        {' · '}
        {t('Attackers')} <span aria-hidden="true">{t('vs')}</span>{' '}
        {seatRune(tournament.config.players[defender(match)])}
        {playerName(tournament.config.players[defender(match)])}
        {' · '}
        {t('Defenders')}
      </p>
      <Board game={board} selected={null} onSelect={() => undefined} disabled />
      <div className="tournament-replay-controls">
        <button
          className="icon-button"
          aria-label={t('First tournament move')}
          onClick={() => setCursor(0)}
          disabled={!cursor}
        >
          ↤
        </button>
        <button
          className="icon-button"
          aria-label={t('Previous tournament move')}
          onClick={() => setCursor((value) => value - 1)}
          disabled={!cursor}
        >
          ←
        </button>
        <span aria-live="polite">
          {t('Move {current} / {total}', { current: cursor, total: match.plies })}
        </span>
        <button
          className="icon-button"
          aria-label={t('Next tournament move')}
          onClick={() => setCursor((value) => value + 1)}
          disabled={cursor >= data.states.length - 1}
        >
          →
        </button>
        <button
          className="icon-button"
          aria-label={t('Last tournament move')}
          onClick={() => setCursor(data.states.length - 1)}
          disabled={cursor >= data.states.length - 1}
        >
          ↦
        </button>
      </div>
      <label className="tournament-replay-range">
        {t('Replay position')}
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
          : t('Starting position')}{' '}
        ·{' '}
        {state.winner
          ? state.winner === 'draw'
            ? t('Rule draw')
            : t('{side} win', { side: t(state.winner) })
          : t('{side} to move', { side: t(state.turn) })}
      </p>
    </dialog>
  );
}
