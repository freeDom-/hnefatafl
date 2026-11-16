import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PortraitHall from './PortraitHall';
import Portrait from './Portrait';
import { api } from './api';
import { notify } from './notifications';
import { setLanguage } from './i18n';
import type { AchievementProgress, PortraitCatalog } from './portraitTypes';

const identity = vi.hoisted(() => ({ id: 'archer' }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
vi.mock('./api', () => ({ api: vi.fn(), errorMessage: (error: Error) => error.message }));
vi.mock('./notifications', () => ({ notify: { success: vi.fn(), error: vi.fn() } }));
const catalog: PortraitCatalog = {
  catalog_version: 1,
  default_portrait_id: 'shieldbearer',
  items: [
    { id: 'shieldbearer', name: 'Shieldbearer', unlock_achievement_id: null },
    { id: 'shieldmaiden', name: 'Shieldmaiden', unlock_achievement_id: 'first-rated-win' },
    { id: 'iron-helm', name: 'Iron Helm', unlock_achievement_id: 'defender-hold' },
    { id: 'skald', name: 'Skald', unlock_achievement_id: 'tablut-initiate' },
  ],
};
const initial: AchievementProgress = {
  catalog_version: 1,
  portrait_catalog_version: 1,
  as_of: '2035-01-01T00:00:00Z',
  summary: { unlocked: 1, total: 3, earned_points: 10, total_points: 30, claimable_points: 10 },
  families: [],
  items: [
    {
      id: 'first-rated-win',
      family_id: 'rated-wins',
      tier: 1,
      points: 10,
      name: 'First Raid',
      description: 'Win your first rated online game.',
      category: 'rated',
      scope: null,
      threshold: 1,
      progress: 1,
      unlocked: false,
      awarded_at: null,
      portrait_id: 'shieldmaiden',
    },
    {
      id: 'defender-hold',
      family_id: 'defender-wins',
      tier: 1,
      points: 10,
      name: 'Shield Wall',
      description: 'Win a rated online game as the defender.',
      category: 'rated',
      scope: null,
      threshold: 1,
      progress: 0,
      unlocked: false,
      awarded_at: null,
      portrait_id: 'iron-helm',
    },
    {
      id: 'tablut-initiate',
      family_id: 'tablut-wins',
      tier: 1,
      points: 10,
      name: 'Tablut Initiate',
      description: 'Win a rated Tablut game.',
      category: 'ruleset',
      scope: 'tablut',
      threshold: 1,
      progress: 0,
      unlocked: true,
      awarded_at: '2034-01-01T00:00:00Z',
      portrait_id: 'skald',
    },
  ],
};
function project(value: AchievementProgress): AchievementProgress {
  return {
    ...value,
    summary: {
      ...value.summary,
      total: value.items.length,
      unlocked: value.items.filter((item) => item.unlocked).length,
      earned_points: value.items
        .filter((item) => item.unlocked)
        .reduce((sum, item) => sum + item.points, 0),
      total_points: value.items.reduce((sum, item) => sum + item.points, 0),
      claimable_points: value.items
        .filter((item) => !item.unlocked && item.progress >= item.threshold)
        .reduce((sum, item) => sum + item.points, 0),
    },
    families: [...new Set(value.items.map((item) => item.family_id))].map((id) => {
      const milestones = value.items.filter((item) => item.family_id === id);
      const collected = milestones.filter((item) => item.unlocked);
      return {
        id,
        name: milestones[0].name,
        progress: milestones[0].progress,
        collected_tier: Math.max(0, ...collected.map((item) => item.tier)),
        collected_threshold: Math.max(0, ...collected.map((item) => item.threshold)),
        achieved_threshold: Math.max(
          0,
          ...milestones
            .filter((item) => item.progress >= item.threshold)
            .map((item) => item.threshold),
        ),
        total_tiers: milestones.length,
        collected_count: collected.length,
        next_milestone: milestones.find((item) => !item.unlocked) ?? null,
        claimable_points: milestones
          .filter((item) => !item.unlocked && item.progress >= item.threshold)
          .reduce((sum, item) => sum + item.points, 0),
        complete: collected.length === milestones.length,
        milestones,
      };
    }),
  };
}
let achievements: AchievementProgress;
let selected: string;
beforeEach(() => {
  identity.id = 'archer';
  selected = 'shieldbearer';
  achievements = structuredClone(initial);
  vi.clearAllMocks();
  setLanguage('en');
  vi.mocked(api).mockImplementation(async (path, _data, method) => {
    if (path === '/portraits/catalog') return catalog as never;
    if (path === '/personal/achievements') return project(achievements) as never;
    if (path === '/profile') return { portrait_id: selected } as never;
    if (path === '/personal/achievements/confirm' && method === 'POST') {
      achievements = {
        ...achievements,
        items: achievements.items.map((item) =>
          item.progress >= item.threshold &&
          (!(_data as { family_id?: string })?.family_id ||
            item.family_id === (_data as { family_id?: string }).family_id)
            ? { ...item, unlocked: true, awarded_at: '2035-01-01T00:00:00Z' }
            : item,
        ),
      };
      return project(achievements) as never;
    }
    if (path === '/profile/portrait' && method === 'PATCH') {
      selected = (_data as { portrait_id: string }).portrait_id;
      return { portrait_id: selected } as never;
    }
    throw new Error(`Unexpected API ${path}`);
  });
});
afterEach(cleanup);
function card(name: string) {
  return screen.getByRole('heading', { name }).closest('li')!;
}
it('uses server states and actual progress, preserving earned portraits after a correction', async () => {
  render(<PortraitHall />);
  await screen.findByRole('heading', { name: 'Shieldbearer' });
  expect(card('Shieldbearer')).toHaveAttribute('data-portrait-state', 'selected');
  expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'available');
  expect(card('Iron Helm')).toHaveAttribute('data-portrait-state', 'locked');
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'earned');
  expect(within(card('Shieldmaiden')).getByText('Progress: 1 / 1')).toBeVisible();
  expect(within(card('Skald')).getByText('Progress: 0 / 1')).toBeVisible();
  expect(api).not.toHaveBeenCalledWith(
    '/personal/achievements/confirm',
    expect.anything(),
    expect.anything(),
    expect.anything(),
  );
});
it('confirms on the server before selection, announces only new awards and reuses the profile response', async () => {
  const changed = vi.fn();
  window.addEventListener('profile-changed', changed);
  const user = userEvent.setup();
  render(<PortraitHall />);
  const claim = await screen.findByRole('button', { name: 'Claim eligible achievements' });
  await user.click(claim);
  await waitFor(() =>
    expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'earned'),
  );
  expect(api).toHaveBeenCalledWith(
    '/personal/achievements/confirm',
    {},
    'POST',
    expect.any(AbortSignal),
  );
  expect(notify.success).toHaveBeenCalledWith('Collected 1 milestones · +10 points');
  expect(claim).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Select portrait: Shieldmaiden' }));
  await waitFor(() =>
    expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'selected'),
  );
  expect(api).toHaveBeenCalledWith(
    '/profile/portrait',
    { portrait_id: 'shieldmaiden' },
    'PATCH',
    expect.any(AbortSignal),
  );
  expect(changed).toHaveBeenCalledTimes(2);
  window.removeEventListener('profile-changed', changed);
});
it('keeps confirmation idempotent when the server returns previously earned awards only', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path === '/personal/achievements/confirm'
      ? Promise.resolve(achievements as never)
      : original(path, ...rest),
  );
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Claim eligible achievements' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/personal/achievements/confirm',
      {},
      'POST',
      expect.any(AbortSignal),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Claim eligible achievements' })).toBeEnabled(),
  );
  expect(notify.success).not.toHaveBeenCalled();
});
it('reports a refused claim without inventing an unlock and permits retry', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path === '/personal/achievements/confirm'
      ? Promise.reject(new Error('Progress changed. Reload before retrying.'))
      : original(path, ...rest),
  );
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Claim eligible achievements' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Progress changed. Reload before retrying.',
  );
  expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'available');
  expect(notify.success).not.toHaveBeenCalled();
});
it('aborts an old-account claim and rejects its response and notifications', async () => {
  let finish: (value: AchievementProgress) => void = () => {};
  let signal: AbortSignal | undefined;
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, requestSignal) => {
    if (path === '/personal/achievements/confirm') {
      signal = requestSignal;
      return new Promise((resolve) => {
        finish = resolve as typeof finish;
      });
    }
    return original(path, data, method, requestSignal);
  });
  const view = render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Claim eligible achievements' }));
  identity.id = 'keeper';
  view.rerender(<PortraitHall />);
  expect(signal?.aborted).toBe(true);
  await act(async () =>
    finish({
      ...achievements,
      items: achievements.items.map((item) => ({ ...item, unlocked: true })),
    }),
  );
  await screen.findByRole('heading', { name: 'Shieldmaiden' });
  expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'available');
  expect(notify.success).not.toHaveBeenCalled();
});
it('aborts selection on unmount and ignores late errors', async () => {
  let fail: (error: Error) => void = () => {};
  let signal: AbortSignal | undefined;
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, requestSignal) => {
    if (path === '/profile/portrait') {
      signal = requestSignal;
      return new Promise((_resolve, reject) => {
        fail = reject;
      });
    }
    return original(path, data, method, requestSignal);
  });
  const view = render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => fail(new Error('Delayed failure')));
  expect(notify.success).not.toHaveBeenCalled();
  expect(notify.error).not.toHaveBeenCalled();
});
it('lets keyboard users select earned portraits', async () => {
  const user = userEvent.setup();
  render(<PortraitHall />);
  const button = await screen.findByRole('button', { name: 'Select portrait: Skald' });
  button.focus();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(card('Skald')).toHaveAttribute('data-portrait-state', 'selected'));
});
it('falls back safely for unknown portrait identifiers and failed artwork', () => {
  render(<Portrait id="https://untrusted.test/image.webp" name="Player portrait" />);
  const image = screen.getByRole('img', { name: 'Player portrait' });
  expect(image).toHaveAttribute('src', '/assets/arena-v2/assets/portraits/shieldbearer.webp');
  fireEvent.error(image);
  expect(screen.getByRole('img', { name: 'Player portrait' }).tagName).toBe('SPAN');
});
it('shows an accessible retry when progress cannot load and recovers without claiming', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let failed = true;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    failed && path === '/personal/achievements'
      ? Promise.reject(new Error('Progress unavailable'))
      : original(path, ...rest),
  );
  render(<PortraitHall />);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Portrait progress could not be refreshed.',
  );
  expect(
    screen.queryByRole('button', { name: 'Claim eligible achievements' }),
  ).not.toBeInTheDocument();
  failed = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('button', { name: 'Claim eligible achievements' })).toBeEnabled();
  expect(notify.success).not.toHaveBeenCalled();
});
it('shows progress for achievements without a portrait reward', async () => {
  achievements.items.push({
    id: 'local-first-game',
    family_id: 'local-first-game',
    tier: 1,
    points: 10,
    name: 'Fireside Game',
    description: 'Finish a same-device local game.',
    category: 'local',
    scope: null,
    threshold: 1,
    progress: 1,
    unlocked: false,
    awarded_at: null,
    portrait_id: null,
  });
  render(<PortraitHall />);
  const heading = await screen.findByRole('heading', { name: 'Fireside Game' });
  const achievement = heading.closest('li')!;
  expect(within(achievement).getByText('Recorded progress: 1 · highest achieved: 1')).toBeVisible();
  expect(within(achievement).getByRole('progressbar')).toHaveAccessibleName(
    'Next milestone: 1 · 10 points',
  );
});
it('announces server-confirmed awards after selecting another already-earned portrait', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) => {
    if (path === '/profile/portrait')
      achievements = {
        ...achievements,
        items: achievements.items.map((item) =>
          item.progress >= item.threshold ? { ...item, unlocked: true } : item,
        ),
      };
    return original(path, ...rest);
  });
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  await waitFor(() =>
    expect(notify.success).toHaveBeenCalledWith('Collected 1 milestones · +10 points'),
  );
  expect(notify.success).toHaveBeenCalledWith('Portrait selected.');
  expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'earned');
});

