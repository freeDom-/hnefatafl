import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PortraitHall from './PortraitHall';
import Portrait from './Portrait';
import { ProgressionProvider } from './Progression';
import { PROGRESSION_POLL_MS } from './useAchievementNotifications';
import { api } from './api';
import { notify } from './notifications';
import { setLanguage } from './i18n';
import type { AchievementProgress, PortraitCatalog } from './portraitTypes';
import { projectAchievements } from '../tests/achievement-fixture';

const identity = vi.hoisted(() => ({ id: 'archer' }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: vi.fn(),
}));
vi.mock('./notifications', () => ({ notify: { success: vi.fn(), error: vi.fn() } }));
const catalog: PortraitCatalog = {
  catalog_version: 2,
  default_portrait_id: 'shieldbearer',
  items: [
    { id: 'shieldbearer', name: 'Shieldbearer', unlock_achievement_id: null },
    { id: 'shieldmaiden', name: 'Shieldmaiden', unlock_achievement_id: 'rated-wins-10' },
    { id: 'iron-helm', name: 'Iron Helm', unlock_achievement_id: 'defender-wins-10' },
    { id: 'skald', name: 'Skald', unlock_achievement_id: 'tablut-wins-10' },
  ],
};
function fixture(): AchievementProgress {
  const items = ['rated-wins', 'defender-wins', 'tablut-wins'].flatMap((family, familyIndex) =>
    [1, 3, 10, 25, 50, 100, 250, 500, 1000].map((threshold, index) => ({
      id: `${family}-${threshold}`,
      family_id: family,
      tier: index + 1,
      points: [10, 20, 30, 40, 60, 80, 120, 160, 240][index],
      name: ['Rated online victories', 'Rated victories as Defender', 'Rated Tablut victories'][
        familyIndex
      ],
      description: '',
      category: 'rated',
      scope: null,
      threshold,
      progress: familyIndex === 0 ? 10 : 0,
      unlocked: familyIndex === 0 ? index < 3 : familyIndex === 2 && index === 0,
      awarded_at: familyIndex === 0 && index < 3 ? '2035-01-01T00:00:00Z' : null,
      portrait_id: null,
    })),
  );
  return projectAchievements({
    catalog_version: 3,
    portrait_catalog_version: 2,
    as_of: '2035-01-01T00:00:00Z',
    summary: { unlocked: 0, total: 27, earned_points: 0, total_points: 2280 },
    items,
    families: [],
    unlocked_portrait_ids: ['shieldmaiden', 'skald'],
  });
}
let progress: AchievementProgress;
let selected = 'shieldbearer';
beforeEach(() => {
  identity.id = 'archer';
  selected = 'shieldbearer';
  progress = fixture();
  vi.clearAllMocks();
  setLanguage('en');
  vi.mocked(api).mockImplementation(async (path, body, method) => {
    if (path === '/portraits/catalog') return catalog as never;
    if (path === '/personal/achievements')
      return structuredClone(projectAchievements(progress)) as never;
    if (path === '/profile') return { portrait_id: selected } as never;
    if (path === '/profile/portrait' && method === 'PATCH') {
      selected = (body as { portrait_id: string }).portrait_id;
      return { portrait_id: selected } as never;
    }
    throw new Error(`Unexpected API ${path}`);
  });
});
afterEach(cleanup);
// The Hall consumes the shared progression read, so every render provides it.
function Hall({ intervalMs }: { intervalMs?: number } = {}) {
  return (
    <ProgressionProvider owner={identity.id} intervalMs={intervalMs}>
      <PortraitHall />
    </ProgressionProvider>
  );
}
const card = (name: string) => screen.getByRole('heading', { name }).closest('li')!;

