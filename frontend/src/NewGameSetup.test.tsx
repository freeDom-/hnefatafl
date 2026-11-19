import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Game } from './types';

/**
 * The setup lifecycle is an application-level concern (route invalidation,
 * account switching, game creation completion), so these tests render the real
 * App with a controllable account and API. `useNewGameSetup.test.tsx` covers the
 * operation primitive directly.
 */
const auth = vi.hoisted(() => ({
  account: {
    id: 'account-odin',
    username: 'Odin',
    permissions: ['play', 'profile', 'training', 'tournaments', 'models', 'all_data'],
  } as { id: string; username: string; permissions: string[] },
}));
vi.mock('./Auth', () => ({
  useAuth: () => ({
    account: auth.account,
    logout: async () => {},
    observeVerification: () => () => {},
  }),
}));

import App from './App';
import { navigate } from './navigation';

const initial: Game = {
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
const createdGame: Game = {
  ...initial,
  id: 'g2',
  board: ['.', '.', 'A', '.', 'K', '.', '.', '.', 'D'],
  legal_moves: [{ source: 2, target: 1 }],
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
const profiles = [
  {
    id: 'baseline',
    name: 'Shield bearer',
    algorithm: 'baseline',
    parameters: { time_limit: 1 },
    supported_rulesets: ['tablut'],
  },
];
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);
type Gate = {
  resolve: (value: Response | Promise<Response>) => void;
  reject: (reason: unknown) => void;
};
type Control = {
  deferRules: boolean;
  deferProfiles: boolean;
  deferCreate: boolean;
  rulesGates: Gate[];
  profileGates: Gate[];
  createGates: Gate[];
};
const control: Control = {
  deferRules: false,
  deferProfiles: false,
  deferCreate: false,
  rulesGates: [],
  profileGates: [],
  createGates: [],
};
const gate = (gates: Gate[]) =>
  new Promise<Response>((resolve, reject) => gates.push({ resolve, reject }));

function installFetcher() {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path === '/api/rulesets')
      return control.deferRules ? gate(control.rulesGates) : json(rules);
    if (path === '/api/ai/profiles')
      return control.deferProfiles ? gate(control.profileGates) : json(profiles);
    if (path === '/api/models') return json([]);
    if (path === '/api/games' && options?.method === 'POST')
      return control.deferCreate ? gate(control.createGates) : json(createdGame);
    if (path === '/api/games/g1' && options?.method === 'GET') return json(initial);
    if (/^\/api\/games\/g[12]\/analytics$/.test(path))
      return json({ advisor: null, time_limit: 5, show_win_chances: true, revision: 0 });
    if (/^\/api\/games\/g[12]\/analysis$/.test(path))
      return json({ cursor: 0, evaluations: [], hint: null, status: 'complete' });
    if (path === '/api/games/g1/replay') return json({ validated: true, states: [initial] });
    // Dashboard reads: the setup flow only needs the option endpoints above,
    // but the Dashboard mounts and settles its own polls on the same route.
    if (path.startsWith('/api/personal/single-player'))
      return json({ games: [], totals: { total_games: 0, in_progress: 0 } });
    if (path.startsWith('/api/personal/online'))
      return json({
        games: [],
        overview: { your_turn: 0, other_active: 0, waiting: 0 },
        statistics: { groups: [] },
      });
    if (path.startsWith('/api/personal/local'))
      return json({ games: [], totals: { total_games: 0, in_progress: 0 } });
    if (path.startsWith('/api/history/games'))
      return json({ games: [], totals: [], matching: 0, limit: 3, next_cursor: null });
    if (path === '/api/personal/progress') return json({ streaks: { by_ruleset: [] } });
    if (path === '/api/personal/achievements')
      return json({ items: [], summary: { unlocked: 0, total: 0 } });
    if (path === '/api/portraits/catalog') return json({ default_portrait_id: null, items: [] });
    if (path === '/api/profile')
      return json({ username: 'Odin', email_verified: true, portrait_id: null });
    if (path.startsWith('/api/leaderboard'))
      return json({ ruleset: 'tablut', entries: [], total: 0, limit: 1, offset: 0, me: null });
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

const dialog = () => screen.queryByRole('dialog', { name: 'Begin a new saga' });

async function renderAtDashboard(query = '') {
  history.replaceState(null, '', `/dashboard${query}`);
  render(<App />);
  await screen.findByRole('button', { name: 'Play vs AI' });
}

async function resolveLatestOptions() {
  const ruleGate = control.rulesGates.at(-1)!;
  const profileGate = control.profileGates.at(-1)!;
  await act(async () => {
    ruleGate.resolve(json(rules));
    profileGate.resolve(json(profiles));
  });
  await screen.findByRole('dialog', { name: 'Begin a new saga' });
}

beforeEach(() => {
  auth.account = {
    id: 'account-odin',
    username: 'Odin',
    permissions: ['play', 'profile', 'training', 'tournaments', 'models', 'all_data'],
  };
  control.deferRules = false;
  control.deferProfiles = false;
  control.deferCreate = false;
  control.rulesGates = [];
  control.profileGates = [];
  control.createGates = [];
  history.replaceState(null, '', '/games/g1');
  localStorage.clear();
  localStorage.setItem('hnefatafl-game', 'g1');
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('opens one dialog when Play vs AI is clicked repeatedly while options are pending', async () => {
  control.deferRules = true;
  control.deferProfiles = true;
  installFetcher();
  await renderAtDashboard();
  const play = screen.getByRole('button', { name: 'Play vs AI' });
  fireEvent.click(play);
  fireEvent.click(play);
  // Each click supersedes the previous request rather than racing it.
  expect(control.rulesGates).toHaveLength(2);
  await resolveLatestOptions();
  expect(screen.getAllByRole('dialog', { name: 'Begin a new saga' })).toHaveLength(1);
});

it('lets the newest overlapping options win when responses arrive in reverse order', async () => {
  control.deferRules = true;
  control.deferProfiles = true;
  installFetcher();
  await renderAtDashboard();
  const play = screen.getByRole('button', { name: 'Play vs AI' });
  fireEvent.click(play);
  fireEvent.click(play);
  await resolveLatestOptions();
  // The superseded first request resolving afterwards must not replace or
  // reopen anything.
  await act(async () => {
    control.rulesGates[0].resolve(json(rules));
    control.profileGates[0].resolve(json(profiles));
  });
  expect(screen.getAllByRole('dialog', { name: 'Begin a new saga' })).toHaveLength(1);
});

it('does not open a dialog when the route changes while options are pending', async () => {
  control.deferRules = true;
  control.deferProfiles = true;
  installFetcher();
  await renderAtDashboard();
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  await act(async () => {
    navigate('/statistics');
  });
  await act(async () => {
    control.rulesGates[0].resolve(json(rules));
    control.profileGates[0].resolve(json(profiles));
  });
  await waitFor(() => expect(dialog()).toBeNull());
  expect(new URL(location.href).pathname).toBe('/statistics');
});

it('ignores an obsolete response after navigating away and back to the same route', async () => {
  control.deferRules = true;
  control.deferProfiles = true;
  installFetcher();
  await renderAtDashboard('?setup=single-player');
  await act(async () => {
    navigate('/statistics');
  });
  await act(async () => {
    navigate('/dashboard?setup=single-player');
  });
  // The first operation is obsolete; only the second may open the dialog.
  await act(async () => {
    control.rulesGates[0].resolve(json(rules));
    control.profileGates[0].resolve(json(profiles));
  });
  await waitFor(() => expect(dialog()).toBeNull());
  await resolveLatestOptions();
  expect(dialog()).toBeVisible();
});

it('reopens setup explicitly after cancellation', async () => {
  installFetcher();
  await renderAtDashboard();
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  const first = await screen.findByRole('dialog', { name: 'Begin a new saga' });
  fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
  await waitFor(() => expect(dialog()).toBeNull());
  control.deferRules = true;
  control.deferProfiles = true;
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  await resolveLatestOptions();
  expect(dialog()).toBeVisible();
  expect(first).not.toBeInTheDocument();
});

it('does not navigate to a game whose creation completes after navigation', async () => {
  installFetcher();
  await renderAtDashboard();
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  await screen.findByRole('button', { name: 'To the table' });
  control.deferCreate = true;
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(control.createGates).toHaveLength(1));
  await act(async () => {
    navigate('/statistics');
  });
  await act(async () => {
    control.createGates[0].resolve(json(createdGame));
  });
  await waitFor(() => expect(dialog()).toBeNull());
  expect(new URL(location.href).pathname).toBe('/statistics');
  expect(screen.queryByRole('grid')).toBeNull();
});

it('lets a newer setup dialog survive a late completion from a dismissed one', async () => {
  installFetcher();
  await renderAtDashboard();
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  await screen.findByRole('button', { name: 'To the table' });
  control.deferCreate = true;
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(control.createGates).toHaveLength(1));
  // Dismiss the first dialog while its POST is still pending, then open another.
  fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
  await waitFor(() => expect(dialog()).toBeNull());
  control.deferRules = true;
  control.deferProfiles = true;
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  await resolveLatestOptions();
  await act(async () => {
    control.createGates[0].resolve(json(createdGame));
  });
  expect(dialog()).toBeVisible();
  expect(new URL(location.href).pathname).toBe('/dashboard');
});

it('invalidates a pending setup when the account changes', async () => {
  control.deferRules = true;
  control.deferProfiles = true;
  installFetcher();
  history.replaceState(null, '', '/dashboard');
  const view = render(<App />);
  await screen.findByRole('button', { name: 'Play vs AI' });
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  expect(control.rulesGates).toHaveLength(1);
  auth.account = { ...auth.account, id: 'account-freyja' };
  await act(async () => {
    view.rerender(<App />);
  });
  await act(async () => {
    control.rulesGates[0].resolve(json(rules));
    control.profileGates[0].resolve(json(profiles));
  });
  await waitFor(() => expect(dialog()).toBeNull());
});

it('creates a game through query-driven setup and navigates to the board', async () => {
  installFetcher();
  await renderAtDashboard('?setup=single-player');
  const setup = await screen.findByRole('dialog', { name: 'Begin a new saga' });
  fireEvent.click(setup.querySelector('button.primary')!);
  await waitFor(() => expect(new URL(location.href).pathname).toBe('/games/g2'));
  expect(await screen.findByRole('grid')).toBeVisible();
});