it('keeps a confirmed selection successful when the follow-up award read fails', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let failReads = false;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path === '/profile/portrait' && method === 'PATCH') {
      failReads = true;
      return original(path, data, method, signal);
    }
    if (path === '/personal/achievements' && failReads)
      return Promise.reject(new Error('Awards unavailable'));
    return original(path, data, method, signal);
  });
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  await waitFor(() => expect(card('Skald')).toHaveAttribute('data-portrait-state', 'selected'));
  expect(notify.success).toHaveBeenCalledWith('Portrait selected.');
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Portrait progress could not be refreshed.');
  expect(alert).not.toHaveTextContent('Awards unavailable');
  expect(notify.success).not.toHaveBeenCalledWith(expect.stringContaining('unlocked'));
});

it('recovers automatically when the follow-up award read fails once and then succeeds', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let failNextRead = false;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path === '/profile/portrait' && method === 'PATCH') {
      failNextRead = true;
      achievements = {
        ...achievements,
        items: achievements.items.map((item) =>
          item.progress >= item.threshold ? { ...item, unlocked: true } : item,
        ),
      };
      return original(path, data, method, signal);
    }
    if (path === '/personal/achievements' && failNextRead) {
      failNextRead = false;
      return Promise.reject(new Error('Transient read failure'));
    }
    return original(path, data, method, signal);
  });
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  await waitFor(() =>
    expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'earned'),
  );
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'selected');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(notify.success).toHaveBeenCalledWith('Portrait selected.');
});