it('uses automatic awards and permanent portrait entitlements, preserving the server catalog order', async () => {
  render(<Hall />);
  await screen.findByRole('heading', { name: 'Shieldbearer' });
  expect(card('Shieldbearer')).toHaveAttribute('data-portrait-state', 'selected');
  expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'earned');
  expect(card('Iron Helm')).toHaveAttribute('data-portrait-state', 'locked');
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'earned');
  expect(within(card('Skald')).queryByRole('progressbar')).not.toBeInTheDocument();
  expect(within(card('Iron Helm')).getByRole('progressbar')).toBeVisible();
  expect(
    within(screen.getByRole('list', { name: 'Portrait collection' }))
      .getAllByRole('heading')
      .map((x) => x.textContent),
  ).toEqual(['Shieldbearer', 'Shieldmaiden', 'Iron Helm', 'Skald']);
  expect(screen.queryByRole('button', { name: /claim|collect/i })).not.toBeInTheDocument();
  expect(vi.mocked(api).mock.calls.every(([, body, method]) => !body && !method)).toBe(true);
});
it('groups families and places the score under Deeds of Valor with the next unachieved target', async () => {
  render(<Hall />);
  const family = (await screen.findByRole('heading', { name: 'Rated online victories' })).closest(
    'li',
  )!;
  expect(family).toHaveAttribute('data-tier', '3');
  expect(within(family).getByText('Next milestone: 25 · 40 points')).toBeVisible();
  expect(within(family).getByText('Earned tier: 3 / 9 · threshold 10')).toBeVisible();
  expect(document.querySelectorAll('.portrait-achievement-list > li')).toHaveLength(3);
  const section = screen.getByRole('heading', { name: 'Deeds of Valor' }).closest('section')!;
  expect(
    within(section).getByRole('progressbar', { name: 'Achievement Score — 70 / 2,280 points' }),
  ).toHaveAttribute('value', '70');
  expect(
    section.compareDocumentPosition(screen.getByRole('list', { name: 'Portrait collection' })) &
      Node.DOCUMENT_POSITION_PRECEDING,
  ).toBeTruthy();
  expect(screen.queryByText(/Ready to collect/)).not.toBeInTheDocument();
});
it('updates points and visual tiers from an authoritative wake without collection', async () => {
  render(<Hall />);
  const family = (await screen.findByRole('heading', { name: 'Rated online victories' })).closest(
    'li',
  )!;
  progress.items = progress.items.map((item) =>
    item.family_id === 'rated-wins'
      ? { ...item, progress: 25, unlocked: item.threshold <= 25 }
      : item,
  );
  fireEvent(window, new Event('profile-changed'));
  await waitFor(() => expect(family).toHaveAttribute('data-tier', '4'));
  expect(family).toHaveClass('achievement-silver');
  expect(within(family).getByText('Next milestone: 50 · 60 points')).toBeVisible();
  expect(screen.getByText('Achievement Score — 110 / 2,280 points')).toBeVisible();
});
it('keeps the shared snapshot rendered when the route cadence changes', async () => {
  const achievementsCalls = () =>
    vi.mocked(api).mock.calls.filter(([path]) => path === '/personal/achievements').length;
  const view = render(<Hall intervalMs={0} />);
  await screen.findByRole('heading', { name: 'Shieldbearer' });
  expect(achievementsCalls()).toBe(1);
  expect(screen.queryByText('Loading your portraits…')).not.toBeInTheDocument();

  view.rerender(<Hall intervalMs={PROGRESSION_POLL_MS} />);
  expect(screen.getByRole('heading', { name: 'Shieldbearer' })).toBeVisible();
  expect(screen.queryByText('Loading your portraits…')).not.toBeInTheDocument();
  expect(achievementsCalls()).toBe(1);

  view.rerender(<Hall intervalMs={0} />);
  expect(screen.getByRole('heading', { name: 'Shieldbearer' })).toBeVisible();
  expect(achievementsCalls()).toBe(1);
});

it('refreshes score and portrait state for a remote completion without focus or navigation', async () => {
  vi.useFakeTimers();
  try {
    // Start with the Tablut milestone locked so the remote completion is the
    // only thing that can change the score and Skald eligibility.
    progress.items = progress.items.map((item) =>
      item.family_id === 'tablut-wins' ? { ...item, unlocked: false } : item,
    );
    progress.unlocked_portrait_ids = ['shieldmaiden'];
    render(<Hall />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText('Achievement Score — 60 / 2,280 points')).toBeVisible();
    expect(card('Skald')).toHaveAttribute('data-portrait-state', 'locked');
    expect(notify.success).not.toHaveBeenCalled();

    // The opponent completes elsewhere. No focus, navigation or profile event is
    // dispatched: only the shared progression read reconciles the result.
    progress.items = progress.items.map((item) =>
      item.family_id === 'tablut-wins'
        ? { ...item, progress: 10, unlocked: item.threshold <= 10 }
        : item,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROGRESSION_POLL_MS);
    });

    expect(screen.getByText('Achievement Score — 120 / 2,280 points')).toBeVisible();
    expect(card('Skald')).toHaveAttribute('data-portrait-state', 'ready');
    expect(
      within(card('Skald')).getByRole('button', { name: 'Unlock portrait: Skald' }),
    ).toBeEnabled();
    expect(within(card('Skald')).queryByRole('progressbar')).not.toBeInTheDocument();
    expect(notify.success).toHaveBeenCalledExactlyOnceWith('New milestones: 3 · +60 points');
  } finally {
    vi.useRealTimers();
  }
});
it('applies confirmed claims to the shared projection even across an older in-flight read', async () => {
  progress.unlocked_portrait_ids = [];
  const original = vi.mocked(api).getMockImplementation()!;
  let answerRead: (value: AchievementProgress) => void = () => {};
  let reading = false;
  vi.mocked(api).mockImplementation((path, ...rest) => {
    if (path === '/personal/achievements' && reading)
      return new Promise((resolve) => {
        answerRead = resolve;
      });
    if (path === '/portraits/unlock') {
      return Promise.resolve({ portrait_id: 'shieldmaiden', newly_unlocked: false });
    }
    return original(path, ...rest);
  });
  render(<Hall />);
  const unlock = await screen.findByRole('button', { name: 'Unlock portrait: Shieldmaiden' });
  reading = true;
  fireEvent(window, new Event('progression-changed'));
  fireEvent.click(unlock);
  await screen.findByRole('button', { name: 'Select portrait: Shieldmaiden' });
  await act(async () => answerRead(projectAchievements(progress)));
  expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'earned');
  expect(card('Shieldbearer')).toHaveAttribute('data-portrait-state', 'selected');
  expect(notify.success).toHaveBeenCalledExactlyOnceWith('New portraits available: 1');
  expect(document.querySelector('canvas')).toBeNull();
});

