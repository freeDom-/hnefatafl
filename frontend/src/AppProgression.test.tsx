import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { PROGRESSION_POLL_MS } from './useAchievementNotifications';
import type { Game } from './types';

vi.mock('./Auth', () => ({
  useAuth: () => ({
    account: { id: 'archer', permissions: ['play'], email_verified: true },
    logout: vi.fn(),
  }),
}));
vi.mock('./notifications', () => ({ notify: { success: vi.fn(), error: vi.fn() } }));

const game: Game = {
  id: 'g1',
  ruleset: 'tablut',
  size: 3,
  board: ['A', '.', '.', '.', 'K', '.', '.', '.', 'D'],
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
  moves: [],
  cursor: 0,
  total_moves: 0,
  legal_moves: [{ source: 0, target: 1 }],
  mode: 'human-human',
  players: {
    attackers: { name: 'Astrid', kind: 'human', config: {} },
    defenders: { name: 'Erik', kind: 'human', config: {} },
  },
  version: 0,
  captured: { attackers: 0, defenders: 0 },
  created_at: '',
};
const rules = [
  {
    id: 'tablut',
    name: 'Tablut',
    size: 9,
    description: 'Help the king escape.',
    rules_markdown: '# Tablut\nMove in straight lines.',
  },
];
const progress = (earned: boolean) => ({
  catalog_version: 3,
  portrait_catalog_version: 2,
  as_of: '2035-01-01T00:00:00Z',
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
          awarded_at: '2035-01-01T00:00:00Z',
          portrait_id: 'shieldmaiden',
        },
      ]
    : [],
});
const json = (value: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: async () => value } as Response);

beforeEach(() => {
  history.replaceState(null, '', '/games/g1');
  localStorage.clear();
  localStorage.setItem('hnefatafl-game', 'g1');
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
});

it('reconciles a remote rated completion while a local game route stays open', async () => {
  vi.useFakeTimers();
  let earned = false;
  const fetcher = vi.fn((path: string) => {
    if (path === '/api/rulesets') return json(rules);
    if (path === '/api/ai/profiles') return json([]);
    if (path === '/api/models') return json([]);
    if (path === '/api/games/g1') return json(game);
    if (path === '/api/games/g1/analytics')
      return json({ advisor: null, time_limit: 5, show_win_chances: true, revision: 0 });
    if (path === '/api/games/g1/analysis')
      return json({ cursor: 0, evaluations: [], hint: null, status: 'complete' });
    if (path === '/api/personal/achievements') return json(progress(earned));
    return json({});
  });
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(screen.getByRole('grid')).toBeVisible();
  expect(location.pathname).toBe('/games/g1');
  const achievementReads = () =>
    fetcher.mock.calls.filter(([path]) => path === '/api/personal/achievements').length;
  expect(achievementReads()).toBe(1);
  const { notify } = await import('./notifications');
  expect(notify.success).not.toHaveBeenCalled();

  // The opponent finishes a separate rated game while this local board stays open.
  earned = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(PROGRESSION_POLL_MS);
  });

  expect(location.pathname).toBe('/games/g1');
  expect(notify.success).toHaveBeenCalledWith('New milestones: 1 · +30 points');
  expect(notify.success).toHaveBeenCalledWith('New portraits available: 1');
  expect(notify.success).toHaveBeenCalledTimes(2);
  expect(achievementReads()).toBeGreaterThan(1);
});
