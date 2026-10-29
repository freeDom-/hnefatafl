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
  TimeControlMode,
} from './onlineTypes';
import { useOnlinePoll } from './useOnlinePoll';
import Modal from './Modal';
import Tabs, { type TabItem } from './Tabs';
import { durationParts, moveSeconds } from './moveDuration';
import { formatRemaining, remainingSeconds, RemainingTime, useNow } from './remainingTime';
import ActiveGameSwitcher, { type ActiveGameEntry } from './ActiveGameSwitcher';
import PlayPage from './PlayPage';
import GameLayout from './GameLayout';
import { useMediaQuery } from './mobilePlay';
import GameDetailsPanel from './GameDetailsPanel';
import GameResult from './GameResult';
import ArmiesPanel from './ArmiesPanel';
import HistoryPanel from './HistoryPanel';
import Battlefield, { BoardToolbar } from './Battlefield';
import { sideGlyph } from './sideGlyph';
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
function playerRemaining(game: OnlineGame, side: Side, now: number): number {
  return game.status === 'active' && game.position.turn === side
    ? remainingSeconds(game.deadline, now)
    : Math.ceil(game.remaining_seconds?.[side] ?? game.time_per_move);
}
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
      p.time_per_move <= 1209600 &&
      (p.time_control === undefined || ['per_move', 'total'].includes(p.time_control)) &&
      (p.increment_seconds === undefined ||
        (Number.isInteger(p.increment_seconds) &&
          p.increment_seconds >= 0 &&
          p.increment_seconds <= 1209600)) &&
      (p.rated === undefined || typeof p.rated === 'boolean')
    )
      return {
        request_id: p.request_id,
        ruleset: p.ruleset,
        side_selection: p.side_selection,
        mode: p.mode,
        time_per_move: p.time_per_move,
        time_control: p.time_control ?? 'per_move',
        increment_seconds: p.increment_seconds ?? 0,
        // Rated-only policy: a retained legacy intent is replayed as rated.
        rated: true,
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
  // A div, not a p: the shared game-details mobile layout hides direct p children.
  return value ? (
    <div className="online-notice" role="status">
      {t(value.text)} {value.detail && <span>{t(value.detail)}</span>}
    </div>
  ) : null;
}
function Admission({ availability }: { availability: Availability }) {
  const { t } = useI18n();
  const { account } = useAuth();
  return !availability.enabled ? (
    <p role="status">
      {t('New online games and joining are currently disabled. Existing seats remain available.')}
    </p>
  ) : !availability.eligible && account?.email_verified !== false ? (
    <p role="status">
      {t(
        'Creating and joining require an active, eligible, verified account. Existing seats remain available.',
      )}
    </p>
  ) : null;
}
function GameSummary({
  game,
  open,
  now,
}: {
  game: OnlineGame;
  open: (id: string) => void;
  now: number;
}) {
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
      <p>
        {game.status === 'active' && game.deadline ? (
          <>
            {t('Remaining')} <RemainingTime seconds={remainingSeconds(game.deadline, now)} />
          </>
        ) : (
          t(
            game.time_control === 'total'
              ? 'Total time per player: {duration}'
              : 'Time per move: {duration}',
            { duration: formatRemaining(game.time_per_move) },
          )
        )}
      </p>
      {game.time_control === 'total' && (
        <p>{t('Increment: {seconds} seconds', { seconds: game.increment_seconds ?? 0 })}</p>
      )}
      <button className="button primary" onClick={() => open(game.id)}>
        {t('Open game')}
      </button>
    </article>
  );
}
type LobbyTab = 'all' | 'mine' | 'my_turn';
const lobbyTabs: readonly TabItem[] = [
  { id: 'all', label: 'All games' },
  { id: 'mine', label: 'My games' },
  { id: 'my_turn', label: 'My turn' },
];
function initialLobbyTab(): LobbyTab {
  const params = new URLSearchParams(location.search);
  const section = params.get('section');
  if (section === 'my_turn') return 'my_turn';
  if (section === 'mine' || params.get('mine') === 'true') return 'mine';
  return 'all';
}
export function Lobby({
  accountId,
  open,
  embedded = false,
  creationRequested = 0,
  dialogOnly = false,
  onCloseCreation,
}: {
  accountId: string;
  open: (id: string) => void;
  embedded?: boolean;
  creationRequested?: number;
  dialogOnly?: boolean;
  onCloseCreation?: () => void;
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
    onCloseCreation?.();
  };
  useEffect(() => {
    if (creationRequested) {
      creationVisible.current = true;
      setCreateOpen(true);
    }
  }, [creationRequested]);
  const [tab, setTab] = useState<LobbyTab>(initialLobbyTab);
  const [offset, setOffset] = useState(0);
  const now = useNow();
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
  const [timeControl, setTimeControl] = useState<TimeControlMode>(
    intent?.time_control ?? 'per_move',
  );
  const [increment, setIncrement] = useState(String(intent?.increment_seconds ?? 0));
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
          `/online/games?section=${tab}&limit=${pageSize}&offset=${offset}`,
          undefined,
          undefined,
          signal,
        ),
      ]);
      return { availability, listing };
    },
    [tab, offset, pageSize],
  );
  const poll = useOnlinePoll(`lobby:${accountId}:${tab}:${offset}`, load);
  const action = useOnlineAction(poll);
  const available =
    poll.phase === 'ready' && poll.data?.availability.enabled && poll.data.availability.eligible;
  const create = (event: FormEvent) => {
    event.preventDefault();
    if (action.busy || !available) return;
    let seconds: number;
    try {
      seconds = intentRef.current?.time_per_move ?? moveSeconds(days, hours, minutes);
      if (
        !intentRef.current &&
        timeControl === 'total' &&
        (!/^\d+$/.test(increment) ||
          !Number.isSafeInteger(Number(increment)) ||
          Number(increment) > 1209600)
      )
        throw new Error('Enter an increment in whole seconds from 0 to 1209600.');
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
      time_control: timeControl,
      increment_seconds: timeControl === 'total' ? Number(increment) : 0,
      rated: true,
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
    <section
      className={dialogOnly ? 'creation-host' : 'panel dashboard-lobby'}
      aria-label={t('Online lobby')}
    >
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
      {!dialogOnly && poll.data && <Admission availability={poll.data.availability} />}
      <div className="online-lobby-layout lobby-discovery">
        {createOpen && (
          <Modal
            title={t('Create an online game')}
            onClose={closeCreation}
            initialFocus="select"
            className="online-create-modal"
          >
            {dialogOnly && poll.data && <Admission availability={poll.data.availability} />}
            <form onSubmit={create} className="new-game-form online-create">
              <fieldset
                className="form-grid online-create-fields"
                disabled={!!intent || action.busy || !available || !rulesets.length}
              >
                <legend className="sr-only">{t('Choose your online game')}</legend>
                <div className="form-section">
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
                </div>
                <div className="form-section">
                  <label>
                    {t('Time control')}
                    <select
                      value={timeControl}
                      onChange={(e) => {
                        const value = e.target.value as TimeControlMode;
                        setTimeControl(value);
                        if (value === 'per_move') setIncrement('0');
                      }}
                    >
                      <option value="per_move">{t('Per move')}</option>
                      <option value="total">{t('Total per player')}</option>
                    </select>
                  </label>
                  <fieldset className="move-duration">
                    <legend>
                      {t(
                        timeControl === 'total' ? 'Total time per player' : 'Time allowed per move',
                      )}
                    </legend>
                    <div className="form-grid form-grid-three">
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
                  </fieldset>
                  {timeControl === 'total' && (
                    <label>
                      {t('Increment per move (seconds)')}
                      <input
                        type="number"
                        min={0}
                        max={1209600}
                        step={1}
                        required
                        value={increment}
                        onChange={(e) => setIncrement(e.target.value)}
                      />
                    </label>
                  )}
                </div>
              </fieldset>
              {validation && <p role="alert">{t(validation)}</p>}
              {rulesError && <p role="alert">{t(rulesError)}</p>}
              {intent && intent.time_per_move % 60 !== 0 && (
                <p role="status">
                  {t('This retained request uses exactly {duration}. Retrying keeps that value.', {
                    duration: formatRemaining(intent.time_per_move),
                  })}
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
        {!dialogOnly && (
          <section className="online-games">
            <h2>{t('Online games')}</h2>
            <Tabs
              id="online-lobby"
              items={lobbyTabs}
              active={tab}
              onChange={(value) => {
                setTab(value as LobbyTab);
                setOffset(0);
              }}
              label="Online lobby games"
              className="online-tabs"
            />
            {poll.data?.listing.games.length === 0 && (
              <p>{t(tab === 'my_turn' ? 'No games await your move.' : 'No games on this page.')}</p>
            )}
            <div className="online-cards">
              {poll.data?.listing.games.map((game) => (
                <GameSummary key={game.id} game={game} open={open} now={now} />
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
        )}
      </div>
    </section>
  );
}
function RatingChanges({ game }: { game: OnlineGame }) {
  const { t } = useI18n();
  const applications = game.result?.rating;
  if (!applications?.length) return null;
  const nameFor = (accountId: string) =>
    game.participants.attackers?.account_id === accountId
      ? game.participants.attackers?.name
      : game.participants.defenders?.name;
  return (
    <div className="online-rating" role="status">
      <h3>{t('Rating changes')}</h3>
      {applications.map((application) => (
        <p key={application.account_id}>
          {nameFor(application.account_id) ?? t('Unassigned')}: {application.delta >= 0 ? '+' : ''}
          {application.delta.toFixed(1)} → {Math.round(application.rating_after)}
          {application.provisional ? ` · ${t('Provisional')}` : ''}
        </p>
      ))}
    </div>
  );
}
function GameRoom({
  id,
  accountId,
  openActive,
  lobby,
  reviewOnly = false,
}: {
  id: string;
  accountId: string;
  openActive: (entry: ActiveGameEntry) => void;
  lobby: () => void;
  reviewOnly?: boolean;
}) {
  const { t, locale } = useI18n();
  const compact = useMediaQuery('(max-width: 760px), (max-height: 480px) and (pointer: coarse)');
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
      return { game, availability };
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
  const viewReplay = async (seekTo = 0) => {
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
        setCursor(Math.max(0, Math.min(seekTo, value.states.length - 1)));
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
  const showReplayAt = (ply: number) => {
    if (replay && ply < replay.states.length) {
      setCursor(Math.max(0, Math.min(ply, replay.states.length - 1)));
      setSelected(null);
      setConfirm(false);
      return;
    }
    void viewReplay(ply);
  };
  useEffect(() => {
    if (reviewOnly && game) void viewReplay();
  }, [reviewOnly, game?.id]);
  const position = cursor !== null && replay ? replay.states[cursor] : game?.position;
  const now = useNow();
  const size = game ? Math.sqrt(game.position.board.length) : 0;
  const captured = (() => {
    if (!game) return { attackers: 0, defenders: 0 };
    const shown = position?.board ?? game.position.board;
    const lost = (piece: string) =>
      game.position.board.filter((p) => p === piece).length -
      shown.filter((p) => p === piece).length;
    return {
      attackers: game.captured.attackers + lost('A'),
      defenders: game.captured.defenders + lost('D') + lost('K'),
    };
  })();
  const remaining =
    game?.status === 'active' && game.deadline ? remainingSeconds(game.deadline, now) : null;
  const gameMetadata = game
    ? [
        { label: t('Game mode'), value: t(modeNames[game.match.mode]) },
        { label: t('Game {leg}', { leg: game.match.leg }), value: t('Rated') },
        {
          label: t(game.time_control === 'total' ? 'Total time per player' : 'Time per move'),
          value: formatRemaining(game.time_per_move),
        },
        ...(game.time_control === 'total'
          ? [
              {
                label: t('Increment per move (seconds)'),
                value: String(game.increment_seconds ?? 0),
              },
            ]
          : []),
        ...(game.time_control === 'total' && game.remaining_seconds
          ? (['attackers', 'defenders'] as const).map((side) => ({
              label: t(
                side === 'attackers' ? 'Attackers remaining time' : 'Defenders remaining time',
              ),
              value: <RemainingTime seconds={playerRemaining(game, side, now)} />,
            }))
          : []),
        {
          label: t('Remaining time'),
          value:
            remaining === null ? (
              '—'
            ) : remaining > 0 ? (
              <RemainingTime seconds={remaining} />
            ) : (
              t('Time expired. Finalizing result…')
            ),
        },
      ]
    : [];
  const gameDetails = game && (
    <GameDetailsPanel
      ruleset={t(game.ruleset)}
      size={size}
      rows={gameMetadata}
      desktopRows={[]}
      footer={
        <>
          {game.status === 'active' && game.deadline && (
            <p className="subtle">
              <time dateTime={game.deadline}>
                {new Intl.DateTimeFormat(locale, {
                  dateStyle: 'medium',
                  timeStyle: 'short',
                }).format(new Date(game.deadline))}
              </time>
            </p>
          )}
          {game.status === 'waiting' && (
            <div className="waiting-game-status" role="status">
              <h3>{t('Waiting for another player')}</h3>
              <dl>
                <div>
                  <dt>{t('Players')}</dt>
                  <dd>1 / 2</dd>
                </div>
                <div>
                  <dt>{t('Side assignment')}</dt>
                  <dd>
                    {t(
                      game.match.side_selection === 'random'
                        ? 'Random when a player joins'
                        : sideNames[game.creator_side ?? 'attackers'],
                    )}
                  </dd>
                </div>
              </dl>
              <p>{t('To change settings, cancel this game and create another.')}</p>
            </div>
          )}
          {reviewOnly && (
            <div>
              {t('Lifecycle')}: {t(statuses[game.status])}
            </div>
          )}
          {game.result ? (
            <GameResult
              winner={game.result.winner}
              reason={game.result.termination_reason}
              labels={sideNames}
            />
          ) : game.status !== 'waiting' ? (
            <div>{t(statuses[game.status])}</div>
          ) : null}
          <RatingChanges game={game} />
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
            <button className="button secondary" disabled={!ready} onClick={() => submit('cancel')}>
              {t('Cancel waiting game')}
            </button>
          )}
          {!compact && <Notice value={action.notice} />}
          <div className="subtle">
            {t('Disconnecting, closing the browser or logging out does not pause clocks.')}
          </div>
        </>
      }
    />
  );
  const armies = game && (
    <ArmiesPanel
      rows={(['attackers', 'defenders'] as Side[]).map((side) => ({
        side,
        symbol: sideGlyph(side),
        name: game.participants[side]?.name ?? t('Unassigned'),
        detail: `${t(sideNames[side])}${ownSide === side ? ` · ${t('You')}` : ''}${
          compact && game.time_control === 'total' && game.remaining_seconds
            ? ` · ${formatRemaining(playerRemaining(game, side, now))}`
            : ''
        }`,
        toMove: game.status === 'active' && game.position.turn === side,
      }))}
      captured={captured}
      label={t('Players and result')}
      metadata={gameMetadata}
    />
  );
  const switcher = !reviewOnly && (
    <ActiveGameSwitcher accountId={accountId} currentId={id} open={openActive} />
  );

  return (
    <PlayPage
      className="online-page"
      heading={t(reviewOnly ? 'Online game review' : 'Online game')}
    >
      {reviewOnly && (
        <button className="button" onClick={lobby}>
          {t('Back to dashboard')}
        </button>
      )}
      {reviewOnly && <p>{t('Read-only review. Replay controls change only your view.')}</p>}
      {poll.phase === 'stale' && (
        <div role="alert" className="online-notice">
          <p>{t('Online service unavailable or stale. Retry to reconnect.')}</p>
          {poll.error && (
            <p>
              {t(errorMessage(poll.error).text)}
              {errorMessage(poll.error).detail ? ` ${errorMessage(poll.error).detail}` : ''}
            </p>
          )}
          <button className="button secondary" onClick={() => void poll.refresh()}>
            {t('Retry')}
          </button>
        </div>
      )}
      {game && position && (
        <>
          {!reviewOnly && <Admission availability={poll.data!.availability} />}
          <GameLayout
            key={id}
            switcher={switcher}
            desktop={{
              sidebar: (
                <>
                  {gameDetails}
                  {armies}
                </>
              ),
              moveCount: game.moves.length,
            }}
            mobile={
              game.status === 'waiting'
                ? undefined
                : {
                    title: t(game.ruleset),
                    subtitle: (
                      <>
                        {game.result ? (
                          <GameResult
                            winner={game.result.winner}
                            reason={game.result.termination_reason}
                            labels={sideNames}
                          />
                        ) : (
                          <span>{t(statuses[game.status])}</span>
                        )}{' '}
                        ·{' '}
                        {game.match.mode !== 'single' && (
                          <>
                            <span>{t('Game {leg}', { leg: game.match.leg })}</span> ·{' '}
                          </>
                        )}
                        {game.status === 'active' && remaining !== null ? (
                          remaining > 0 ? (
                            <RemainingTime seconds={remaining} />
                          ) : (
                            t('Time expired. Finalizing result…')
                          )
                        ) : (
                          t('Move') + ' ' + position.ply
                        )}
                      </>
                    ),
                    players: armies,
                    settings: gameDetails,
                    moveCount: game.moves.length,
                  }
            }
            sidebar={
              <>
                {gameDetails}
                {armies}
              </>
            }
            battlefield={
              <Battlefield
                board={
                  <Board
                    game={{
                      ...position,
                      size,
                      moves: cursor !== null && replay ? replay.game.moves : game.moves,
                      cursor: position.ply,
                      capturedKingSquare: game.terminal_king_square,
                    }}
                    selected={
                      selected?.version === game.version && cursor === null ? selected.square : null
                    }
                    onSelect={select}
                    readOnly={!mayAct}
                  />
                }
                toolbar={
                  <BoardToolbar
                    leading={
                      <>
                        {!reviewOnly && mayAct && !confirm && (
                          <button
                            className="button secondary small"
                            onClick={() => setConfirm(true)}
                          >
                            {t('Concede')}
                          </button>
                        )}
                        {confirm && mayAct && (
                          <>
                            <button
                              className="button secondary small"
                              ref={confirmButton}
                              onClick={() => submit('concede')}
                            >
                              {t('Confirm concession')}
                            </button>
                            <button
                              className="button secondary small"
                              onClick={() => setConfirm(false)}
                            >
                              {t('Keep playing')}
                            </button>
                          </>
                        )}
                      </>
                    }
                    help={t(
                      reviewOnly
                        ? 'Read-only review. Replay controls change only your view.'
                        : mayAct
                          ? 'Select your piece, then an empty destination. The server checks the move.'
                          : ownSide
                            ? 'Waiting for your turn or server confirmation.'
                            : 'You are viewing this game. Only the seated player whose turn it is may act.',
                    )}
                  />
                }
              >
                {compact && <Notice value={action.notice} />}
                {confirm && mayAct && (
                  <p role="status">
                    {t('Concede this game? This ends your game if the server accepts it.')}
                  </p>
                )}
                {cursor !== null && replay && game.position.ply > replay.game.position.ply && (
                  <p role="status">{t('New moves are available on the live board.')}</p>
                )}
                {replayError && <p role="alert">{t(replayError)}</p>}
                {action.busy && <p role="status">{t('Waiting for server confirmation…')}</p>}
              </Battlefield>
            }
            history={
              <HistoryPanel
                status={
                  cursor !== null &&
                  !reviewOnly && (
                    <div className="history-review-status">
                      <span>{t('Read-only history')}</span>
                      <button
                        className="button secondary small"
                        onClick={() => {
                          setCursor(null);
                          setSelected(null);
                        }}
                      >
                        {t('Return to live board')}
                      </button>
                    </div>
                  )
                }
                startAction={{
                  onClick: () => showReplayAt(0),
                  disabled: replayBusy || action.busy,
                }}
                count={game.moves.length}
                entries={game.moves.map((move, index) => ({
                  ply: move.ply,
                  notation: `${coordinate(move.source, size)} → ${coordinate(move.target, size)}`,
                  side: move.side,
                  current: cursor === index + 1,
                  future: cursor !== null && index + 1 > cursor,
                  onSeek: () => {
                    if (!reviewOnly && move.ply === game.position.ply) {
                      setCursor(null);
                      setSelected(null);
                    } else showReplayAt(index + 1);
                  },
                }))}
              />
            }
          />
        </>
      )}
    </PlayPage>
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
          openActive={(entry) =>
            navigate(entry.type === 'online' ? `/online/games/${entry.id}` : `/games/${entry.id}`)
          }
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
