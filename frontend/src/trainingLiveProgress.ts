import { useEffect, useState } from 'react';
import type { Job } from './types';
import { activeJob, finite, jobProgress, type Values } from './trainingDashboardData';

/**
 * Elapsed time is published by the backend as a monotonic duration, not as a
 * wall-clock timestamp, so the browser never has to trust a worker's clock or
 * compare it with its own. The dashboard anchors each reported value at the
 * moment the response arrived and advances it with a local one-second ticker.
 * A worker clock may be unsynchronised or skewed without affecting the display.
 */

/** A phase without a progress event for this long is still normal work. */
export const LONG_OPERATION_SECONDS = 120;
/** Beyond this, monitoring reports the phase as potentially stalled. */
export const STALLED_SECONDS = 600;
/** No refreshed snapshot for this long means publication itself stopped. */
export const PUBLICATION_STALE_SECONDS = 20;
/** A remote supervisor reports every couple of seconds; silence means the link. */
export const SUPERVISOR_STALE_SECONDS = 20;
/**
 * The dashboard polls every 2.5 seconds, so silence for this long means the
 * browser cannot see fresh monitoring state at all. It is checked before the
 * worker's own freshness: without a recent authoritative observation, a stale
 * child or supervisor cannot be told apart from an unreachable server.
 */
export const MONITORING_STALE_SECONDS = 20;

export type HeartbeatState =
  | 'active'
  | 'long_operation'
  | 'stalled'
  | 'unresponsive'
  | 'disconnected'
  | 'monitoring_unavailable'
  | 'waiting'
  | 'terminal';

export interface LiveProgress {
  /** Seconds since the job started; fixed once the job is terminal. */
  elapsed?: number;
  /**
   * Seconds in the current iteration. Continuous across the phases of one
   * iteration and restarted by a genuinely new iteration.
   */
  iterationElapsed?: number;
  /** Seconds in the current phase; restarts when the phase changes. */
  phaseElapsed?: number;
  /** Seconds since meaningful computational progress. */
  progressAge?: number;
  /** Seconds since the last refreshed monitoring snapshot. */
  publicationAge?: number;
  /**
   * Seconds since the server last heard from a remote supervisor. A connected
   * supervisor can forward an unchanged child snapshot, so this is reported
   * separately from the child's own publication age.
   */
  supervisorAge?: number;
  /**
   * Seconds since the browser last received authoritative monitoring data. A
   * gap means the browser cannot see the worker, not that the worker stopped.
   */
  monitoringAge?: number;
  /** Undefined for jobs recorded before heartbeat fields existed. */
  heartbeat?: HeartbeatState;
}