it('reports a failed selection without a success notice or local award', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path === '/profile/portrait'
      ? Promise.reject(new Error('Portrait is not earned.'))
      : original(path, ...rest),
  );
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Portrait is not earned.');
  expect(notify.success).not.toHaveBeenCalled();
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'earned');
  expect(card('Shieldbearer')).toHaveAttribute('data-portrait-state', 'selected');
});

it('aborts an old-account selection and rejects its response and notifications', async () => {
  let finish: (value: { portrait_id: string }) => void = () => {};
  let signal: AbortSignal | undefined;
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, requestSignal) => {
    if (path === '/profile/portrait') {
      signal = requestSignal;
      return new Promise((resolve) => {
        finish = resolve as typeof finish;
      });
    }
    return original(path, data, method, requestSignal);
  });
  const view = render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  identity.id = 'keeper';
  view.rerender(<PortraitHall />);
  expect(signal?.aborted).toBe(true);
  await act(async () => finish({ portrait_id: 'skald' }));
  await screen.findByRole('heading', { name: 'Shieldbearer' });
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'earned');
  expect(notify.success).not.toHaveBeenCalled();
});

it('coalesces repeated selection clicks into one request', async () => {
  let finish: (value: { portrait_id: string }) => void = () => {};
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path === '/profile/portrait' && method === 'PATCH')
      return new Promise((resolve) => {
        finish = resolve as typeof finish;
      });
    return original(path, data, method, signal);
  });
  render(<PortraitHall />);
  const button = await screen.findByRole('button', { name: 'Select portrait: Skald' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path, , method]) => path === '/profile/portrait' && method === 'PATCH'),
  ).toHaveLength(1);
  await act(async () => finish({ portrait_id: 'skald' }));
});

