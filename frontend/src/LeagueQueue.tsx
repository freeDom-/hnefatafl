import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { useI18n } from './i18n';
import { leagueError, RULESET_LABELS } from './leaguePresentation';
import type {
  LeaguePolicy,
  LeagueQueueEntry,
  LeagueQueueStatus,
  LeagueRuleset,
} from './leagueTypes';
import { formatRemaining, RemainingTime, remainingSeconds } from './remainingTime';
import { surfaceLink } from './surfaceNavigation';
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

type QueueSnapshot = { status: LeagueQueueStatus; clock: ServerSample };
export const queueSnapshot = (status: LeagueQueueStatus): QueueSnapshot => ({
  status,
  clock: sampleTime(status.server_time),
});

const QUEUE_POLL_MS = 4000;
const GAME_POLL_MS = 8000;
const CHANNEL = 'hnefatafl-league-queue';

export default function LeagueQueue({
  accountId,
  ruleset,
  initial,
  policy,
  refreshSignal,
  onGameChanged,
}: {
  accountId: string;
  ruleset: LeagueRuleset;
  initial: QueueSnapshot;
  policy: LeaguePolicy;
  refreshSignal: number;
  onGameChanged: (active: boolean) => void;
}) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
  const [cadence, setCadence] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const mutation = useRef<AbortController | null>(null);
  const retries = useRef(new Map<LeagueRuleset, string>());
  const channel = useRef<BroadcastChannel | null>(null);
  const lastGame = useRef<string | null>(initial.status.active_game?.game_id ?? null);
  const lastRefreshSignal = useRef(refreshSignal);

  const load = useCallback(
    async (signal: AbortSignal) =>
      queueSnapshot(await api<LeagueQueueStatus>('/leagues/queue', undefined, undefined, signal)),
    [],
  );
  const poll = useOnlinePoll(`league-queue:${accountId}`, load, {
    intervalMs: cadence,
    initialData: initial,
  });
  const { status, clock } = poll.data ?? initial;
  // Mutation responses can describe an old request. The nested canonical state
  // is the only source for cancellation targets and playable official links.
  const active = status.active_game;
  const entry = status.queue?.status === 'queued' ? status.queue : null;
  const queueOutcome = status.queue?.status ?? status.status;
  const refresh = poll.refresh;
  const refreshQueue = useCallback(() => void refresh(), [refresh]);

  useEffect(() => {
    setCadence(active ? GAME_POLL_MS : entry ? QUEUE_POLL_MS : 0);
  }, [active, entry]);
  useEffect(() => {
    const current = active?.game_id ?? null;
    if (lastGame.current !== current) {
      lastGame.current = current;
      onGameChanged(current !== null);
    }
  }, [active?.game_id, onGameChanged]);
  useEffect(() => {
    if (lastRefreshSignal.current !== refreshSignal) {
      lastRefreshSignal.current = refreshSignal;
      refreshQueue();
    }
  }, [refreshQueue, refreshSignal]);
  useEffect(() => {
    if (typeof window.BroadcastChannel !== 'function') return;
    const connection = new window.BroadcastChannel(CHANNEL);
    channel.current = connection;
    const changed = () => refreshQueue();
    connection.addEventListener('message', changed);
    return () => {
      connection.removeEventListener('message', changed);
      connection.close();
      channel.current = null;
    };
  }, [refreshQueue]);
  // Route and account changes remove the whole queue scope. A new ruleset also
  // invalidates mutations for the previous selection; their writes can still
  // have committed, so reconcile a fresh canonical snapshot.
  useEffect(() => {
    setBusy(false);
    setFailure(null);
    return () => {
      const previous = mutation.current;
      if (previous) {
        mutation.current = null;
        previous.abort();
        refreshQueue();
      }
    };
  }, [refreshQueue, ruleset]);

  async function write(kind: 'join' | 'cancel') {
    if (mutation.current || (kind === 'cancel' && !entry)) return;
    const requestId =
      kind === 'cancel' ? entry!.request_id : (retries.current.get(ruleset) ?? crypto.randomUUID());
    if (kind === 'join') retries.current.set(ruleset, requestId);
    const controller = new AbortController();
    mutation.current = controller;
    setBusy(true);
    setFailure(null);
    try {
      const next = await api<LeagueQueueStatus>(
        kind === 'join' ? '/leagues/queue' : '/leagues/queue/cancel',
        kind === 'join' ? { ruleset, request_id: requestId } : { request_id: requestId },
        'POST',
        AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
      );
      if (controller.signal.aborted) return;
      if (kind === 'join') retries.current.delete(ruleset);
      poll.accept(queueSnapshot(next));
      channel.current?.postMessage('queue-changed');
    } catch (error) {
      if (controller.signal.aborted) return;
      // An HTTP rejection is a known result. A transport failure may have
      // happened after commit and retains the original id for an explicit retry.
      if (kind === 'join' && error instanceof ApiError && error.status < 500)
        retries.current.delete(ruleset);
      setFailure(error);
      refreshQueue();
      channel.current?.postMessage('queue-changed');
    } finally {
      if (mutation.current === controller) {
        mutation.current = null;
        setBusy(false);
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
      {failure != null && (
        <p className="form-error" role="alert">
          {leagueError(failure, t)}
        </p>
      )}
      {poll.error && (
        <div className="form-error" role="alert">
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
            disabled={busy || !!unavailable}
            onClick={() => void write('join')}
          >
            {t(busy ? 'Joining…' : 'Join queue')}
          </button>
        )}
      </div>
    </section>
  );
}
