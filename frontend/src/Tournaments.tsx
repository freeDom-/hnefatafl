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
import PageHeading from './PageHeading';
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
  TournamentMode,
  TournamentPlayer,
  TournamentReplay,
  TournamentResult,
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
/**
 * Bounded automatic retries for a full-detail refresh that failed transiently.
 * A terminal summary must never be lost because one request failed, and the
 * budget keeps a persistent outage from turning into a retry loop.
 */
export const DETAIL_RETRY_DELAYS = [1000, 3000, 8000];

/** Missing mode means the historical balanced round-robin tournament. */
const modeOf = (tournament: Tournament): TournamentMode =>
  tournament.mode ?? tournament.config.mode ?? 'round_robin';
/**
 * Cheap change marker for the selected tournament. A status change without a
 * new completed game (failure, cancellation, completion) is still detected.
 */
const progressMarker = (tournament: Tournament) =>
  `${tournament.status}|${tournament.progress.completed_games}|${tournament.progress.total_games}|${tournament.ended_at ?? ''}`;
const rate = (value: number | null, locale: string) =>
  value === null
    ? '—'
    : `${(value * 100).toLocaleString(locale, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      })}%`;

interface FixedRoleStats {
  attackerWins: number;
  defenderWins: number;
  draws: number;
  unfinished: number;
  played: number;
  completed: number;
  attackerWinRate: number | null;
  defenderWinRate: number | null;
  attackerScoreRate: number | null;
  defenderScoreRate: number | null;
}