it('does not repeat a confirmation while one is already in flight', async () => {
  let finish: (value: AchievementProgress) => void = () => {};
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path === '/personal/achievements/confirm')
      return new Promise((resolve) => {
        finish = resolve as typeof finish;
      });
    return original(path, data, method, signal);
  });
  render(<PortraitHall />);
  const claim = await screen.findByRole('button', { name: 'Claim eligible achievements' });
  fireEvent.click(claim);
  fireEvent.click(claim);
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(
        ([path, , method]) => path === '/personal/achievements/confirm' && method === 'POST',
      ),
  ).toHaveLength(1);
  await act(async () => finish(achievements));
});

it('clears a lost-response claim error once the authoritative refresh confirms the award', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path === '/personal/achievements/confirm') {
      // The server commits the confirmation but the HTTP response is lost.
      achievements = {
        ...achievements,
        items: achievements.items.map((item) =>
          item.progress >= item.threshold
            ? { ...item, unlocked: true, awarded_at: '2035-01-01T00:00:00Z' }
            : item,
        ),
      };
      return Promise.reject(new Error('Network error'));
    }
    return original(path, data, method, signal);
  });
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Claim eligible achievements' }));
  await waitFor(() =>
    expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'earned'),
  );
  expect(notify.success).toHaveBeenCalledWith('Collected 1 milestones · +10 points');
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});

it('clears a lost-response claim error after a later successful retry refresh', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let committed = false;
  let recoveryReads = 0;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path === '/personal/achievements/confirm') {
      committed = true;
      return Promise.reject(new Error('Response lost'));
    }
    if (path === '/personal/achievements' && committed) {
      recoveryReads++;
      if (recoveryReads === 1) return Promise.reject(new Error('Still offline'));
      return Promise.resolve(
        project({
          ...achievements,
          items: achievements.items.map((item) =>
            item.progress >= item.threshold
              ? { ...item, unlocked: true, awarded_at: '2035-01-01T00:00:00Z' }
              : item,
          ),
        }),
      );
    }
    return original(path, data, method, signal);
  });
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Claim eligible achievements' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(card('Shieldmaiden')).toHaveAttribute('data-portrait-state', 'earned'),
  );
  expect(notify.success).toHaveBeenCalledWith('Collected 1 milestones · +10 points');
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});

it('clears a lost-response selection error once the profile refresh shows the selection', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path === '/profile/portrait' && method === 'PATCH') {
      // The server commits the selection but the HTTP response is lost.
      selected = (data as { portrait_id: string }).portrait_id;
      return Promise.reject(new Error('Network error'));
    }
    return original(path, data, method, signal);
  });
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  await waitFor(() => expect(card('Skald')).toHaveAttribute('data-portrait-state', 'selected'));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(notify.success).toHaveBeenCalledWith('Portrait selected.');
});

