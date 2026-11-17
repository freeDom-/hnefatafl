import { useCallback, useEffect, useMemo, useRef } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import { notify } from './notifications';
import { useOnlinePoll } from './useOnlinePoll';
import type { AchievementProgress } from './portraitTypes';

/**
 * How often the shared progression read reconciles remote completions while the
 * page is visible. Completion events, focus, reconnect and profile changes still
 * refresh immediately; the interval is only the eventual-consistency backstop
 * for routes that have no live game poll of their own. `useOnlinePoll` suspends
 * the recurring read while the tab is hidden and coalesces concurrent refreshes.
 */
export const PROGRESSION_POLL_MS = 15000;

export type ProgressionValue = {
  achievements: AchievementProgress | null;
  phase: 'loading' | 'ready' | 'stale';
  refresh: () => Promise<AchievementProgress | null>;
};

/**
 * One account baseline and event-driven refreshes; never recurring per-component
 * polling. The single caller is `ProgressionProvider`, which shares the result.
 */
export function useAchievementNotifications(
  owner: string | undefined,
  intervalMs: number = PROGRESSION_POLL_MS,
): ProgressionValue {
  const { t, locale } = useI18n();
  const load = useCallback(
    (signal: AbortSignal) =>
      api<AchievementProgress>('/personal/achievements', undefined, undefined, signal),
    [],
  );
  const poll = useOnlinePoll(`progression:${owner ?? 'anonymous'}`, load, { intervalMs });
  // A first read establishes the baseline: historical awards are never announced.
  const previous = useRef<{ owner: string; data: AchievementProgress } | null>(null);
  useEffect(() => {
    const next = poll.data;
    if (!owner || !next) return;
    const prior = previous.current;
    previous.current = { owner, data: next };
    if (!prior || prior.owner !== owner) return;
    // Tolerate a partial projection (for example a mocked or pre-upgrade
    // response) without failing the whole shell; a full server payload always
    // carries both collections.
    const priorItems = prior.data.items ?? [];
    const old = new Set(priorItems.filter((item) => item.unlocked).map((item) => item.id));
    const added = (next.items ?? []).filter((item) => item.unlocked && !old.has(item.id));
    const priorPortraits = prior.data.unlocked_portrait_ids ?? [];
    const portraits = (next.unlocked_portrait_ids ?? []).filter(
      (id) => !priorPortraits.includes(id),
    );
    if (!added.length && !portraits.length) return;
    const number = new Intl.NumberFormat(locale);
    if (added.length)
      notify.success(
        t('New milestones: {count} · +{points} points', {
          count: number.format(added.length),
          points: number.format(
            (next.summary?.earned_points ?? 0) - (prior.data.summary?.earned_points ?? 0),
          ),
        }),
      );
    if (portraits.length)
      notify.success(
        t('New portraits available: {count}', { count: number.format(portraits.length) }),
      );
  }, [owner, poll.data, t, locale]);
  // Explicit completion signals from the game room, analysis and dashboard polls
  // refresh the shared read instead of each owner fetching its own copy.
  useEffect(() => {
    const wake = () => void poll.refresh();
    window.addEventListener('progression-changed', wake);
    return () => window.removeEventListener('progression-changed', wake);
  }, [poll.refresh]);
  return useMemo(
    () => ({ achievements: poll.data, phase: poll.phase, refresh: poll.refresh }),
    [poll.data, poll.phase, poll.refresh],
  );
}
