import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from './api';
import { useAuth } from './Auth';
import Board, { coordinate } from './Board';
import { useI18n } from './i18n';
import type { Ruleset, Side } from './types';
import type {
  Availability,
  CreationIntent,
  MatchMode,
  OnlineGame,
  OnlineListing,
  OnlineReplay,
  OnlineTransition,
  SideSelection,
} from './onlineTypes';
import { useOnlinePoll } from './useOnlinePoll';
import ReplayControls from './ReplayControls';
import Modal from './Modal';
import { durationParts, moveSeconds } from './moveDuration';
import './online.css';

export const modeNames: Record<MatchMode, string> = {
  single: 'Single game',
  simultaneous: 'Two-game match — simultaneous',
  sequential: 'Two-game match — sequential',
};
const transitionLabels = {
  created: 'Game created.',
  join: 'Player joined.',
  cancel: 'Waiting game cancelled.',
  move: 'Move accepted.',
  concede: 'Concession accepted.',
  timeout: 'Deadline finalized.',
  none: 'No state change.',
  void: 'Game voided.',
};
const sideNames = { attackers: 'Attackers', defenders: 'Defenders', random: 'Random at join' };
const statuses = {
  waiting: 'Waiting',
  active: 'Active',
  completed: 'Completed',
  cancelled: 'Cancelled',
  voided: 'Voided',
};
const INTENT_KEY = 'hnefatafl-online-intent';
function restoreIntent(accountId: string): CreationIntent | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(INTENT_KEY) ?? 'null');
    const p = saved?.payload;
    if (
      saved?.account_id === accountId &&
      p &&
      /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(p.request_id) &&
      ['brandubh', 'tablut', 'copenhagen'].includes(p.ruleset) &&
      Object.hasOwn(sideNames, p.side_selection) &&
      Object.hasOwn(modeNames, p.mode) &&
      Number.isInteger(p.time_per_move) &&
      p.time_per_move >= 60 &&
      p.time_per_move <= 1209600
    )
      return {
        request_id: p.request_id,
        ruleset: p.ruleset,
        side_selection: p.side_selection,
        mode: p.mode,
        time_per_move: p.time_per_move,
      };
  } catch {
    /* Recovery still works through My games without browser storage. */
  }
  return null;
}
function rememberIntent(accountId: string, payload: CreationIntent | null) {
  try {
    if (payload)
      sessionStorage.setItem(INTENT_KEY, JSON.stringify({ account_id: accountId, payload }));
    else sessionStorage.removeItem(INTENT_KEY);
  } catch {
    /* Keep the in-memory intent. */
  }
}
function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    const prefix: Record<number, string> = {
      401: 'Your session ended. Please log in again.',
      403: 'The server denied this action. Check eligibility, your seat and whose turn it is.',
      409: 'State changed or adjudication is paused. Reloaded without repeating your action.',
      422: 'Check the request values.',
      429: 'Creation limit reached. Cancel a waiting game or wait before deliberately retrying.',
      503: 'Online service unavailable. The last confirmed state is retained.',
    };
    return {
      text:
        prefix[error.status] ?? 'Request failed. Review the refreshed state before trying again.',
      detail: error.message,
    };
  }
  return {
    text: 'The outcome is uncertain. Review the refreshed state before trying again.',
    detail: '',
  };
}
/** Mutations pause reads, never retry themselves, then reload the canonical state. */
function useOnlineAction<T>(poll: ReturnType<typeof useOnlinePoll<T>>) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; detail: string } | null>(null);
  const lock = useRef(false);
  const alive = useRef(true);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      request.current?.abort();
    };
  }, []);
  const run = async (
    path: string,
    body: unknown,
    accepted: (response: OnlineTransition) => void,
  ) => {
    if (lock.current || !alive.current) return;
    lock.current = true;
    setBusy(true);
    setNotice(null);
    poll.hold();
    const controller = new AbortController();
    request.current = controller;
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await api<OnlineTransition>(path, body, undefined, controller.signal);
      if (!alive.current) return;
      accepted(response);
      setNotice({
        text: response.action_accepted
          ? 'Action confirmed by the server.'
          : response.transition === 'timeout'
            ? 'The server finalized the deadline. Your requested action was not accepted.'
            : 'The server did not accept the requested action. Review the refreshed state.',
        detail: transitionLabels[response.transition],
      });
    } catch (error) {
      if (alive.current) setNotice(errorMessage(error));
    } finally {
      clearTimeout(timer);
      if (alive.current) {
        await poll.resume();
        if (alive.current) setBusy(false);
      }
      lock.current = false;
    }
  };
  return { busy, notice, run };
}
function Notice({ value }: { value: { text: string; detail: string } | null }) {
  const { t } = useI18n();
  return value ? (
    <p className="online-notice" role="status">
      {t(value.text)} {value.detail && <span>{t(value.detail)}</span>}
    </p>
  ) : null;
}
function Health({
  phase,
  error,
  refresh,
}: {
  phase: string;
  error: Error | null;
  refresh: () => unknown;
}) {
  const { t } = useI18n();
  return (
    <div className="online-health">
      <span role="status">
        {t(
          phase === 'loading'
            ? 'Loading online games…'
            : phase === 'stale'
              ? 'Stale state. Reconnecting…'
              : 'Connected · refreshes automatically',
        )}
      </span>
      <button className="button secondary" onClick={() => void refresh()}>
        {t('Refresh')}
      </button>
      {error && <Notice value={errorMessage(error)} />}
    </div>
  );
}
function Admission({ availability }: { availability: Availability }) {
  const { t } = useI18n();
  return !availability.enabled ? (
    <p role="status">
      {t('New online games and joining are currently disabled. Existing seats remain available.')}
    </p>
  ) : !availability.eligible ? (
    <p role="status">
      {t(
        'Creating and joining require an active, eligible, verified account. Existing seats remain available.',
      )}
    </p>
  ) : null;
}
function GameSummary({ game, open }: { game: OnlineGame; open: (id: string) => void }) {
  const { t } = useI18n();
  return (
    <article className="online-card" data-game-id={game.id}>
      <h3>
        {t(game.ruleset)} · {t(statuses[game.status])}
      </h3>
      <p>
        {t(modeNames[game.match.mode])} · {t('Game {leg}', { leg: game.match.leg })}
      </p>
      <p>
        {t('Attackers')}: {game.participants.attackers?.name ?? t('Unassigned')}
        {' · '}
        {t('Defenders')}: {game.participants.defenders?.name ?? t('Unassigned')}
      </p>
      <p>{t('Time per move: {seconds} seconds', { seconds: game.time_per_move })}</p>
      <button className="button" onClick={() => open(game.id)}>
        {t('Open game')}
      </button>
    </article>
  );
}
export function Lobby({
  accountId,
  open,
  embedded = false,
  creationRequested = 0,
}: {
  accountId: string;
  open: (id: string) => void;
  embedded?: boolean;
  creationRequested?: number;
}) {
  const { t } = useI18n();
  const pageSize = embedded ? 10 : 25;
  const [createOpen, setCreateOpen] = useState(
    !embedded || new URLSearchParams(location.search).get('setup') === 'online',
  );
  const creationVisible = useRef(createOpen);
  const closeCreation = () => {
    creationVisible.current = false;
    setCreateOpen(false);
  };
  useEffect(() => {
    if (creationRequested) {
      creationVisible.current = true;
      setCreateOpen(true);
    }
  }, [creationRequested]);
  const [mine, setMine] = useState(new URLSearchParams(location.search).get('mine') === 'true');
  const [offset, setOffset] = useState(0);
  const [intent, setIntent] = useState<CreationIntent | null>(() => restoreIntent(accountId));
  const intentRef = useRef(intent);
  const [rulesets, setRulesets] = useState<Ruleset[]>([]);
  const [rulesError, setRulesError] = useState('');
  const [ruleset, setRuleset] = useState(
    intent?.ruleset ??
      (['brandubh', 'tablut', 'copenhagen'].includes(
        new URLSearchParams(location.search).get('ruleset') ?? '',
      )
        ? new URLSearchParams(location.search).get('ruleset')!
        : 'brandubh'),
  );
  const [side, setSide] = useState<SideSelection>(intent?.side_selection ?? 'random');
  const [mode, setMode] = useState<MatchMode>(intent?.mode ?? 'single');
  const [parts] = useState(() => durationParts(intent?.time_per_move ?? 259200));
  const [days, setDays] = useState(parts.days);
  const [hours, setHours] = useState(parts.hours);
  const [minutes, setMinutes] = useState(parts.minutes);
  const [validation, setValidation] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    const timeout = setTimeout(() => controller.abort(), 10000);
    api<Ruleset[]>('/rulesets', undefined, undefined, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setRulesets(value);
      })
      .catch(() => {
        if (alive) setRulesError('Rulesets unavailable. Refresh the page to recover.');
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      alive = false;
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);
  const load = useCallback(
    async (signal: AbortSignal) => {
      const [availability, listing] = await Promise.all([
        api<Availability>('/online/availability', undefined, undefined, signal),
        api<OnlineListing>(
          `/online/games?limit=${pageSize}&offset=${offset}&mine=${mine}`,
          undefined,
          undefined,
          signal,
        ),
      ]);
      return { availability, listing };
    },
    [mine, offset, pageSize],
  );
  const poll = useOnlinePoll(`lobby:${accountId}:${mine}:${offset}`, load);
  const action = useOnlineAction(poll);
  const available =
    poll.phase === 'ready' && poll.data?.availability.enabled && poll.data.availability.eligible;
  const create = (event: FormEvent) => {
    event.preventDefault();
    if (action.busy || !available) return;
    let seconds: number;
    try {
      seconds = intentRef.current?.time_per_move ?? moveSeconds(days, hours, minutes);
    } catch (error) {
      setValidation((error as Error).message);
      return;
    }
    setValidation('');
    const payload = intentRef.current ?? {
      request_id: crypto.randomUUID(),
      ruleset,
      side_selection: side,
      mode,
      time_per_move: seconds,
    };
    intentRef.current = payload;
    setIntent(payload);
    rememberIntent(accountId, payload);
    void action.run('/online/games', payload, (response) => {
      if (response.action_accepted && response.transition === 'created') {
        rememberIntent(accountId, null);
        intentRef.current = null;
        setIntent(null);
        if (creationVisible.current) open(response.game.id);
      }
    });
  };
  return (
    <section className="panel dashboard-lobby" aria-label={t('Online lobby')}>
      <h2>{t('Online lobby')}</h2>
      {poll.phase === 'loading' && <p role="status">{t('Loading online games…')}</p>}
      {poll.phase === 'stale' && (
        <div role="alert" className="online-notice">
          <p>{t('Lobby data is stale. Reconnecting…')}</p>
          <button className="button secondary" onClick={() => void poll.refresh()}>
            {t('Retry connection')}
          </button>
        </div>
      )}
      {!createOpen && <Notice value={action.notice} />}
      {intent && !createOpen && (
        <button
          className="button secondary"
          onClick={() => {
            creationVisible.current = true;
            setCreateOpen(true);
          }}
        >
          {t('Review retained creation request')}
        </button>
      )}
      {poll.data && <Admission availability={poll.data.availability} />}
      <div className="online-lobby-layout lobby-discovery">
        {createOpen && (
          <Modal title={t('Create an online game')} onClose={closeCreation} initialFocus="select">
            <form onSubmit={create} className="new-game-form online-create">
              <fieldset disabled={!!intent || action.busy || !available || !rulesets.length}>
                <legend>{t('Choose your online game')}</legend>
                <label>
                  {t('Ruleset')}
                  <select value={ruleset} onChange={(e) => setRuleset(e.target.value)}>
                    {rulesets.map((r) => (
                      <option value={r.id} key={r.id}>
                        {t(r.name)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {t('Creator side')}
                  <select value={side} onChange={(e) => setSide(e.target.value as SideSelection)}>
                    {Object.entries(sideNames).map(([value, label]) => (
                      <option value={value} key={value}>
                        {t(label)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {t('Match mode')}
                  <select value={mode} onChange={(e) => setMode(e.target.value as MatchMode)}>
                    {Object.entries(modeNames).map(([value, label]) => (
                      <option value={value} key={value}>
                        {t(label)}
                      </option>
                    ))}
                  </select>
                </label>
                <fieldset className="move-duration">
                  <legend>{t('Time allowed per move')}</legend>
                  <div className="form-grid">
                    {(
                      [
                        ['Days', days, setDays, 14],
                        ['Hours', hours, setHours, 23],
                        ['Minutes', minutes, setMinutes, 59],
                      ] as const
                    ).map(([label, value, change, max]) => (
                      <label key={label}>
                        {t(label)}
                        <input
                          type="number"
                          min={0}
                          max={max}
                          step={1}
                          required
                          value={value}
                          onChange={(event) => change(event.target.value)}
                        />
                      </label>
                    ))}
                  </div>
                  <p>
                    {t(
                      'At least 1 minute, at most 14 days in total. Each accepted move renews the allowance.',
                    )}
                  </p>
                </fieldset>
              </fieldset>
              {validation && <p role="alert">{t(validation)}</p>}
              {rulesError && <p role="alert">{t(rulesError)}</p>}
              {intent && intent.time_per_move % 60 !== 0 && (
                <p role="status">
                  {t(
                    'This retained request uses exactly {seconds} seconds per move. Retrying keeps that value.',
                    { seconds: intent.time_per_move },
                  )}
                </p>
              )}
              {intent && (
                <p role="status">
                  {t(
                    'Request retained. Check My games before deliberately retrying the same request. A conflict can mean it already committed.',
                  )}
                </p>
              )}
              <div className="modal-actions">
                <button type="button" className="button secondary" onClick={closeCreation}>
                  {t('Cancel')}
                </button>
                <button
                  className="button primary"
                  disabled={action.busy || !available || !rulesets.length}
                >
                  {t(intent ? 'Retry retained request' : 'Create game')}
                </button>
              </div>
            </form>
            {intent && (
              <button
                className="button secondary"
                disabled={action.busy}
                onClick={() => {
                  rememberIntent(accountId, null);
                  intentRef.current = null;
                  setIntent(null);
                }}
              >
                {t('Discard request after checking My games')}
              </button>
            )}
            <Notice value={action.notice} />
          </Modal>
        )}
        <section className="online-games">
          <h2>{t(mine ? 'My games' : 'Visible games')}</h2>
          <div className="online-toolbar">
            <button
              className="button secondary"
              aria-pressed={!mine}
              disabled={action.busy}
              onClick={() => {
                setMine(false);
                setOffset(0);
              }}
            >
              {t('All games')}
            </button>
            <button
              className="button secondary"
              aria-pressed={mine}
              disabled={action.busy}
              onClick={() => {
                setMine(true);
                setOffset(0);
              }}
            >
              {t('My games')}
            </button>
          </div>
          {poll.data?.listing.games.length === 0 && <p>{t('No games on this page.')}</p>}
          <div className="online-cards">
            {poll.data?.listing.games.map((game) => (
              <GameSummary key={game.id} game={game} open={open} />
            ))}
          </div>
          <nav className="online-toolbar" aria-label={t('Game pages')}>
            <button
              className="button secondary"
              disabled={action.busy || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - pageSize))}
            >
              {t('Previous page')}
            </button>
            <span>{t('Page {page}', { page: offset / pageSize + 1 })}</span>
            <button
              className="button secondary"
              disabled={
                action.busy ||
                poll.phase !== 'ready' ||
                (poll.data?.listing.games.length ?? 0) < pageSize ||
                offset + pageSize > 100000
              }
              onClick={() => setOffset(offset + pageSize)}
            >
              {t('Next page')}
            </button>
          </nav>
        </section>
      </div>
    </section>
  );
}
function Deadline({ game }: { game: OnlineGame }) {
  const { t, locale } = useI18n();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  if (!game.deadline || game.status !== 'active') return null;
  const remaining = Math.ceil((Date.parse(game.deadline) - now) / 1000);
  return (
    <div className="online-deadline">
      <p>
        {t('Move deadline')}:{' '}
        <time dateTime={game.deadline}>
          {new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'long' }).format(
            new Date(game.deadline),
          )}
        </time>
      </p>
      <p role="status">
        {remaining <= 0
          ? t('Awaiting adjudication. The server still reports this game as Active.')
          : t('Approximately {seconds} seconds remaining', { seconds: remaining })}
      </p>
      <small>
        {t('Countdown uses your device clock. Only the server decides deadlines and results.')}
      </small>
    </div>
  );
}
function Result({ game }: { game: OnlineGame }) {
  const { t } = useI18n();
  if (!game.result) return <p>{t(statuses[game.status])}</p>;
  return (
    <p className="online-result" role="status">
      <strong>
        {game.result.winner === 'draw'
          ? t('Draw')
          : game.result.winner
            ? t('{side} win', { side: t(sideNames[game.result.winner]) })
            : t('No winner')}
      </strong>{' '}
      · {t(game.result.termination_reason.replaceAll('_', ' '))}
    </p>
  );
}
function GameRoom({
  id,
  accountId,
  open,
  lobby,
  reviewOnly = false,
}: {
  id: string;
  accountId: string;
  open: (id: string) => void;
  lobby: () => void;
  reviewOnly?: boolean;
}) {
  const { t } = useI18n();
  const { account } = useAuth();
  const prefix = reviewOnly ? '/personal/online' : '/online/games';
  const load = useCallback(
    async (signal: AbortSignal) => {
      const [game, availability] = await Promise.all([
        api<OnlineGame>(`${prefix}/${id}`, undefined, undefined, signal),
        reviewOnly
          ? Promise.resolve({ enabled: false, eligible: false } as Availability)
          : api<Availability>('/online/availability', undefined, undefined, signal),
      ]);
      const siblings = await Promise.all(
        game.match.games
          .filter((item) => item.id !== id)
          .map((item) => api<OnlineGame>(`${prefix}/${item.id}`, undefined, undefined, signal)),
      );
      return { game, availability, siblings };
    },
    [id, prefix, reviewOnly],
  );
  const poll = useOnlinePoll(`game:${accountId}:${id}`, load);
  const action = useOnlineAction(poll);
  const game = poll.data?.game;
  const [selected, setSelected] = useState<{ square: number; version: number } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [replay, setReplay] = useState<OnlineReplay | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState('');
  const replayRequest = useRef<AbortController | null>(null);
  const confirmButton = useRef<HTMLButtonElement | null>(null);
  useEffect(
    () => () => {
      replayRequest.current?.abort();
      replayRequest.current = null;
    },
    [],
  );
  useEffect(() => {
    setSelected(null);
    setConfirm(false);
  }, [game?.version]);
  useEffect(() => {
    if (confirm) confirmButton.current?.focus();
  }, [confirm]);
  const ownSide: Side | undefined = game
    ? (['attackers', 'defenders'] as Side[]).find(
        (side) => game.participants[side]?.account_id === accountId,
      )
    : undefined;
  const ready = poll.phase === 'ready' && !action.busy && !replayBusy;
  const mayAct =
    !!game &&
    !reviewOnly &&
    ready &&
    game.status === 'active' &&
    ownSide === game.position.turn &&
    account?.permissions.includes('play') &&
    cursor === null;
  const submit = (name: string, extra: Record<string, number> = {}) => {
    if (reviewOnly || !game || !ready) return;
    setSelected(null);
    setConfirm(false);
    void action.run(
      `/online/games/${id}/${name}`,
      { version: game.version, ...extra },
      (response) => {
        if (poll.data) poll.accept({ ...poll.data, game: response.game });
      },
    );
  };
  const select = (square: number) => {
    if (!game || !mayAct) return;
    if (square < 0) {
      setSelected(null);
      return;
    }
    const piece = game.position.board[square];
    if (selected && selected.version === game.version && piece === '.') {
      submit('moves', { source: selected.square, target: square });
      return;
    }
    const ownPiece = ownSide === 'attackers' ? piece === 'A' : piece === 'D' || piece === 'K';
    setSelected(ownPiece && selected?.square !== square ? { square, version: game.version } : null);
  };
  const viewReplay = async () => {
    if (replayRequest.current || action.busy) return;
    const controller = new AbortController();
    replayRequest.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    setReplayBusy(true);
    setReplayError('');
    try {
      const value = await api<OnlineReplay>(
        `${prefix}/${id}/replay`,
        undefined,
        undefined,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        if (!value.validated || !value.states.length) throw new Error('Replay unavailable.');
        setReplay(value);
        setCursor(0);
        setSelected(null);
        setConfirm(false);
      }
    } catch {
      if (replayRequest.current === controller) setReplayError('Replay unavailable.');
    } finally {
      clearTimeout(timeout);
      if (replayRequest.current === controller) {
        setReplayBusy(false);
        replayRequest.current = null;
      }
    }
  };
  useEffect(() => {
    if (reviewOnly && game) void viewReplay();
  }, [reviewOnly, game?.id]);
  const position = cursor !== null && replay ? replay.states[cursor] : game?.position;
  return (
    <section className="online-page">
      <button className="button" onClick={lobby}>
        {t(reviewOnly ? 'Back to dashboard' : 'Back to online lobby')}
      </button>
      <h1 tabIndex={-1}>{t(reviewOnly ? 'Online game review' : 'Online game')}</h1>
      {reviewOnly && <p>{t('Read-only review. Replay controls change only your view.')}</p>}
      <Health phase={poll.phase} error={poll.error} refresh={poll.refresh} />
      {game && position && (
        <>
          {!reviewOnly && <Admission availability={poll.data!.availability} />}
          <div className="online-toolbar">
            <span>{t(modeNames[game.match.mode])}</span>
            <span>
              {t('Creator side')}: {t(sideNames[game.creator_side ?? game.match.side_selection])}
            </span>
            <span>{t('Game {leg}', { leg: game.match.leg })}</span>
            <span>
              {t(game.ruleset)} · {t('Rules version')} {game.ruleset_version}
            </span>
            <span>{t('Time per move: {seconds} seconds', { seconds: game.time_per_move })}</span>
          </div>
          <nav className="online-toolbar" aria-label={t('Match games')}>
            {[game, ...poll.data!.siblings]
              .sort((a, b) => a.match.leg - b.match.leg)
              .map((item) => (
                <button
                  className="button secondary"
                  key={item.id}
                  aria-current={item.id === id ? 'page' : undefined}
                  onClick={() => open(item.id)}
                >
                  {t('Game {leg}', { leg: item.match.leg })} · {t(statuses[item.status])} ·{' '}
                  {t('Version {version}', { version: item.version })}
                  {item.result?.winner
                    ? ` · ${t(item.result.winner === 'draw' ? 'Draw' : sideNames[item.result.winner])}`
                    : ''}
                </button>
              ))}
          </nav>
          <div className="online-game-layout">
            <section className="battlefield">
              <div className="turn-banner" aria-live="polite">
                <div>
                  <strong>
                    {cursor !== null
                      ? t('Historical replay · read only')
                      : game.status === 'active'
                        ? t('{side} to move', { side: t(sideNames[game.position.turn]) })
                        : t(statuses[game.status])}
                  </strong>
                  <span>
                    {cursor !== null
                      ? t('Replay move {ply}', { ply: cursor })
                      : t('Version {version}', { version: game.version })}
                  </span>
                </div>
              </div>
              <Board
                game={{
                  ...position,
                  size: Math.sqrt(position.board.length),
                  moves: cursor !== null && replay ? replay.game.moves : game.moves,
                  cursor: position.ply,
                }}
                selected={
                  selected?.version === game.version && cursor === null ? selected.square : null
                }
                onSelect={select}
                readOnly={!mayAct}
              />
              <p>
                {t(
                  reviewOnly
                    ? 'Read-only review. Replay controls change only your view.'
                    : mayAct
                      ? 'Select your piece, then an empty destination. The server checks the move.'
                      : ownSide
                        ? 'Waiting for your turn or server confirmation.'
                        : 'You are viewing this game. Only the seated player whose turn it is may act.',
                )}
              </p>
              <div className="online-toolbar">
                <button
                  className="button secondary"
                  disabled={replayBusy || action.busy}
                  onClick={() => void viewReplay()}
                >
                  {t('View replay')}
                </button>
                {cursor !== null && replay && (
                  <>
                    <ReplayControls
                      cursor={cursor}
                      length={replay.states.length}
                      onChange={setCursor}
                    />
                    {!reviewOnly && (
                      <button
                        className="button secondary"
                        onClick={() => {
                          setCursor(null);
                          setSelected(null);
                        }}
                      >
                        {t('Return to live board')}
                      </button>
                    )}
                  </>
                )}
              </div>
              {cursor !== null && replay && game.position.ply > replay.game.position.ply && (
                <p role="status">{t('New moves are available on the live board.')}</p>
              )}
              {replayError && <p role="alert">{t(replayError)}</p>}
            </section>
            <aside className="panel online-details">
              <h2>{t('Players and result')}</h2>
              {(['attackers', 'defenders'] as Side[]).map((side) => (
                <p key={side}>
                  <strong>{t(sideNames[side])}</strong>:{' '}
                  {game.participants[side]?.name ?? t('Unassigned')}
                  {ownSide === side ? ` · ${t('You')}` : ''}
                </p>
              ))}
              {game.status === 'waiting' && (
                <p>
                  {t(
                    'Waiting for a second player. Random sides are assigned when they join. To change settings, cancel and create again.',
                  )}
                </p>
              )}
              {reviewOnly && (
                <p>
                  {t('Lifecycle')}: {t(statuses[game.status])}
                </p>
              )}
              <Result game={game} />
              <Deadline game={game} />
              {!reviewOnly && game.status === 'waiting' && game.creator_id !== accountId && (
                <button
                  className="button primary"
                  disabled={
                    !ready || !poll.data!.availability.enabled || !poll.data!.availability.eligible
                  }
                  onClick={() => submit('join')}
                >
                  {t('Join game')}
                </button>
              )}
              {!reviewOnly && game.status === 'waiting' && game.creator_id === accountId && (
                <button className="button" disabled={!ready} onClick={() => submit('cancel')}>
                  {t('Cancel waiting game')}
                </button>
              )}
              {mayAct && !confirm && (
                <button className="button" onClick={() => setConfirm(true)}>
                  {t('Concede')}
                </button>
              )}
              {confirm && mayAct && (
                <div role="group" aria-label={t('Confirm concession')}>
                  <p>{t('Concede this game? This ends your game if the server accepts it.')}</p>
                  <button
                    className="button secondary"
                    ref={confirmButton}
                    onClick={() => submit('concede')}
                  >
                    {t('Confirm concession')}
                  </button>
                  <button className="button secondary" onClick={() => setConfirm(false)}>
                    {t('Keep playing')}
                  </button>
                </div>
              )}
              {action.busy && <p role="status">{t('Waiting for server confirmation…')}</p>}
              <Notice value={action.notice} />
              <p>{t('Disconnecting, closing the browser or logging out does not pause clocks.')}</p>
              <h3>{t('Accepted moves')}</h3>
              <ol>
                {game.moves.map((move) => (
                  <li key={move.ply}>
                    {coordinate(move.source, Math.sqrt(game.position.board.length))} →{' '}
                    {coordinate(move.target, Math.sqrt(game.position.board.length))}
                  </li>
                ))}
              </ol>
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
export default function Online() {
  const { account } = useAuth();
  const [path, setPath] = useState(location.pathname);
  const heading = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const update = () => setPath(location.pathname);
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);
  const navigate = (next: string) => {
    if (next !== location.pathname) history.pushState(null, '', next);
    setPath(next);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };
  useEffect(() => {
    heading.current?.focus();
  }, [path]);
  if (!account) return null;
  const match = /^\/online\/games\/(online-[0-9a-f]{32})(\/review)?$/.exec(path);
  return (
    <div ref={heading} tabIndex={-1} className="online-root">
      {match ? (
        <GameRoom
          key={`${account.id}:${match[1]}:${!!match[2]}`}
          id={match[1]}
          accountId={account.id}
          reviewOnly={!!match[2]}
          open={(id) => navigate(`/online/games/${id}${match[2] ? '/review' : ''}`)}
          lobby={() => navigate('/dashboard')}
        />
      ) : (
        <Lobby
          key={account.id}
          accountId={account.id}
          open={(id) => navigate(`/online/games/${id}`)}
        />
      )}
    </div>
  );
}