it('keeps a genuine selection error when the refresh does not show the selection', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path === '/profile/portrait'
      ? Promise.reject(new Error('Portrait is not earned.'))
      : original(path, ...rest),
  );
  render(<PortraitHall />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select portrait: Skald' }));
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent('Portrait is not earned.'),
  );
  expect(card('Skald')).toHaveAttribute('data-portrait-state', 'earned');
  expect(notify.success).not.toHaveBeenCalled();
});

function tieredFixture(progress = 10) {
  const base = { ...initial.items[0], name: 'Rated online victories', progress };
  achievements.items = [
    ...[1, 3, 10, 25].map((threshold, index) => ({
      ...base,
      id: index === 0 ? base.id : `rated-wins-${threshold}`,
      threshold,
      tier: index + 1,
      points: [10, 20, 30, 40][index],
      portrait_id: index === 0 ? base.portrait_id : null,
    })),
    { ...initial.items[1], progress: 1 },
  ];
}
it('groups eligible tiers into one card, collects only that family and updates its score and visual tier', async () => {
  tieredFixture();
  render(<PortraitHall />);
  const family = (await screen.findByRole('heading', { name: 'Rated online victories' })).closest(
    'li',
  )!;
  expect(document.querySelectorAll('[data-family="rated-wins"]')).toHaveLength(1);
  expect(family).toHaveAttribute('data-tier', '0');
  expect(family).toHaveClass('achievement-engraved');
  expect(within(family).getByText('Next milestone: 1 · 10 points')).toBeVisible();
  expect(screen.getByText('Achievement Score — 0 / 110 points')).toBeVisible();
  fireEvent.click(
    within(family).getByRole('button', { name: 'Collect milestones: Rated online victories' }),
  );
  await waitFor(() => expect(family).toHaveAttribute('data-tier', '3'));
  expect(family).toHaveClass('achievement-gold');
  expect(within(family).getByText('Next milestone: 25 · 40 points')).toBeVisible();
  expect(screen.getByText('Achievement Score — 60 / 110 points')).toBeVisible();
  expect(screen.getByText('54.5% complete · 3 / 5 milestones collected')).toBeVisible();
  expect(api).toHaveBeenCalledWith(
    '/personal/achievements/confirm',
    { family_id: 'rated-wins' },
    'POST',
    expect.any(AbortSignal),
  );
  expect(notify.success).toHaveBeenCalledWith('Collected 3 milestones · +60 points');
  expect(document.querySelector('[data-family="defender-wins"]')).toHaveAttribute('data-tier', '0');
});
it('renders full completion without a next milestone or claim action', async () => {
  tieredFixture(25);
  achievements.items = achievements.items.map((item) => ({
    ...item,
    unlocked: true,
    awarded_at: '2035-01-01T00:00:00Z',
  }));
  render(<PortraitHall />);
  const family = (await screen.findByRole('heading', { name: 'Rated online victories' })).closest(
    'li',
  )!;
  expect(family).toHaveClass('achievement-legendary');
  expect(family).toHaveAttribute('data-state', 'complete');
  expect(within(family).getByText('Fully completed')).toBeVisible();
  expect(within(family).queryByRole('progressbar')).not.toBeInTheDocument();
  expect(within(family).queryByRole('button')).not.toBeInTheDocument();
  expect(screen.getByText('100% complete · 5 / 5 milestones collected')).toBeVisible();
  expect(
    screen.getByRole('progressbar', { name: 'Achievement Score — 110 / 110 points' }),
  ).toHaveAttribute('value', '110');
});
it('localizes family milestones and zero score in German', async () => {
  tieredFixture(0);
  achievements.items = achievements.items.map((item) => ({ ...item, progress: 0 }));
  setLanguage('de');
  render(<PortraitHall />);
  await screen.findByRole('heading', { name: 'Gewertete Online-Siege' });
  expect(screen.getByText('Errungenschaftspunkte — 0 / 110 Punkte')).toBeVisible();
  expect(screen.getByText('0% abgeschlossen · 0 / 5 Meilensteine gesammelt')).toBeVisible();
  expect(screen.getAllByText('Nächster Meilenstein: 1 · 10 Punkte')).toHaveLength(2);
});
it('caps only Portrait Hall artwork for fractional high-DPI screens', async () => {
  const previous = window.devicePixelRatio;
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2.5 });
  render(<PortraitHall />);
  await screen.findByRole('heading', { name: 'Shieldbearer' });
  expect(document.querySelector('.portrait-hall')).toHaveStyle('--portrait-art-max: 128px');
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: previous });
});