/** Monotonic client clock, sampled once a second while it is needed. */
export function useMonotonicNow(active: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() =>
    typeof performance === 'undefined' ? 0 : performance.now(),
  );
  useEffect(() => {
    if (!active) return;
    setNow(performance.now());
    const timer = window.setInterval(() => setNow(performance.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs]);
  return now;
}

interface Anchor {
  key: string;
  jobId: string;
  phaseKey: string;
  /** Identity of the active iteration; a new one restarts only its own timer. */
  iterationKey: string;
  at: number;
  duration: number;
  iteration: number;
  phase: number;
  progressAge: number;
  /** Server-observed publication age at the anchor instant, when available. */
  publicationAge?: number;
  /** Server-observed supervisor age at the anchor instant, when available. */
  supervisorAge?: number;
  snapshotKey: string;
  changedAt: number;
}

function number(value: unknown): number | undefined {
  return finite(value) ? value : undefined;
}

/**
 * Live timings for one job. `observedAt` is the monotonic time of the last
 * successful monitoring observation, so the hook can tell a silent worker apart
 * from a browser that cannot reach the server; without it, the browser is
 * assumed to be up to date.
 */
export function useLiveProgress(job: Job, observedAt?: number): LiveProgress {
  const progress = jobProgress(job);
  const active = activeJob(job);
  const now = useMonotonicNow(active);
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const duration = number(progress.duration);
  const iterationElapsed = number(progress.iteration_elapsed);
  const phaseElapsed = number(progress.phase_elapsed);
  const progressAge = number(progress.last_progress_seconds);
  // The server measures how long ago the worker last published a snapshot with
  // its own clock, so a reload never mistakes an old snapshot for a fresh one
  // and an unsynchronised remote worker clock is never trusted.
  const reportedPublicationAge = number(job.publication_age_seconds);
  const reportedSupervisorAge = number(job.supervisor_age_seconds);
  const stage = typeof progress.stage === 'string' ? progress.stage : '';
  const phaseKey = `${stage}@${String(progress.phase_started_at ?? '')}@${String(progress.iteration ?? '')}`;
  // The active iteration changes as soon as a new one starts, so its timer
  // restarts without waiting for a completed game, while a phase change inside
  // one iteration leaves it running. `iteration_started_at` and the run identity
  // separate a genuine transition from an ordinary publication; the last
  // completed iteration is only the fallback for older payloads.
  const activeIteration = number(progress.active_iteration) ?? number(progress.iteration);
  const iterationKey = `${activeIteration ?? ''}@${String(progress.iteration_started_at ?? '')}@${String(progress.run ?? '')}`;
  // Every publication refreshes this key, including heartbeat-only refreshes.
  const snapshotKey = String(progress.updated_at ?? progress.duration ?? '');
  // The server's freshness ages and the browser's last observation take part in
  // the anchor key: a recovered worker or supervisor must re-anchor at its fresh
  // value immediately instead of waiting for the next publication.
  const anchorKey = [
    job.id,
    phaseKey,
    iterationKey,
    snapshotKey,
    duration,
    iterationElapsed,
    phaseElapsed,
    progressAge,
    active,
    reportedPublicationAge,
    reportedSupervisorAge,
    observedAt,
  ].join('|');
  // How long ago the browser last saw authoritative monitoring data. Without a
  // recent observation nothing about the worker can be confirmed.
  const monitoringAge =
    observedAt === undefined ? undefined : Math.max((now - observedAt) / 1000, 0);

  // Re-anchor during render so a new snapshot is reflected in the same commit
  // instead of one tick later.
  if (anchor === null || anchor.key !== anchorKey) {
    const at = typeof performance === 'undefined' ? 0 : performance.now();
    const sameJob = anchor !== null && anchor.jobId === job.id;
    const samePhase = sameJob && anchor.phaseKey === phaseKey;
    const sameIteration = sameJob && anchor.iterationKey === iterationKey;
    const changed = !sameJob || anchor.snapshotKey !== snapshotKey;
    // Everything already displayed stays displayed while the job runs: an
    // out-of-order or delayed snapshot must never undo elapsed time on screen.
    const shown = sameJob ? anchor.duration + (at - anchor.at) / 1000 : 0;
    const shownIteration = sameIteration ? anchor.iteration + (at - anchor.at) / 1000 : 0;
    const shownPhase = samePhase ? anchor.phase + (at - anchor.at) / 1000 : 0;
    setAnchor({
      key: anchorKey,
      jobId: job.id,
      phaseKey,
      iterationKey,
      at,
      // A terminal job adopts the authoritative backend duration even when the
      // browser had extrapolated a larger one; a running job keeps its maximum.
      duration: active ? Math.max(shown, duration ?? 0) : (duration ?? 0),
      // A new iteration restarts its timer; a phase change inside one iteration
      // keeps it running from where it was.
      iteration: active ? Math.max(shownIteration, iterationElapsed ?? 0) : (iterationElapsed ?? 0),
      phase: active ? Math.max(shownPhase, phaseElapsed ?? 0) : (phaseElapsed ?? 0),
      // Progress age legitimately resets when work resumes, so it follows the
      // newest report instead of the maximum.
      progressAge: progressAge ?? 0,
      // Both ages are durations measured by the server at this observation, so
      // the freshest observation is authoritative in either direction.
      publicationAge: reportedPublicationAge,
      supervisorAge: reportedSupervisorAge,
      snapshotKey,
      changedAt: changed ? at : (anchor?.changedAt ?? at),
    });
  }

  const current = anchor;
  const anchored = current !== null && current.jobId === job.id;
  const sinceAnchor = anchored ? Math.max((now - current.at) / 1000, 0) : 0;
  const monitored = typeof progress.updated_at === 'string' && progressAge !== undefined;
  const live: LiveProgress = {};
  const elapsedBase = anchored ? current.duration : (duration ?? 0);
  if (duration !== undefined) {
    live.elapsed = active ? elapsedBase + sinceAnchor : elapsedBase;
  }
  const iterationBase = anchored ? current.iteration : (iterationElapsed ?? 0);
  if (iterationElapsed !== undefined) {
    live.iterationElapsed = active ? iterationBase + sinceAnchor : iterationBase;
  }
  const phaseBase = anchored ? current.phase : (phaseElapsed ?? 0);
  if (phaseElapsed !== undefined) {
    live.phaseElapsed = active ? phaseBase + sinceAnchor : phaseBase;
  }
  if (monitored) {
    const ageBase = anchored ? current.progressAge : (progressAge ?? 0);
    live.progressAge = active ? ageBase + sinceAnchor : ageBase;
    const publicationBase = anchored ? current.publicationAge : reportedPublicationAge;
    const fallback = anchored ? Math.max((now - current.changedAt) / 1000, 0) : 0;
    live.publicationAge =
      publicationBase !== undefined
        ? active
          ? publicationBase + sinceAnchor
          : publicationBase
        : fallback;
    // The supervisor's age is a duration the server keeps growing while reports
    // stop, so it is advanced between observations. That extrapolation is only
    // sound while the browser is still receiving snapshots, so without a
    // monitoring signal only the confirmed value is used.
    const supervisorBase = anchored ? current.supervisorAge : reportedSupervisorAge;
    if (supervisorBase !== undefined) {
      live.supervisorAge =
        active && observedAt !== undefined ? supervisorBase + sinceAnchor : supervisorBase;
    }
    live.monitoringAge = monitoringAge;
    live.heartbeat = active
      ? classify(live.progressAge, live.publicationAge, live.supervisorAge, monitoringAge)
      : 'terminal';
  }
  if (active && ['pending', 'queued'].includes(job.status)) live.heartbeat = 'waiting';
  return live;
}

function classify(
  progressAge: number,
  publicationAge: number,
  supervisorAge?: number,
  monitoringAge?: number,
): HeartbeatState {
  // The browser's own observation is checked first. Without a recent
  // authoritative snapshot, a stale child or supervisor cannot be told apart
  // from a server the browser cannot reach, and a failed poll must never be
  // reported as a disconnected worker.
  if (monitoringAge !== undefined && monitoringAge > MONITORING_STALE_SECONDS)
    return 'monitoring_unavailable';
  // A silent supervisor says nothing about the child, so it is reported before
  // the child's own freshness rather than being mistaken for it.
  if (supervisorAge !== undefined && supervisorAge > SUPERVISOR_STALE_SECONDS)
    return 'disconnected';
  if (publicationAge > PUBLICATION_STALE_SECONDS) return 'unresponsive';
  if (progressAge > STALLED_SECONDS) return 'stalled';
  if (progressAge > LONG_OPERATION_SECONDS) return 'long_operation';
  return 'active';
}

/** Compact, locale-independent duration used by the job header and phase block. */
export function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  return `${hours ? `${hours}h ` : ''}${Math.floor((seconds % 3600) / 60)}m ${Math.floor(seconds % 60)}s`;
}

