import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useI18n } from './i18n';
import { leagueError, RULESET_LABELS } from './leaguePresentation';
import {
  forgetJoin,
  joinIntentPrefix,
  pendingJoins,
  rememberJoin,
  type JoinIntent,
} from './leagueQueueRequests';
import type {
  LeaguePolicy,
  LeagueQueueEntry,
  LeagueQueueStatus,
  LeagueRuleset,
} from './leagueTypes';
import { formatRemaining, RemainingTime, remainingSeconds } from './remainingTime';
import { surfaceLink } from './surfaceNavigation';
import { leagueApi, useLeagueRefresh } from './useLeagueRefresh';
import { useOnlinePoll } from './useOnlinePoll';

export type ServerSample = { serverTime: number; receivedAt: number };
export const sampleTime = (serverTime: string): ServerSample => ({
  serverTime: Date.parse(serverTime),
  receivedAt: performance.now(),
});
const sampledNow = (sample: ServerSample) =>
  sample.serverTime + Math.max(0, performance.now() - sample.receivedAt);

function useServerCountdown(sample: ServerSample, deadline: string, onElapsed: () => void) {
  const [now, setNow] = useState(() => sampledNow(sample));
  const elapsed = useRef<string | null>(null);
  useEffect(() => {
    let timer: number | undefined;
    const refresh = () => {
      const estimated = sampledNow(sample);
      setNow(estimated);
      if (remainingSeconds(deadline, estimated) === 0) {
        window.clearInterval(timer);
        if (elapsed.current !== deadline) {
          elapsed.current = deadline;
          onElapsed();
        }
      }
    };
    const visible = () => {
      window.clearInterval(timer);
      if (document.hidden) return;
      timer = window.setInterval(refresh, 1000);
      refresh();
    };
    visible();
    document.addEventListener('visibilitychange', visible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [deadline, onElapsed, sample]);
  return now;
}

/** Current season and queue timers use the same received server clock sample. */
export function LeagueCountdown({
  sample,
  deadline,
  onElapsed,
}: {
  sample: ServerSample;
  deadline: string;
  onElapsed: () => void;
}) {
  const now = useServerCountdown(sample, deadline, onElapsed);
  return <RemainingTime seconds={remainingSeconds(deadline, now)} />;
}

function QueueTiming({
  sample,
  entry,
  onElapsed,
}: {
  sample: ServerSample;
  entry: LeagueQueueEntry;
  onElapsed: () => void;
}) {
  const { t } = useI18n();
  const now = useServerCountdown(sample, entry.expires_at, onElapsed);
  const displayNow = Math.min(now, Date.parse(entry.expires_at));
  return (
    <p>
      {t('{value} waiting', {
        value: formatRemaining((displayNow - Date.parse(entry.joined_at)) / 1000),
      })}
      {' · '}
      <RemainingTime seconds={remainingSeconds(entry.expires_at, now)} />
    </p>
  );
}

type QueueSnapshot = { status: LeagueQueueStatus; clock: ServerSample; unresolved?: JoinIntent[] };
export const queueSnapshot = (status: LeagueQueueStatus): QueueSnapshot => ({
  status,
  clock: sampleTime(status.server_time),
});

const QUEUE_POLL_MS = 4000;
const GAME_POLL_MS = 8000;
const IDLE_POLL_MS = 60000;

export default function LeagueQueue({
  accountId,
  ruleset,
  initial,
  policy,
  currentSeasonNotice,
  onGameChanged,
}: {
  accountId: string;
  ruleset: LeagueRuleset;
  initial: QueueSnapshot;
  policy: LeaguePolicy;
  currentSeasonNotice?: ReactNode;
  onGameChanged: (active: boolean) => void;
}) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
  const [cadence, setCadence] = useState(IDLE_POLL_MS);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const mutation = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(() => pendingJoins(accountId));
  const channel = useRef<BroadcastChannel | null>(null);
  const lastGame = useRef<string | null>(initial.status.active_game?.game_id ?? null);
  const previousRuleset = useRef(ruleset);

  const load = useCallback(
    async (signal: AbortSignal) => {
      // A reply can only reconcile metadata that existed before this read.
      const unresolved = pendingJoins(accountId);
      const status = await leagueApi<LeagueQueueStatus>(
        accountId,
        '/leagues/queue',
        undefined,
        undefined,
        signal,
      );
      const snapshot = queueSnapshot(status);
      if (!signal.aborted) {
        snapshot.unresolved = [];
        for (const intent of unresolved) {
          if (
            intent.generation !== status.join_generation ||
            intent.requestId === status.join_generation
          )
            forgetJoin(accountId, intent.requestId);
          else snapshot.unresolved.push(intent);
        }
        const saved = pendingJoins(accountId);
        setPending(saved);
        if (unresolved.length && !saved.length) setFailure(null);
      }
      return snapshot;
    },
    [accountId],
  );
  const poll = useOnlinePoll(`league-queue:${accountId}`, load, {
    intervalMs: cadence,
    initialData: initial,
  });
  const { status, clock, unresolved } = poll.data ?? initial;
  // Mutation responses can describe an old request. The nested canonical state
  // is the only source for cancellation targets and playable official links.
  const active = status.active_game;
  const entry = status.queue?.status === 'queued' ? status.queue : null;
  const queueOutcome = status.queue?.status ?? status.status;
  const refresh = poll.refresh;
  const resume = poll.resume;
  const refreshQueue = useCallback(() => void refresh(), [refresh]);
  useLeagueRefresh(refreshQueue);

  useEffect(() => {
    setCadence(active ? GAME_POLL_MS : entry ? QUEUE_POLL_MS : IDLE_POLL_MS);
  }, [active, entry]);
  useEffect(() => {
    const current = active?.game_id ?? null;
    if (lastGame.current !== current) {
      lastGame.current = current;
      onGameChanged(current !== null);
    }
  }, [active?.game_id, onGameChanged]);
  useEffect(() => {
    const prefix = joinIntentPrefix(accountId);
    if (pendingJoins(accountId).length) refreshQueue();
    const connection =
      typeof window.BroadcastChannel === 'function'
        ? new window.BroadcastChannel(`${prefix}changes`)
        : null;
    channel.current = connection;
    const changed = () => {
      setPending(pendingJoins(accountId));
      refreshQueue();
    };
    const stored = (event: StorageEvent) => {
      if (event.key?.startsWith(prefix)) changed();
    };
    window.addEventListener('storage', stored);
    connection?.addEventListener('message', changed);
    return () => {
      window.removeEventListener('storage', stored);
      connection?.close();
      channel.current = null;
    };
  }, [accountId, refreshQueue]);
  // Route and account changes remove the whole queue scope. A new ruleset also
  // invalidates mutations for the previous selection; their writes can still
  // have committed, so reconcile a fresh canonical snapshot.
  useEffect(() => {
    setBusy(false);
    setFailure(null);
    if (previousRuleset.current !== ruleset) {
      previousRuleset.current = ruleset;
      if (pendingJoins(accountId).length) refreshQueue();
    }
    return () => {
      const previous = mutation.current;
      if (previous) {
        mutation.current = null;
        previous.abort();
        void resume();
      }
    };
  }, [accountId, refreshQueue, resume, ruleset]);

  const recoverable = unresolved?.find((intent) =>
    pending.some((saved) => saved.requestId === intent.requestId),
  );

  async function write(kind: 'join' | 'retry' | 'discard' | 'cancel') {
    if (mutation.current) return;
    if (kind === 'join') {
      const saved = pendingJoins(accountId);
      if (saved.length) {
        setPending(saved);
        refreshQueue();
        return;
      }
    }
    const intent =
      kind === 'retry' || kind === 'discard'
        ? recoverable
        : kind === 'join'
          ? { requestId: crypto.randomUUID(), ruleset, generation: status.join_generation }
          : null;
    const requestId = kind === 'cancel' ? entry?.request_id : intent?.requestId;
    if (!requestId) return;
    if (kind === 'join' && intent) rememberJoin(accountId, intent);
    const controller = new AbortController();
    mutation.current = controller;
    poll.hold();
    setBusy(true);
    setFailure(null);
    try {
      const next = await leagueApi<LeagueQueueStatus>(
        accountId,
        kind === 'discard'
          ? '/leagues/queue/discard'
          : intent
            ? '/leagues/queue'
            : '/leagues/queue/cancel',
        intent
          ? {
              ruleset: intent.ruleset,
              request_id: requestId,
              expected_generation: intent.generation,
            }
          : { request_id: requestId },
        'POST',
        AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
      );
      if (controller.signal.aborted) return;
      if (intent) forgetJoin(accountId, requestId);
      poll.accept(queueSnapshot(next));
      channel.current?.postMessage('queue-changed');
    } catch (error) {
      if (controller.signal.aborted) return;
      // Another tab may have submitted the same UUID. Reconcile it by reading
      // the generation even if this particular attempt was rejected.
      setFailure(error);
      channel.current?.postMessage('queue-changed');
    } finally {
      if (mutation.current === controller) {
        mutation.current = null;
        setPending(pendingJoins(accountId));
        setBusy(false);
        void resume();
      }
    }
  }

  const notice = active
    ? 'League game ready'
    : entry
      ? 'Waiting for an opponent'
      : queueOutcome === 'expired'
        ? 'Your queue entry expired.'
        : queueOutcome === 'cancelled'
          ? 'Your queue entry was cancelled.'
          : 'You are not in the league queue.';
  const unavailable = !status.eligible
    ? 'Matchmaking requires an active, verified account with permission to play.'
    : status.paused
      ? 'League matchmaking is paused.'
      : !status.enabled
        ? 'Online matchmaking is currently unavailable.'
        : !status.clocks_enabled
          ? 'Online clocks are paused. League matchmaking will resume when clocks are enabled.'
          : null;
  return (
    <section className="arena-surface league-panel" aria-labelledby="league-queue-heading">
      <h2 id="league-queue-heading">{t('Matchmaking')}</h2>
      {currentSeasonNotice}
      <p role="status">{t(notice)}</p>
      {unavailable && <p>{t(unavailable)}</p>}
      <p>
        {t(
          'Each player has {minutes} minutes total and gains {increment} seconds after each move.',
          {
            minutes: number.format(policy.clock_seconds / 60),
            increment: number.format(policy.increment_seconds),
          },
        )}
      </p>
      <p>
        {t(
          'Both players must be within each other’s rating window. It starts at ±{initial} Elo and widens by {step} every {interval} seconds, up to ±{maximum} Elo.',
          {
            initial: number.format(policy.rating_window.initial),
            step: number.format(policy.rating_window.step),
            interval: number.format(policy.rating_window.interval_seconds),
            maximum: number.format(policy.rating_window.maximum),
          },
        )}
      </p>
      {entry && (
        <>
          <p>
            {t('{ruleset} queue · ±{window} Elo', {
              ruleset: t(RULESET_LABELS[entry.ruleset]),
              window: number.format(entry.rating_window),
            })}
          </p>
          <QueueTiming sample={clock} entry={entry} onElapsed={refreshQueue} />
        </>
      )}
      {!active && !entry && (
        <p>
          {t('Queue entries expire after {minutes} minutes or at the end of the season.', {
            minutes: number.format(policy.queue_seconds / 60),
          })}
        </p>
      )}
      {pending.length > 0 && !busy && (
        <p role="status">
          {recoverable
            ? t('A previous join is unconfirmed for {ruleset}.', {
                ruleset: t(RULESET_LABELS[recoverable.ruleset]),
              })
            : t('Checking previous join requests…')}
        </p>
      )}
      {failure != null && (
        <p className="form-error" role="alert">
          {leagueError(failure, t)}
        </p>
      )}
      {poll.error && (
        <div className="form-error" role="alert">
          <p>{t('Queue status may be out of date.')}</p>
          <p>{leagueError(poll.error, t)}</p>
          <button className="button secondary" type="button" onClick={refreshQueue}>
            {t('Refresh queue status')}
          </button>
        </div>
      )}
      <div className="page-toolbar">
        {active ? (
          <a
            className="button primary"
            href={`/online/games/${encodeURIComponent(active.game_id)}`}
            onClick={surfaceLink}
          >
            {t('Open league game')}
          </a>
        ) : entry ? (
          <button
            className="button secondary"
            type="button"
            disabled={busy}
            onClick={() => void write('cancel')}
          >
            {t(busy ? 'Cancelling…' : 'Cancel queue')}
          </button>
        ) : (
          <button
            className="button primary"
            type="button"
            disabled={busy || !!unavailable || poll.phase === 'stale' || pending.length > 0}
            onClick={() => void write('join')}
          >
            {t(busy ? 'Joining…' : 'Join queue')}
          </button>
        )}
        {!active && !entry && recoverable && (
          <button
            className="button secondary"
            type="button"
            disabled={busy || !!unavailable || poll.phase === 'stale'}
            onClick={() => void write('retry')}
          >
            {t('Retry previous join')}
          </button>
        )}
        {recoverable && (
          <button
            className="button secondary"
            type="button"
            disabled={busy || poll.phase === 'stale'}
            onClick={() => void write('discard')}
          >
            {t('Discard pending join')}
          </button>
        )}
      </div>
    </section>
  );
}
