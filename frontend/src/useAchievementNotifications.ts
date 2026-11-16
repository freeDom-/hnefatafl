import { useEffect } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import { notify } from './notifications';
import type { AchievementProgress } from './portraitTypes';

/** One account baseline and event-driven refreshes; never recurring polling. */
export function useAchievementNotifications(owner: string | undefined) {
  const { t, locale } = useI18n();
  useEffect(() => {
    if (!owner) return;
    let alive = true,
      running = false,
      queued = false;
    let previous: AchievementProgress | null = null;
    const controller = new AbortController();
    const refresh = async () => {
      if (running) {
        queued = true;
        return;
      }
      running = true;
      do {
        queued = false;
        try {
          const next = await api<AchievementProgress>(
            '/personal/achievements',
            undefined,
            undefined,
            controller.signal,
          );
          if (!alive) return;
          if (previous) {
            const old = new Set(
              previous.items.filter((item) => item.unlocked).map((item) => item.id),
            );
            const added = next.items.filter((item) => item.unlocked && !old.has(item.id));
            const portraits = next.unlocked_portrait_ids.filter(
              (id) => !previous!.unlocked_portrait_ids.includes(id),
            );
            const number = new Intl.NumberFormat(locale);
            if (added.length)
              notify.success(
                t('New milestones: {count} · +{points} points', {
                  count: number.format(added.length),
                  points: number.format(
                    next.summary.earned_points - previous.summary.earned_points,
                  ),
                }),
              );
            if (portraits.length)
              notify.success(
                t('New portraits available: {count}', { count: number.format(portraits.length) }),
              );
            if (added.length || portraits.length)
              window.dispatchEvent(new Event('profile-changed'));
          }
          previous = next;
        } catch {
          /* Existing views own error/retry notices; focus retries this baseline. */
        }
      } while (queued && alive);
      running = false;
    };
    const wake = () => {
      void refresh();
    };
    window.addEventListener('progression-changed', wake);
    window.addEventListener('focus', wake);
    void refresh();
    return () => {
      alive = false;
      controller.abort();
      window.removeEventListener('progression-changed', wake);
      window.removeEventListener('focus', wake);
    };
  }, [owner, t, locale]);
}