export interface PhaseSummary {
  /** Fraction of the phase's scheduled work units, when a total is known. */
  ratio?: number;
  /** Labels are English source keys; the view translates them. */
  rows: Array<{ label: string; value: string }>;
}

function count(value: unknown, locale: string): string | undefined {
  return finite(value) ? value.toLocaleString(locale) : undefined;
}

function pair(completed: unknown, total: unknown, locale: string): string | undefined {
  const done = count(completed, locale);
  const scheduled = count(total, locale);
  if (done === undefined && scheduled === undefined) return undefined;
  return `${done ?? '—'} / ${scheduled ?? '—'}`;
}

function fraction(completed: unknown, total: unknown): number | undefined {
  if (!finite(completed) || !finite(total) || total <= 0) return undefined;
  return Math.max(0, Math.min(1, completed / total));
}

/**
 * Phase-specific counters that are genuinely known while a phase runs. Unknown
 * or legacy values are omitted rather than rendered as an invented percentage.
 */
export function phaseSummary(stage: string, counters: Values, locale: string): PhaseSummary {
  const rows: PhaseSummary['rows'] = [];
  let ratio: number | undefined;
  const add = (label: string, value: string | undefined) => {
    if (value !== undefined) rows.push({ label, value });
  };
  if (stage === 'self_play') {
    ratio = fraction(counters.games_completed, counters.games_total);
    add('Games', pair(counters.games_completed, counters.games_total, locale));
    add('Active games', count(counters.games_active, locale));
    add('Positions', count(counters.positions, locale));
    add('MCTS simulations', count(counters.simulations, locale));
  } else if (stage === 'optimization') {
    ratio = fraction(counters.optimizer_steps, counters.optimizer_total_steps);
    add(
      'Optimization steps',
      pair(counters.optimizer_steps, counters.optimizer_total_steps, locale),
    );
    add('Epoch', count(counters.epoch, locale));
    add('Policy loss', metric(counters.policy_loss));
    add('Value loss', metric(counters.value_loss));
  } else if (stage === 'evaluation') {
    ratio = fraction(counters.evaluation_games, counters.evaluation_total);
    add('Games', pair(counters.evaluation_games, counters.evaluation_total, locale));
    add('Wins', count(counters.evaluation_wins, locale));
    add('Draws', count(counters.evaluation_draws, locale));
    add('Losses', count(counters.evaluation_losses, locale));
    add(
      'Opponent',
      typeof counters.evaluation_opponent === 'string' ? counters.evaluation_opponent : undefined,
    );
  }
  return ratio === undefined ? { rows } : { ratio, rows };
}

function metric(value: unknown): string | undefined {
  return finite(value) ? value.toFixed(3) : undefined;
}