/** Side-specific outcome counts for a fixed-role comparison. */
function fixedRoleStats(result: TournamentResult): FixedRoleStats {
  const attackerWins = result.games.filter((game) => game.winner === 'attackers').length;
  const defenderWins = result.games.filter((game) => game.winner === 'defenders').length;
  const draws = result.games.filter((game) => game.winner === 'draw').length;
  const unfinished = result.games.filter((game) => !game.winner).length;
  const completed = attackerWins + defenderWins + draws;
  const share = (numerator: number) => (completed ? numerator / completed : null);
  return {
    attackerWins,
    defenderWins,
    draws,
    unfinished,
    played: result.games.length,
    completed,
    // The win-rate denominator excludes unfinished games.
    attackerWinRate: share(attackerWins),
    defenderWinRate: share(defenderWins),
    // Score rate keeps the tournament convention: a win is 1 point, a draw 0.5.
    attackerScoreRate: share(attackerWins + draws / 2),
    defenderScoreRate: share(defenderWins + draws / 2),
  };
}
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
  const [fixedOpen, setFixedOpen] = useState(false);
  const [suggestedName, setSuggestedName] = useState('');
  const [deleting, setDeleting] = useState<Tournament | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState('');
  const [replay, setReplay] = useState<{ index: number; data: TournamentReplay } | null>(null);
  const [replaying, setReplaying] = useState<number | null>(null);
  const [matchesOpen, setMatchesOpen] = useState(false);
  /** A refresh that failed for the selected tournament, recoverable by hand. */
  const [detailError, setDetailError] = useState('');
  const selectedId = useRef<string | null>(null);
  const requestNumber = useRef(0);
  const refreshRevision = useRef(0);
  /** Change marker of the currently displayed full record. */
  const detailMarker = useRef<string | null>(null);
  /** Latest summary marker observed for the selected tournament. */
  const summaryMarker = useRef<string | null>(null);
  /** In-flight detail request: its tournament, its starting marker and its abort handle. */
  const detailRequest = useRef<{
    id: string;
    marker: string | null;
    controller: AbortController;
  } | null>(null);
  const listRequest = useRef<{ revision: number; promise: Promise<void> } | null>(null);
  /** Pending automatic retry of a failed detail refresh. */
  const retryTimer = useRef<number | null>(null);
  /** Summary marker the current retry budget belongs to. */
  const retryKey = useRef<string | null>(null);
  const retryAttempt = useRef(0);
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

  const openFixedSetup = () => {
    const next = suggestTournamentName(
      [...tournaments.map((tournament) => tournament.name), previousNameSuggestion.current],
      undefined,
      language,
    );
    previousNameSuggestion.current = next;
    setSuggestedName(next);
    setFixedOpen(true);
  };

  /** Drop a pending automatic retry; a newer request supersedes it. */
  const cancelDetailRetry = () => {
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
  };

  /**
   * Load the selected tournament's full record.
   *
   * An obsolete request is aborted, and a response that is older than a summary
   * already observed triggers exactly one immediate follow-up fetch, so a
   * delayed running snapshot can never leave terminal results hidden. A failed
   * request keeps that synchronization requirement and is retried a bounded
   * number of times before the panel offers a manual retry.
   */
  const loadDetail = useCallback(
    async (id: string, resetLog: boolean, marker: string | null): Promise<void> => {
      const request = ++requestNumber.current;
      cancelDetailRetry();
      detailRequest.current?.controller.abort();
      const controller = new AbortController();
      detailRequest.current = { id, marker, controller };
      if (resetLog) {
        // The match log starts collapsed for every newly opened tournament; a
        // same-tournament data refresh below keeps the current expansion state.
        setMatchesOpen(false);
        setReplay(null);
      }
      setDetailLoading(true);
      try {
        const next = await api<Tournament>(
          `/tournaments/${id}`,
          undefined,
          undefined,
          controller.signal,
        );
        // A slow response for tournament A never overwrites a newer selection B.
        if (!mounted.current || request !== requestNumber.current || selectedId.current !== id)
          return;
        const observed = summaryMarker.current;
        detailMarker.current = progressMarker(next);
        setSelected(next);
        setDetailError('');
        retryKey.current = null;
        retryAttempt.current = 0;
        // The summary moved on while this response was in flight, so the record
        // is already stale: fetch the newer one instead of waiting for a poll.
        if (observed !== null && observed !== detailMarker.current && observed !== marker)
          void loadDetail(id, false, observed);
      } catch (error) {
        // A superseded or unmounted request aborts silently.
        if (controller.signal.aborted || (error as Error)?.name === 'AbortError') return;
        if (!mounted.current || request !== requestNumber.current || selectedId.current !== id)
          return;
        const message = (error as Error).message;
        const key = `${id}|${summaryMarker.current ?? ''}`;
        if (retryKey.current !== key) {
          // Report a first failure once; later attempts stay quiet and visible
          // through the recoverable panel below.
          retryKey.current = key;
          retryAttempt.current = 0;
          errorHandler.current(message);
        }
        setDetailError(message);
        const delay = DETAIL_RETRY_DELAYS[retryAttempt.current];
        if (delay !== undefined) {
          retryAttempt.current += 1;
          cancelDetailRetry();
          retryTimer.current = window.setTimeout(() => {
            retryTimer.current = null;
            if (!mounted.current || selectedId.current !== id) return;
            void loadDetail(id, false, summaryMarker.current);
          }, delay);
        }
      } finally {
        if (detailRequest.current?.controller === controller) {
          detailRequest.current = null;
          if (mounted.current) setDetailLoading(false);
        }
      }
    },
    [],
  );

  /** Re-run a failed refresh for the selected tournament after the user asks. */
  const retryDetail = async () => {
    const id = selectedId.current;
    if (!id) return;
    retryKey.current = null;
    retryAttempt.current = 0;
    setDetailError('');
    await loadDetail(id, false, summaryMarker.current);
  };

  const select = useCallback(
    async (id: string) => {
      selectedId.current = id;
      // Never leave another tournament's match history on screen while its
      // replacement loads.
      setSelected((current) => (current?.id === id ? current : null));
      detailMarker.current = null;
      summaryMarker.current = null;
      retryKey.current = null;
      retryAttempt.current = 0;
      setDetailError('');
      await loadDetail(id, true, null);
    },
    [loadDetail],
  );

  const refresh = useCallback(async (): Promise<void> => {
    if (!mounted.current || mutating.current) return;
    const pending = listRequest.current;
    if (pending) {
      // Coalesce overlapping polls instead of stacking requests.
      await pending.promise;
      // A mutation invalidated the pending read; it needs one fresh read after it.
      if (mounted.current && pending.revision !== refreshRevision.current) return refresh();
      return;
    }
    const revision = refreshRevision.current;
    const promise = (async () => {
      const saved = await api<Tournament[]>('/tournaments?summary=true');
      if (!mounted.current || revision !== refreshRevision.current || mutating.current) return;
      setTournaments(saved);
      setLoading(false);
      const id = selectedId.current;
      const current = id ? saved.find((tournament) => tournament.id === id) : undefined;
      if (!current) {
        // First load, or the selected tournament is gone. The history is loaded
        // separately so a slow detail request never blocks the next poll.
        if (saved[0]) void select(saved[0].id);
        else {
          selectedId.current = null;
          summaryMarker.current = null;
          cancelDetailRetry();
          retryKey.current = null;
          retryAttempt.current = 0;
          setDetailError('');
          detailRequest.current?.controller.abort();
          detailRequest.current = null;
          setSelected(null);
          setReplay(null);
          setMatchesOpen(false);
        }
        return;
      }
      summaryMarker.current = progressMarker(current);
      // One mechanism decides who fetches: while an automatic retry is pending,
      // or once its budget for this marker is spent, polling must not start
      // another request. A genuinely newer marker resets the budget.
      const markerKey = `${current.id}|${summaryMarker.current}`;
      const retryPending = retryTimer.current !== null && retryKey.current === markerKey;
      const budgetSpent =
        retryKey.current === markerKey && retryAttempt.current >= DETAIL_RETRY_DELAYS.length;
      if (
        detailMarker.current !== summaryMarker.current &&
        detailRequest.current?.id !== current.id &&
        !retryPending &&
        !budgetSpent
      )
        void loadDetail(current.id, false, summaryMarker.current);
    })();
    listRequest.current = { revision, promise };
    try {
      await promise;
    } finally {
      if (listRequest.current?.promise === promise) listRequest.current = null;
    }
  }, [select, loadDetail]);

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
      refreshRevision.current++;
      listRequest.current = null;
      cancelDetailRetry();
      detailRequest.current?.controller.abort();
      detailRequest.current = null;
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
    refreshRevision.current++;
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
    refreshRevision.current++;
    setDeleteError('');
    try {
      await api(`/tournaments/${deleting.id}`, undefined, 'DELETE');
      if (selectedId.current === deleting.id) {
        selectedId.current = null;
        requestNumber.current++;
        summaryMarker.current = null;
        // An in-flight history or a pending retry for the deleted tournament is
        // now irrelevant.
        cancelDetailRetry();
        retryKey.current = null;
        retryAttempt.current = 0;
        setDetailError('');
        detailRequest.current?.controller.abort();
        detailRequest.current = null;
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
      <section className="page-toolbar tournament-intro">
        <PageHeading
          title={t('The tournament hall.')}
          subtitle={t(
            'Set your strategists against each other. Every result becomes part of their saga.',
          )}
        />
        <div className="tournament-create">
          <button className="button primary" onClick={openSetup} disabled={loading || running}>
            <span aria-hidden="true">＋</span>
            {t('New tournament')}
          </button>
          <button
            className="button secondary"
            onClick={openFixedSetup}
            disabled={loading || running}
            title={t('Compare one trained model as attackers against another as defenders.')}
          >
            {t('Fixed-role comparison')}
          </button>
        </div>
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
                  <span className="tournament-saved-tags">
                    <span className={`tournament-status ${tournament.status}`}>
                      {t(statusLabel[tournament.status])}
                    </span>
                    {modeOf(tournament) === 'fixed_roles' && (
                      <span className="badge tournament-mode-tag">
                        {t('Fixed-role comparison')}
                      </span>
                    )}
                  </span>
                  <strong>{tournament.name}</strong>
                  <small>
                    {tournament.config.players.map((player, index) => (
                      <span key={index}>
                        {index > 0 ? ` ${t('vs')} ` : ''}
                        {modeOf(tournament) === 'fixed_roles' &&
                          `${t(index === 0 ? 'Attackers' : 'Defenders')} `}
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
          {!selected && detailError ? (
            <section className="panel tournament-welcome">
              <span className="tournament-emblem" aria-hidden="true">
                <Crown />
              </span>
              <h2>{t('The latest results could not be loaded.')}</h2>
              <small role="alert">{t(detailError)}</small>
              <button
                className="button secondary"
                onClick={() => void retryDetail()}
                disabled={detailLoading}
              >
                {t('Try again')}
              </button>
            </section>
          ) : !selected ? (
            detailLoading ? (
              <section className="panel tournament-welcome" aria-busy="true">
                <span className="tournament-emblem" aria-hidden="true">
                  <Crown />
                </span>
                <h2>{t('Opening the tournament…')}</h2>
                <small>{t('Loading the saved results.')}</small>
              </section>
            ) : (
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
                <button
                  className="button secondary"
                  onClick={openFixedSetup}
                  disabled={loading || running}
                >
                  {t('Fixed-role comparison')}
                </button>
                <small>{t('Saved automatically. Come back whenever you like.')}</small>
              </section>
            )
          ) : (
            <section className="panel tournament-detail" aria-label={t('Tournament results')}>
              <div className="tournament-detail-heading">
                <div>
                  <span className="tournament-saved-tags">
                    <span className={`tournament-status ${selected.status}`}>
                      {t(statusLabel[selected.status])}
                    </span>
                    {modeOf(selected) === 'fixed_roles' && (
                      <span className="badge tournament-mode-tag">
                        {t('Fixed-role comparison')}
                      </span>
                    )}
                  </span>
                  <h2>{selected.name}</h2>
                  <p className="subtle">
                    {date(selected.created_at, locale)} ·{' '}
                    {modeOf(selected) === 'fixed_roles'
                      ? t('{games} games · attackers never swap sides · seed {seed}', {
                          games: selected.config.games,
                          seed: selected.config.seed,
                        })
                      : t('{games} games per pairing per battlefield · seed {seed}', {
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
              {detailError && (
                <p role="alert" className="form-error">
                  {t('The latest results could not be loaded.')} {t(detailError)}{' '}
                  <button
                    type="button"
                    className="button secondary small"
                    onClick={() => void retryDetail()}
                    disabled={detailLoading}
                  >
                    {t('Try again')}
                  </button>
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
              {selected.result && modeOf(selected) === 'fixed_roles' && (
                <div className="tournament-side-stats">
                  <div className="tournament-section-title">
                    <h3>{t('Side performance')}</h3>
                    <span>{t('Attackers and defenders are never swapped.')}</span>
                  </div>
                  <div className="tournament-opponents tournament-role-cards">
                    {(['attackers', 'defenders'] as const).map((role, index) => {
                      const player = selected.config.players[index];
                      const stats = fixedRoleStats(selected.result!);
                      const wins = role === 'attackers' ? stats.attackerWins : stats.defenderWins;
                      const winRate =
                        role === 'attackers' ? stats.attackerWinRate : stats.defenderWinRate;
                      const scoreRate =
                        role === 'attackers' ? stats.attackerScoreRate : stats.defenderScoreRate;
                      return (
                        <article
                          className={`tournament-standing tournament-role-card ${role}`}
                          key={role}
                        >
                          <small>{t(role === 'attackers' ? 'ATTACKERS' : 'DEFENDERS')}</small>
                          <h4>
                            {seatRune(player)}
                            {playerName(player)}
                          </h4>
                          <dl className="tournament-score-stats">
                            <div>
                              <dt>{t('Wins')}</dt>
                              <dd>{wins}</dd>
                            </div>
                            <div>
                              <dt>{t('Win rate')}</dt>
                              <dd>{rate(winRate, locale)}</dd>
                            </div>
                            <div>
                              <dt>{t('Score rate')}</dt>
                              <dd>{rate(scoreRate, locale)}</dd>
                            </div>
                          </dl>
                        </article>
                      );
                    })}
                  </div>
                  {(() => {
                    const stats = fixedRoleStats(selected.result!);
                    const outcomes = [
                      { key: 'attackers', label: t('Attacker wins'), count: stats.attackerWins },
                      { key: 'defenders', label: t('Defender wins'), count: stats.defenderWins },
                      { key: 'draws', label: t('Rule draws'), count: stats.draws },
                      { key: 'unfinished', label: t('Unfinished'), count: stats.unfinished },
                    ];
                    // A category without games takes no width, so the chart
                    // never shows a colour for a result that did not happen.
                    const visible = outcomes.filter((segment) => segment.count > 0);
                    return stats.played ? (
                      <>
                        <div
                          className="tournament-outcome-bar"
                          role="img"
                          aria-label={t(
                            '{played} games: {attackers} attacker wins, {defenders} defender wins, {draws} draws, {unfinished} unfinished.',
                            {
                              played: stats.played,
                              attackers: stats.attackerWins,
                              defenders: stats.defenderWins,
                              draws: stats.draws,
                              unfinished: stats.unfinished,
                            },
                          )}
                        >
                          {visible.map((segment) => (
                            <span
                              key={segment.key}
                              className={`outcome-${segment.key}`}
                              style={{ flexGrow: segment.count }}
                            />
                          ))}
                        </div>
                        <ul className="tournament-outcome-legend">
                          {outcomes.map((segment) => (
                            <li key={segment.key}>
                              <span
                                className={`swatch outcome-${segment.key}`}
                                aria-hidden="true"
                              />
                              {segment.label}
                              <strong>{segment.count}</strong>
                            </li>
                          ))}
                        </ul>
                      </>
                    ) : (
                      <p className="empty-copy">{t('No completed games yet.')}</p>
                    );
                  })()}
                  <p className="tournament-scoring">
                    {t(
                      'Win rate and score rate use completed games only; unfinished games are excluded. A win scores 1 point and a rule draw 0.5 for each side.',
                    )}
                  </p>
                </div>
              )}
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
                            <small>
                              {modeOf(selected) === 'fixed_roles'
                                ? t(index === 0 ? 'ATTACKERS' : 'DEFENDERS')
                                : t('OPPONENT {number}', { number: index + 1 })}
                            </small>
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
            refreshRevision.current++;
            selectedId.current = created.id;
            requestNumber.current++;
            detailMarker.current = progressMarker(created);
            setSelected(created);
            setReplay(null);
            setMatchesOpen(false);
            setNewOpen(false);
            setNotice('Tournament started. Results are saved after every finished game.');
            void refresh().catch((error) => onError(error.message));
          }}
        />
      )}
      {fixedOpen && (
        <FixedRoleSetup
          initialName={suggestedName}
          existingNames={tournaments.map((tournament) => tournament.name)}
          onSuggestedName={(name) => {
            previousNameSuggestion.current = name;
          }}
          rulesets={rulesets}
          models={models}
          profiles={profiles}
          onClose={() => setFixedOpen(false)}
          onCreated={(created) => {
            refreshRevision.current++;
            selectedId.current = created.id;
            requestNumber.current++;
            detailMarker.current = progressMarker(created);
            setSelected(created);
            setReplay(null);
            setMatchesOpen(false);
            setFixedOpen(false);
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
  modelBacked = false,
}: {
  prefix: string;
  search: AlphaBetaSearchSettings;
  onPreset: (name: AlphaBetaPresetName) => void;
  onField: (patch: Partial<AlphaBetaSearchSettings>) => void;
  /** Neural evaluation cannot reuse symmetric positions, so those switches are off. */
  modelBacked?: boolean;
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
          {!modelBacked && <option value="symmetry">{t('Full symmetry')}</option>}
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
          legacy || modelBacked,
        )}
        {toggle(
          'symmetry_pruning',
          'Symmetry move pruning',
          'Search one representative per symmetric move group.',
          legacy || modelBacked,
        )}
      </div>
      {modelBacked && (
        <p className="subtle">
          {t(
            'Neural evaluation cannot reuse rotated or reflected positions, so symmetry search is unavailable.',
          )}
        </p>
      )}
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

function FixedRoleSetup({
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
  const [ruleset, setRuleset] = useState('brandubh');
  const [attackerId, setAttackerId] = useState(
    () => selectStrongestModel(models, 'brandubh')?.model.id ?? '',
  );
  const [defenderId, setDefenderId] = useState(
    () => selectStrongestModel(models, 'brandubh')?.model.id ?? '',
  );
  // One shared search configuration is submitted for both seats; only the model
  // identity differs, so neither side can drift into hidden defaults.
  const [shared, setShared] = useState<Config>({
    algorithm: 'neural',
    search_wrapper: 'mcts',
    time_limit: 1,
    iterations: 80,
    exploration: 1.4,
    temperature: 0,
  });
  const [games, setGames] = useState(20);
  const [maxPlies, setMaxPlies] = useState(300);
  const [seed, setSeed] = useState(42);
  const [busy, setBusy] = useState(false);
  const [importingConfig, setImportingConfig] = useState(false);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  const compatible = models.filter((model) => model.ruleset === ruleset);
  const wrapper = String(shared.search_wrapper ?? 'mcts');
  const alphaBeta = searchFromConfig(shared) ?? DEFAULT_ALPHA_BETA_SEARCH;
  const attacker = models.find((model) => model.id === attackerId);
  const defender = models.find((model) => model.id === defenderId);
  const attackerCompatible = compatible.some((model) => model.id === attackerId);
  const defenderCompatible = compatible.some((model) => model.id === defenderId);
  const valid =
    !!name.trim() &&
    attackerCompatible &&
    defenderCompatible &&
    games >= 1 &&
    games <= 100 &&
    maxPlies >= 1 &&
    maxPlies <= 1000 &&
    seed >= 0 &&
    seed <= 4294967295;
  const update = (key: string, value: string | number) =>
    setShared((previous) => ({ ...previous, [key]: value }));
  const changeWrapper = (next: string) =>
    setShared((previous) => {
      const updated: Config = { ...previous, search_wrapper: next };
      // Nested alpha-beta settings are rejected for every other wrapper.
      if (next === 'alpha_beta' && !updated.search)
        updated.search = { ...DEFAULT_ALPHA_BETA_SEARCH };
      if (next !== 'alpha_beta') delete updated.search;
      return updated;
    });
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
        aria-label={`${t('Shared')} ${t(label).toLocaleLowerCase(locale)}`}
        min={minimum}
        max={maximum}
        step={step}
        required
        value={Number(shared[key] ?? fallback)}
        onChange={(event) => update(key, Number(event.target.value))}
      />
    </label>
  );
  const modelField = (
    role: 'attackers' | 'defenders',
    value: string,
    setValue: (id: string) => void,
  ) => {
    const selected = models.find((model) => model.id === value);
    const compatibleSelection = compatible.some((model) => model.id === value);
    return (
      <label>
        {t('Trained model')}
        <select
          aria-label={`${t(role === 'attackers' ? 'Attackers' : 'Defenders')} ${t('trained model')}`}
          required
          value={value}
          onChange={(event) => setValue(event.target.value)}
        >
          <option value="">{t('Select a compatible model')}</option>
          {compatible.map((model) => (
            <option value={model.id} key={model.id}>
              {modelOptionLabel(model, t)}
            </option>
          ))}
          {value && !compatibleSelection && (
            <option value={value}>
              {selected
                ? `${selected.name} · ${t('incompatible battlefield')}`
                : t('Unavailable model')}
            </option>
          )}
        </select>
        <small>{t('Trained models require one matching battlefield.')}</small>
      </label>
    );
  };
  const importConfig = async (file: File) => {
    setImportingConfig(true);
    try {
      const imported = await readImportedTournamentConfig(file, profiles, models);
      if (imported.config.mode !== 'fixed_roles') {
        notify.error(
          t('This is a round-robin tournament. Import it from the tournament setup instead.'),
        );
        return;
      }
      const [attackers, defenders] = imported.config.players;
      const settings: Config = { ...attackers.config };
      delete settings.model_id;
      setRuleset(imported.config.rulesets[0]);
      setAttackerId(String(attackers.config.model_id ?? ''));
      setDefenderId(String(defenders.config.model_id ?? ''));
      setShared(settings);
      setGames(imported.config.games);
      setMaxPlies(imported.config.max_plies);
      setSeed(imported.config.seed);
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
          mode: 'fixed_roles',
          rulesets: [ruleset],
          games,
          max_plies: maxPlies,
          seed,
          players: [
            { config: { ...shared, model_id: attackerId } },
            { config: { ...shared, model_id: defenderId } },
          ],
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
      aria-label={t('Fixed-role comparison')}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form onSubmit={(event) => void submit(event)}>
        <div className="modal-top">
          <div>
            <h2>{t('Fixed-role comparison')}</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={t('Close fixed-role setup')}
            onClick={onClose}
            disabled={busy}
          >
            ×
          </button>
        </div>
        <p className="tournament-setup-copy">
          {t(
            'Compare two trained models with fixed sides. One model always attacks, the other always defends, and both share one search configuration.',
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
              'Import a portable fixed-role configuration, then review and edit it before starting.',
            )}
          </span>
        </div>
        <div className="name-suggestion-field">
          <label htmlFor="fixed-role-name">{t('Tournament name')}</label>
          <div className="name-input-group">
            <input
              id="fixed-role-name"
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
          <legend>{t('Battlefield')}</legend>
          <p className="subtle">
            {t(
              'Trained models belong to one ruleset, so a comparison uses exactly one battlefield.',
            )}
          </p>
          <div className="tournament-rulesets">
            {sortRulesets(rulesets).map((rule) => (
              <label className={ruleset === rule.id ? 'chosen' : ''} key={rule.id}>
                <input
                  type="radio"
                  name="fixed-role-battlefield"
                  aria-label={`${t(rule.name)} ${rule.size} × ${rule.size}`}
                  checked={ruleset === rule.id}
                  onChange={() => setRuleset(rule.id)}
                />
                <strong>{t(rule.name)}</strong>
                <small>
                  {rule.size} × {rule.size}
                </small>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="tournament-setup-opponents fixed-roles">
          <fieldset className="tournament-player">
            <legend>{t('Attackers')}</legend>
            <p className="subtle">{t('Plays the attacking side in every game.')}</p>
            {modelField('attackers', attackerId, setAttackerId)}
            {attacker && <ModelTrainingSummary model={attacker} compact />}
          </fieldset>
          <fieldset className="tournament-player">
            <legend>{t('Defenders')}</legend>
            <p className="subtle">{t('Plays the defending side in every game.')}</p>
            {modelField('defenders', defenderId, setDefenderId)}
            {defender && <ModelTrainingSummary model={defender} compact />}
          </fieldset>
        </div>
        <fieldset className="tournament-fields">
          <legend>{t('Shared search configuration')}</legend>
          <p className="subtle">
            {t('Both participants use these settings. Only the trained model differs.')}
          </p>
          <SearchWrapper
            value={wrapper}
            label={t('Shared search wrapper')}
            onChange={changeWrapper}
          />
          <div className="form-grid">
            {wrapper !== 'none' && field('time_limit', 'Time limit (seconds)', 0.01, 15, 0.01, 1)}
            {wrapper === 'mcts' && (
              <>
                {field('iterations', 'Search iterations', 1, 5000, 1, 80)}
                {field('exploration', 'Exploration', 0, 10, 0.1, 1.4)}
              </>
            )}
            {wrapper === 'alpha_beta' && field('depth', 'Search depth', 1, 5, 1, 2)}
            {(wrapper === 'mcts' || wrapper === 'none') &&
              field('temperature', 'Temperature', 0, 5, 0.1, 0)}
          </div>
          {wrapper === 'alpha_beta' && (
            <AlphaBetaSearchFields
              prefix={t('Shared')}
              modelBacked
              search={alphaBeta}
              onPreset={(preset) =>
                setShared((previous) => ({
                  ...previous,
                  search: effectiveSearch(
                    presetSearch(preset, searchFromConfig(previous) ?? DEFAULT_ALPHA_BETA_SEARCH),
                  ),
                }))
              }
              onField={(patch) =>
                setShared((previous) => ({
                  ...previous,
                  search: effectiveSearch({
                    ...(searchFromConfig(previous) ?? DEFAULT_ALPHA_BETA_SEARCH),
                    ...patch,
                  }),
                }))
              }
            />
          )}
        </fieldset>
        <div className="form-grid tournament-run-settings">
          <label>
            {t('Number of games')}
            <input
              type="number"
              aria-label={t('Number of games')}
              aria-describedby="fixed-role-games-help"
              min={1}
              max={100}
              step={1}
              required
              value={games}
              onChange={(event) => setGames(Number(event.target.value))}
            />
          </label>
          <label>
            {t('Move limit per game')}
            <input
              type="number"
              min={1}
              max={1000}
              required
              value={maxPlies}
              onChange={(event) => setMaxPlies(Number(event.target.value))}
            />
          </label>
          <label>
            {t('Tournament seed')}
            <input
              type="number"
              min={0}
              max={4294967295}
              required
              value={seed}
              onChange={(event) => setSeed(Number(event.target.value))}
            />
          </label>
          <div className="tournament-run-count">
            <strong>{games}</strong>
            <span>{t('games with fixed sides')}</span>
          </div>
        </div>
        <p id="fixed-role-games-help" className="tournament-scoring">
          {t(
            'Every scheduled game keeps the same sides, so an odd game count is valid. The tournament seed controls all game and move seeds. Unfinished games score no points.',
          )}
        </p>
        {!attackerCompatible && (
          <p className="form-error">
            {attackerId
              ? t('The attackers model is not trained for this battlefield.')
              : t('Select a trained model for the attackers.')}
          </p>
        )}
        {!defenderCompatible && (
          <p className="form-error">
            {defenderId
              ? t('The defenders model is not trained for this battlefield.')
              : t('Select a trained model for the defenders.')}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            {t('Cancel')}
          </button>
          <button className="button primary" disabled={busy || !valid}>
            {busy ? t('Starting…') : t('Start tournament')}
          </button>
        </div>
      </form>
    </dialog>
  );
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
      if (imported.config.mode === 'fixed_roles') {
        notify.error(
          t('This is a fixed-role comparison. Import it from the fixed-role setup instead.'),
        );
        return;
      }
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
