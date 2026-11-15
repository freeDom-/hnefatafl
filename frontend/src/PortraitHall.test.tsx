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
  summary: { unlocked: 1, total: 3 },
  items: [
    {
      id: 'first-rated-win',
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
    if (path === '/personal/achievements') return achievements as never;
    if (path === '/profile') return { portrait_id: selected } as never;
    if (path === '/personal/achievements/confirm' && method === 'POST') {
      achievements = {
        ...achievements,
        items: achievements.items.map((item) =>
          item.progress >= item.threshold
            ? { ...item, unlocked: true, awarded_at: '2035-01-01T00:00:00Z' }
            : item,
        ),
      };
      return achievements as never;
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
  expect(notify.success).toHaveBeenCalledWith('Achievements unlocked: 1');
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
  expect(within(achievement).getByText('Finish a same-device local game.')).toBeVisible();
  expect(within(achievement).getByText('Progress: 1 / 1')).toBeVisible();
  expect(within(achievement).getByRole('progressbar')).toHaveAccessibleName('Progress: 1 / 1');
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
  await waitFor(() => expect(notify.success).toHaveBeenCalledWith('Achievements unlocked: 1'));
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
