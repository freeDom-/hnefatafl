import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAchievementNotifications } from './useAchievementNotifications';
import { api } from './api';
import { notify } from './notifications';
import { setLanguage } from './i18n';
import type { Achievement, AchievementProgress } from './portraitTypes';
vi.mock('./api', () => ({ api: vi.fn() }));
vi.mock('./notifications', () => ({ notify: { success: vi.fn() } }));

const award = (id: string, points: number, portrait_id: string | null = null): Achievement => ({
  id,
  family_id: id,
  tier: 1,
  points,
  name: id,
  description: '',
  category: 'rated',
  scope: null,
  threshold: 10,
  progress: 10,
  unlocked: true,
  awarded_at: '2035-01-01',
  portrait_id,
});
const firstGame = award('local-first-game', 10);
const shieldmaiden = award('rated-wins-10', 30, 'shieldmaiden');
const forgeKeeper = award('ai-wins-10', 30, 'forge-keeper');
const snapshot = (items: Achievement[] = [], claimed: string[] = []): AchievementProgress => ({
  catalog_version: 3,
  portrait_catalog_version: 2,
  as_of: '2035-01-01',
  families: [],
  unlocked_portrait_ids: claimed,
  summary: {
    unlocked: items.filter((item) => item.unlocked).length,
    total: 77,
    earned_points: items.reduce((total, item) => total + (item.unlocked ? item.points : 0), 0),
    total_points: 6290,
  },
  items,
});

async function observe(initial = snapshot()) {
  let progress = initial;
  vi.mocked(api).mockImplementation(async () => structuredClone(progress) as never);
  const hook = renderHook(() => useAchievementNotifications('archer', 0));
  await act(async () => {});
  return {
    result: hook.result,
    publish: async (next: AchievementProgress, event = 'progression-changed') => {
      progress = next;
      await act(async () => {
        window.dispatchEvent(new Event(event));
      });
    },
  };
}

const notices = () => vi.mocked(notify.success).mock.calls.map(([message]) => message);

beforeEach(() => {
  vi.clearAllMocks();
  setLanguage('en');
});
afterEach(cleanup);

it('refreshes on completion and announces a milestone without a portrait', async () => {
  const { publish } = await observe();
  await publish(snapshot([firstGame]));
  expect(notices()).toEqual(['New milestones: 1 · +10 points']);
  expect(vi.mocked(api).mock.calls.map(([path]) => path)).toEqual([
    '/personal/achievements',
    '/personal/achievements',
  ]);
});

it('announces eligibility when the milestone is awarded, then confirms the separate claim', async () => {
  const { publish } = await observe();
  // Reaching a projected threshold alone does not prove an award.
  await publish(snapshot([{ ...shieldmaiden, unlocked: false, awarded_at: null }]));
  expect(notices()).toEqual([]);

  const eligible = snapshot([shieldmaiden]);
  await publish(eligible);
  await publish(eligible);
  expect(notices()).toEqual(['New milestones: 1 · +30 points', 'New portraits ready to unlock: 1']);

  const claimed = snapshot([shieldmaiden], ['shieldmaiden']);
  await publish(claimed);
  await publish(claimed);
  expect(notices()).toEqual([
    'New milestones: 1 · +30 points',
    'New portraits ready to unlock: 1',
    'New portraits unlocked: 1',
  ]);
});

it('counts unique portraits in batches of new eligibility and claims', async () => {
  const { publish } = await observe();
  const awards = [shieldmaiden, forgeKeeper, award('duplicate-portrait', 10, 'shieldmaiden')];
  await publish(snapshot(awards));
  await publish(snapshot(awards, ['shieldmaiden', 'shieldmaiden', 'forge-keeper']));
  expect(notices()).toEqual([
    'New milestones: 3 · +70 points',
    'New portraits ready to unlock: 2',
    'New portraits unlocked: 2',
  ]);
});

it('excludes historical and newly observed claims from a batch of new eligibility', async () => {
  const { publish } = await observe(snapshot([], ['shieldmaiden']));
  await publish(snapshot([shieldmaiden, forgeKeeper], ['shieldmaiden', 'forge-keeper']));
  expect(notices()).toEqual(['New milestones: 2 · +60 points', 'New portraits unlocked: 1']);
});

it('keeps a successful local claim across an outdated in-flight response', async () => {
  const eligible = snapshot([shieldmaiden]);
  const { publish, result } = await observe();
  await publish(eligible);
  let finish: (value: AchievementProgress) => void = () => {};
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve as typeof finish;
      }),
  );
  act(() => window.dispatchEvent(new Event('progression-changed')));
  act(() => result.current.confirmPortraitUnlock('shieldmaiden'));
  expect(result.current.achievements?.unlocked_portrait_ids).toEqual(['shieldmaiden']);
  await act(async () => finish(snapshot()));
  expect(result.current.achievements?.unlocked_portrait_ids).toEqual(['shieldmaiden']);
  await publish(snapshot([shieldmaiden], ['shieldmaiden']));
  await publish(snapshot([shieldmaiden, firstGame], ['shieldmaiden']));
  expect(notices()).toEqual([
    'New milestones: 1 · +30 points',
    'New portraits ready to unlock: 1',
    'New portraits unlocked: 1',
    'New milestones: 1 · +10 points',
  ]);
});

it('announces a remote claim once when reconnecting, even if a later snapshot is outdated', async () => {
  const eligible = snapshot([shieldmaiden]);
  const { publish } = await observe(eligible);
  const claimed = snapshot([shieldmaiden], ['shieldmaiden']);
  await publish(claimed, 'online');
  await publish(eligible, 'focus');
  await publish(claimed, 'focus');
  expect(notices()).toEqual(['New portraits unlocked: 1']);
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
  await act(async () => oldFinish(snapshot([shieldmaiden], ['shieldmaiden'])));
  expect(notify.success).not.toHaveBeenCalled();
});
it('loads historical awards, eligibility and claims without announcing them', async () => {
  const history = snapshot([firstGame, shieldmaiden, forgeKeeper], ['forge-keeper']);
  const { publish } = await observe(history);
  expect(api).toHaveBeenCalledTimes(1);
  await publish(history);
  expect(notices()).toEqual([]);
});

it('localizes new milestone, eligibility and claim notifications in German', async () => {
  setLanguage('de');
  const { publish } = await observe();
  await publish(snapshot([shieldmaiden]));
  await publish(snapshot([shieldmaiden], ['shieldmaiden']));
  expect(notices()).toEqual([
    'Neue Meilensteine: 1 · +30 Punkte',
    'Neue Porträts zum Freischalten: 1',
    'Neue Porträts freigeschaltet: 1',
  ]);
});

it('defers completion refreshes while hidden and resumes once visible', async () => {
  let visibility = 'visible';
  const getter = vi
    .spyOn(document, 'visibilityState', 'get')
    .mockImplementation(() => visibility as DocumentVisibilityState);
  vi.mocked(api).mockResolvedValue(snapshot() as never);
  renderHook(() => useAchievementNotifications('archer'));
  await act(async () => {});
  visibility = 'hidden';
  act(() => {
    window.dispatchEvent(new Event('progression-changed'));
    window.dispatchEvent(new Event('focus'));
  });
  expect(api).toHaveBeenCalledTimes(1);
  visibility = 'visible';
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  await act(async () => {});
  expect(api).toHaveBeenCalledTimes(2);
  getter.mockRestore();
});
