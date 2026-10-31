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

export type HeartbeatState =
  | 'active'
  | 'long_operation'
  | 'stalled'
  | 'unresponsive'
  | 'waiting'
  | 'terminal';

export interface LiveProgress {
  /** Seconds since the job started; fixed once the job is terminal. */
  elapsed?: number;
  /** Seconds in the current phase; restarts when the phase changes. */
  phaseElapsed?: number;
  /** Seconds since meaningful computational progress. */
  progressAge?: number;
  /** Seconds since the last refreshed monitoring snapshot. */
  publicationAge?: number;
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
  at: number;
  duration: number;
  phase: number;
  progressAge: number;
  snapshotKey: string;
  changedAt: number;
}

function number(value: unknown): number | undefined {
  return finite(value) ? value : undefined;
}

export function useLiveProgress(job: Job): LiveProgress {
  const progress = jobProgress(job);
  const active = activeJob(job);
  const now = useMonotonicNow(active);
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const duration = number(progress.duration);
  const phaseElapsed = number(progress.phase_elapsed);
  const progressAge = number(progress.last_progress_seconds);
  const stage = typeof progress.stage === 'string' ? progress.stage : '';
  const phaseKey = `${stage}@${String(progress.phase_started_at ?? '')}@${String(progress.iteration ?? '')}`;
  // Every publication refreshes this key, including heartbeat-only refreshes.
  const snapshotKey = String(progress.updated_at ?? progress.duration ?? '');
  const anchorKey = `${job.id}|${phaseKey}|${snapshotKey}|${duration}|${phaseElapsed}|${progressAge}`;

  // Re-anchor during render so a new snapshot is reflected in the same commit
  // instead of one tick later.
  if (anchor === null || anchor.key !== anchorKey) {
    const at = typeof performance === 'undefined' ? 0 : performance.now();
    const sameJob = anchor !== null && anchor.jobId === job.id;
    const samePhase = sameJob && anchor.phaseKey === phaseKey;
    const changed = !sameJob || anchor.snapshotKey !== snapshotKey;
    // Everything already displayed stays displayed: an out-of-order or delayed
    // snapshot must never undo elapsed time that was on screen.
    const shown = sameJob ? anchor.duration + (at - anchor.at) / 1000 : 0;
    const shownPhase = samePhase ? anchor.phase + (at - anchor.at) / 1000 : 0;
    setAnchor({
      key: anchorKey,
      jobId: job.id,
      phaseKey,
      at,
      duration: Math.max(shown, duration ?? 0),
      phase: Math.max(shownPhase, phaseElapsed ?? 0),
      // Progress age legitimately resets when work resumes, so it follows the
      // newest report instead of the maximum.
      progressAge: progressAge ?? 0,
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
  const phaseBase = anchored ? current.phase : (phaseElapsed ?? 0);
  if (phaseElapsed !== undefined) {
    live.phaseElapsed = active ? phaseBase + sinceAnchor : phaseBase;
  }
  if (monitored) {
    const ageBase = anchored ? current.progressAge : (progressAge ?? 0);
    live.progressAge = active ? ageBase + sinceAnchor : ageBase;
    live.publicationAge = anchored ? Math.max((now - current.changedAt) / 1000, 0) : 0;
    live.heartbeat = active ? classify(live.progressAge, live.publicationAge) : 'terminal';
  }
  if (active && ['pending', 'queued'].includes(job.status)) live.heartbeat = 'waiting';
  return live;
}

function classify(progressAge: number, publicationAge: number): HeartbeatState {
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
