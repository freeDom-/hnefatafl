import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAchievementNotifications } from './useAchievementNotifications';
import { api } from './api';
import { notify } from './notifications';
import { setLanguage } from './i18n';
import type { AchievementProgress } from './portraitTypes';
vi.mock('./api', () => ({ api: vi.fn() }));
vi.mock('./notifications', () => ({ notify: { success: vi.fn() } }));
const snapshot = (earned = false): AchievementProgress => ({
  catalog_version: 3,
  portrait_catalog_version: 2,
  as_of: '2035-01-01',
  families: [],
  unlocked_portrait_ids: earned ? ['shieldmaiden'] : [],
  summary: {
    unlocked: earned ? 1 : 0,
    total: 77,
    earned_points: earned ? 30 : 0,
    total_points: 6290,
  },
  items: earned
    ? [
        {
          id: 'rated-wins-10',
          family_id: 'rated-wins',
          tier: 3,
          points: 30,
          name: 'Rated online victories',
          description: '',
          category: 'rated',
          scope: null,
          threshold: 10,
          progress: 10,
          unlocked: true,
          awarded_at: '2035-01-01',
          portrait_id: 'shieldmaiden',
        },
      ]
    : [],
});
beforeEach(() => {
  vi.clearAllMocks();
  setLanguage('en');
});
afterEach(cleanup);
it('refreshes on completion events and announces automatic milestones and portraits once', async () => {
  let progress = snapshot();
  vi.mocked(api).mockImplementation(async () => structuredClone(progress) as never);
  renderHook(() => useAchievementNotifications('archer'));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  await act(async () => {});
  progress = snapshot(true);
  act(() => window.dispatchEvent(new Event('progression-changed')));
  await waitFor(() =>
    expect(notify.success).toHaveBeenCalledWith('New milestones: 1 · +30 points'),
  );
  expect(notify.success).toHaveBeenCalledWith('New portraits available: 1');
  act(() => window.dispatchEvent(new Event('progression-changed')));
  await act(async () => {});
  expect(notify.success).toHaveBeenCalledTimes(2);
});
it('coalesces events during one in-flight refresh and ignores a late old-account response', async () => {
  let finish: (value: AchievementProgress) => void = () => {};
  let signal: AbortSignal | undefined;
  vi.mocked(api).mockImplementation((_path, _body, _method, requestSignal) => {
    signal = requestSignal;
    return new Promise((resolve) => {
      finish = resolve as typeof finish;
    });
  });
  const hook = renderHook(({ owner }) => useAchievementNotifications(owner), {
    initialProps: { owner: 'archer' },
  });
  act(() => {
    window.dispatchEvent(new Event('progression-changed'));
    window.dispatchEvent(new Event('progression-changed'));
  });
  expect(api).toHaveBeenCalledTimes(1);
  const oldSignal = signal;
  const oldFinish = finish;
  vi.mocked(api).mockResolvedValue(snapshot() as never);
  hook.rerender({ owner: 'keeper' });
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => oldFinish(snapshot(true)));
  expect(notify.success).not.toHaveBeenCalled();
});
it('does not poll or announce historical awards on initial load, and localizes new rewards', async () => {
  setLanguage('de');
  let progress = snapshot();
  vi.mocked(api).mockImplementation(async () => structuredClone(progress) as never);
  renderHook(() => useAchievementNotifications('archer'));
  await act(async () => {});
  expect(notify.success).not.toHaveBeenCalled();
  expect(api).toHaveBeenCalledTimes(1);
  progress = snapshot(true);
  act(() => window.dispatchEvent(new Event('progression-changed')));
  await waitFor(() =>
    expect(notify.success).toHaveBeenCalledWith('Neue Meilensteine: 1 · +30 Punkte'),
  );
  expect(notify.success).toHaveBeenCalledWith('Neue Porträts verfügbar: 1');
});