it('discards an in-flight unlock response when the account changes', async () => {
  progress.unlocked_portrait_ids = [];
  const original = vi.mocked(api).getMockImplementation()!;
  let respond: (value: unknown) => void = () => {};
  let signal: AbortSignal | undefined;
  vi.mocked(api).mockImplementation((path, body, method, requestSignal) => {
    if (path !== '/portraits/unlock') return original(path, body, method, requestSignal);
    signal = requestSignal;
    return new Promise((resolve) => {
      respond = resolve;
    });
  });
  const view = render(<Hall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Unlock portrait: Shieldmaiden' }));
  identity.id = 'keeper';
  view.rerender(<Hall />);
  await waitFor(() => expect(signal?.aborted).toBe(true));
  await act(async () => respond({ portrait_id: 'shieldmaiden', newly_unlocked: true }));
  expect(
    await screen.findByRole('button', { name: 'Unlock portrait: Shieldmaiden' }),
  ).toBeEnabled();
  expect(notify.success).not.toHaveBeenCalled();
});
it('retains the awarded tier after correction and still targets the next unearned milestone', async () => {
  progress.items = progress.items.map((item) =>
    item.family_id === 'rated-wins' ? { ...item, progress: 0 } : item,
  );
  render(<Hall />);
  const family = (await screen.findByRole('heading', { name: 'Rated online victories' })).closest(
    'li',
  )!;
  expect(family).toHaveAttribute('data-tier', '3');
  expect(within(family).getByText('Recorded progress: 0 · highest achieved: 0')).toBeVisible();
  expect(within(family).getByRole('progressbar')).toHaveAccessibleName(
    'Next milestone: 25 · 40 points',
  );
});
it('renders full completion at 100% without further targets', async () => {
  progress.items = progress.items.map((item) => ({ ...item, progress: 1000, unlocked: true }));
  render(<Hall />);
  await screen.findByText('100% complete · 27 / 27 milestones earned');
  for (const family of document.querySelectorAll('.achievement-card')) {
    expect(family).toHaveClass('achievement-legendary');
    expect(within(family as HTMLElement).queryByRole('progressbar')).not.toBeInTheDocument();
  }
});
it('shows a localized zero score and automatic tier labels in German', async () => {
  progress.items = progress.items.map((item) => ({ ...item, progress: 0, unlocked: false }));
  progress.unlocked_portrait_ids = [];
  setLanguage('de');
  render(<Hall />);
  await screen.findByText('Errungenschaftspunkte — 0 / 2.280 Punkte');
  expect(screen.getByRole('heading', { name: 'Halle des Ruhms' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Heldentaten' })).toBeVisible();
  expect(screen.getByText('0% abgeschlossen · 0 / 27 Meilensteine verdient')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Gewertete Online-Siege' })).toBeVisible();
});
it('associates runes with family IDs even when the server order changes', async () => {
  progress.items.reverse();
  render(<Hall />);
  const family = (await screen.findByRole('heading', { name: 'Rated online victories' })).closest(
    'li',
  )!;
  expect(family.querySelector('.achievement-rune')).toHaveTextContent('ᛏ');
});
it('selects a portrait using keyboard controls and applies the server result immediately', async () => {
  const user = userEvent.setup();
  render(<Hall />);
  const select = await screen.findByRole('button', { name: 'Select portrait: Skald' });
  select.focus();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(card('Skald')).toHaveAttribute('data-portrait-state', 'selected'));
  expect(api).toHaveBeenCalledWith(
    '/profile/portrait',
    { portrait_id: 'skald' },
    'PATCH',
    expect.any(AbortSignal),
  );
  expect(notify.success).toHaveBeenCalledWith('Portrait selected.');
});
it('retains a successful selection even if the followup refresh fails', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let fail = false;
  vi.mocked(api).mockImplementation((path, ...rest) => {
    if (path === '/profile/portrait') {
      fail = true;
      return original(path, ...rest);
    }
    if (fail && path === '/personal/achievements') return Promise.reject(new Error('Offline'));
    return original(path, ...rest);
  });
  render(<Hall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  await screen.findByRole('alert');
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'selected');
  expect(notify.success).toHaveBeenCalledWith('Portrait selected.');
});
it('recovers a lost selection response from the authoritative refresh', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) => {
    if (path === '/profile/portrait') {
      selected = 'skald';
      return Promise.reject(new Error('Lost response'));
    }
    return original(path, ...rest);
  });
  const changed = vi.fn();
  window.addEventListener('profile-changed', changed);
  try {
    render(<Hall />);
    fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
    await waitFor(() => expect(card('Skald')).toHaveAttribute('data-portrait-state', 'selected'));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(notify.success).toHaveBeenCalledTimes(1);
    // A profile-dependent consumer receives exactly one confirmation event for
    // the recovered operation, matching the normal successful selection path.
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    await act(async () => {});
    expect(changed).toHaveBeenCalledTimes(1);
  } finally {
    window.removeEventListener('profile-changed', changed);
  }
});
it('aborts old-account mutations and ignores late results and notices', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let finish: (v: unknown) => void = () => {};
  let signal: AbortSignal | undefined;
  vi.mocked(api).mockImplementation((path, body, method, requestSignal) => {
    if (path === '/profile/portrait') {
      signal = requestSignal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    }
    return original(path, body, method, requestSignal);
  });
  const view = render(<Hall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  identity.id = 'keeper';
  progress = fixture();
  progress.unlocked_portrait_ids = [];
  view.rerender(<Hall />);
  await waitFor(() => expect(signal?.aborted).toBe(true));
  await act(async () => finish({ portrait_id: 'skald' }));
  expect(notify.success).not.toHaveBeenCalled();
  expect(card('Shieldbearer')).toHaveAttribute('data-portrait-state', 'selected');
});
it('aborts stale reads on account switch and never restores old entitlements', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let finish: (v: unknown) => void = () => {};
  let old = true;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path === '/personal/achievements' && old
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : original(path, ...rest),
  );
  const view = render(<Hall />);
  old = false;
  identity.id = 'keeper';
  progress.unlocked_portrait_ids = [];
  view.rerender(<Hall />);
  await screen.findByRole('heading', { name: 'Shieldbearer' });
  await act(async () => finish(fixture()));
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'locked');
});
it('provides loading, empty, error and retry states without reward writes', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let failed = true;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path === '/personal/achievements' && failed
      ? Promise.reject(new Error('Unavailable'))
      : original(path, ...rest),
  );
  render(<Hall />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading');
  await screen.findByRole('alert');
  failed = false;
  progress.items = [];
  progress.unlocked_portrait_ids = [];
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByRole('heading', { name: 'Shieldbearer' });
  expect(screen.getByText('Achievement Score — 0 / 0 points')).toBeVisible();
  expect(screen.getByText('0% complete · 0 / 0 milestones earned')).toBeVisible();
});
it('uses high-resolution gallery art while preserving original small avatars and safe fallbacks', () => {
  const view = render(<Portrait id="shieldbearer" gallery name="Large" />);
  expect(screen.getByRole('img')).toHaveAttribute('width', '1024');
  expect(screen.getByRole('img')).toHaveAttribute(
    'src',
    '/assets/arena-v2/assets/portraits/shieldbearer-large.webp',
  );
  view.rerender(<Portrait id="unknown-url" name="Small" />);
  expect(screen.getByRole('img')).toHaveAttribute('width', '320');
  expect(screen.getByRole('img')).toHaveAttribute(
    'src',
    '/assets/arena-v2/assets/portraits/shieldbearer.webp',
  );
  fireEvent.error(screen.getByRole('img'));
  expect(screen.getByRole('img').tagName).toBe('SPAN');
});
