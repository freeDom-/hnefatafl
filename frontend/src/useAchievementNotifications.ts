import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  confirmPortraitUnlock: (id: string) => void;
};

type NotificationBaseline = {
  owner: string;
  awardedIds: Set<string>;
  eligiblePortraitIds: Set<string>;
  claimedPortraitIds: Set<string>;
  earnedPoints: number;
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
  const [confirmed, setConfirmed] = useState<{ owner: string; ids: string[] }>();
  const confirmPortraitUnlock = useCallback(
    (id: string) => {
      if (!owner) return;
      setConfirmed((previous) => {
        const ids = previous?.owner === owner ? previous.ids : [];
        return ids.includes(id) ? previous : { owner, ids: [...ids, id] };
      });
    },
    [owner],
  );
  // Claims are permanent. A read already in flight when a claim commits may
  // still return an older snapshot. Merge only IDs confirmed by the write.
  const achievements = useMemo(() => {
    if (!poll.data || !confirmed || confirmed.owner !== owner) return poll.data;
    const unlocked = poll.data.unlocked_portrait_ids ?? [];
    const added = confirmed.ids.filter((id) => !unlocked.includes(id));
    return added.length
      ? { ...poll.data, unlocked_portrait_ids: [...unlocked, ...added].sort() }
      : poll.data;
  }, [poll.data, confirmed, owner]);
  // Awards and claims are permanent. Keep what this session has already seen
  // so an older response cannot cause a second announcement.
  const baseline = useRef<NotificationBaseline | null>(null);
  useEffect(() => {
    if (baseline.current?.owner !== owner) baseline.current = null;
    if (!owner || !achievements) return;
    const prior = baseline.current;
    const observed = (baseline.current = prior ?? {
      owner,
      awardedIds: new Set<string>(),
      eligiblePortraitIds: new Set<string>(),
      claimedPortraitIds: new Set<string>(),
      earnedPoints: 0,
    });
    const previousAwards = observed.awardedIds.size;
    const previousEligible = observed.eligiblePortraitIds.size;
    const previousClaims = observed.claimedPortraitIds.size;
    for (const id of achievements.unlocked_portrait_ids ?? []) observed.claimedPortraitIds.add(id);
    for (const milestone of achievements.items ?? []) {
      if (!milestone.unlocked || observed.awardedIds.has(milestone.id)) continue;
      observed.awardedIds.add(milestone.id);
      const portraitId = milestone.portrait_id;
      if (portraitId && !observed.claimedPortraitIds.has(portraitId))
        observed.eligiblePortraitIds.add(portraitId);
    }
    const points = Math.max(0, (achievements.summary?.earned_points ?? 0) - observed.earnedPoints);
    observed.earnedPoints += points;
    if (!prior) return; // The first read only establishes the historical baseline.

    const awards = observed.awardedIds.size - previousAwards;
    const eligible = observed.eligiblePortraitIds.size - previousEligible;
    const claims = observed.claimedPortraitIds.size - previousClaims;
    if (!awards && !eligible && !claims) return;
    const number = new Intl.NumberFormat(locale);
    if (awards)
      notify.success(
        t('New milestones: {count} · +{points} points', {
          count: number.format(awards),
          points: number.format(points),
        }),
      );
    if (eligible)
      notify.success(
        t('New portraits ready to unlock: {count}', { count: number.format(eligible) }),
      );
    if (claims)
      notify.success(t('New portraits unlocked: {count}', { count: number.format(claims) }));
  }, [owner, achievements, t, locale]);
  // Explicit completion signals from the game room, analysis and dashboard polls
  // refresh the shared read instead of each owner fetching its own copy.
  useEffect(() => {
    const wake = () => void poll.refresh();
    window.addEventListener('progression-changed', wake);
    return () => window.removeEventListener('progression-changed', wake);
  }, [poll.refresh]);
  return useMemo(
    () => ({ achievements, phase: poll.phase, refresh: poll.refresh, confirmPortraitUnlock }),
    [achievements, poll.phase, poll.refresh, confirmPortraitUnlock],
  );
}
