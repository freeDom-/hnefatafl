import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import type { Game } from './types';
import { navigate } from './navigation';
import { setLanguage } from './i18n';

vi.mock('./Auth', () => ({
  useAuth: () => ({ account: { id: 'me', username: 'Archer', permissions: [] }, logout: vi.fn() }),
}));
const initial: Game = {
  id: 'identity-game',
  ruleset: 'brandubh',
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
    attackers: { name: 'Thor', username: 'Archer', account_id: 'me', kind: 'human', config: {} },
    defenders: { name: 'Historical human', kind: 'human', config: {} },
  },
  version: 0,
  captured: { attackers: 0, defenders: 0 },
  created_at: '',
};
let current: Game;
const respond = (body: unknown) =>
  Promise.resolve({ ok: true, json: async () => body } as Response);
const fetcher = vi.fn<(path: string, options?: RequestInit) => Promise<Response>>();
beforeEach(() => {
  current = structuredClone(initial);
  history.replaceState(null, '', '/games/identity-game');
  localStorage.clear();
  setLanguage('en', false);
  fetcher.mockReset();
  fetcher.mockImplementation((path) => {
    if (path === '/api/rulesets') return respond([{ id: 'brandubh', name: 'Brandubh', size: 3 }]);
    if (path === '/api/ai/profiles' || path === '/api/models') return respond([]);
    if (path === '/api/games/identity-game') return respond(current);
    if (path.endsWith('/analytics'))
      return respond({ advisor: null, time_limit: 5, show_win_chances: true, revision: 0 });
    if (path.endsWith('/analysis'))
      return respond({ cursor: 0, evaluations: [], hint: null, status: 'complete' });
    if (path.endsWith('/moves')) return respond(current);
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const reads = () => fetcher.mock.calls.filter(([path]) => path === '/api/games/identity-game');
function renamed(name: string): Game {
  return {
    ...current,
    players: { ...current.players, attackers: { ...current.players.attackers, name } },
  };
}

it.each([false, true])(
  'shows current canonical attribution and refreshes mounted names on focus (mobile: %s)',
  async (mobile) => {
    vi.stubGlobal('matchMedia', () => ({
      matches: mobile,
      addEventListener() {},
      removeEventListener() {},
    }));
    render(<App />);
    await screen.findByRole('grid');
    expect(screen.getByRole('heading', { name: 'Thor' })).toBeVisible();
    expect(screen.getByText(/\(@Archer\)/)).toBeVisible();
    expect(reads()).toHaveLength(1);
    current = renamed('Freya Þór 😀');
    fireEvent(window, new Event('focus'));
    await screen.findByRole('heading', { name: 'Freya Þór 😀' });
    expect(reads()).toHaveLength(2);
    current = renamed('Archer');
    fireEvent(window, new Event('profile-changed'));
    await screen.findByRole('heading', { name: 'Archer' });
    expect(screen.queryByText(/\(@Archer\)/)).toBeNull();
    expect(screen.getByRole('heading', { name: 'Historical human' })).toBeVisible();
  },
);

it('coalesces concurrent wakes, discards a superseded identity read and aborts on unmount', async () => {
  let resolve!: (value: Response) => void;
  const pending = new Promise<Response>((done) => {
    resolve = done;
  });
  const original = fetcher.getMockImplementation()!;
  render(<App />);
  await screen.findByRole('grid');
  fetcher.mockImplementation((path, options) =>
    path === '/api/games/identity-game' && reads().length === 2 ? pending : original(path, options),
  );
  fireEvent(window, new Event('focus'));
  const signal = reads()[1][1]!.signal!;
  current = renamed('Current shared identity');
  fireEvent(window, new Event('profile-changed'));
  fireEvent(window, new Event('online'));
  expect(reads()).toHaveLength(2);
  await act(async () => resolve(await respond(renamed('Superseded name'))));
  await screen.findByRole('heading', { name: 'Current shared identity' });
  expect(screen.queryByRole('heading', { name: 'Superseded name' })).toBeNull();
  expect(reads()).toHaveLength(3);
  cleanup();
  expect(signal.aborted).toBe(true);
  fireEvent(window, new Event('focus'));
  fireEvent(window, new Event('profile-changed'));
  expect(reads()).toHaveLength(3);
});

it('keeps a successful move when an older identity read finishes later', async () => {
  let resolve!: (value: Response) => void;
  const pending = new Promise<Response>((done) => {
    resolve = done;
  });
  const original = fetcher.getMockImplementation()!;
  render(<App />);
  await screen.findByRole('grid');
  const old = current;
  fetcher.mockImplementation((path, options) =>
    path === '/api/games/identity-game' ? pending : original(path, options),
  );
  fireEvent(window, new Event('focus'));
  current = {
    ...renamed('Freya'),
    version: 1,
    turn: 'defenders',
    cursor: 1,
    total_moves: 1,
    board: ['.', 'A', '.', '.', 'K', '.', '.', '.', 'D'],
    moves: [{ source: 0, target: 1, side: 'attackers' }],
    legal_moves: [],
  };
  fireEvent.click(screen.getByRole('gridcell', { name: 'a3, attacker' }));
  fireEvent.click(screen.getByRole('gridcell', { name: 'b3, empty, legal move' }));
  await screen.findByRole('gridcell', { name: 'b3, attacker' });
  await act(async () => resolve(await respond(old)));
  expect(screen.getByRole('gridcell', { name: 'b3, attacker' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Freya' })).toBeVisible();
  expect(fetcher.mock.calls.find(([path]) => path.endsWith('/moves'))?.[1]?.body).toContain(
    '"version":0',
  );
});

it('aborts an in-flight identity read and ignores its late response after unmount', async () => {
  let resolve!: (value: Response) => void;
  const pending = new Promise<Response>((done) => {
    resolve = done;
  });
  const original = fetcher.getMockImplementation()!;
  const view = render(<App />);
  await screen.findByRole('grid');
  fetcher.mockImplementation((path, options) =>
    path === '/api/games/identity-game' ? pending : original(path, options),
  );
  fireEvent(window, new Event('focus'));
  const signal = reads()[1][1]!.signal!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(await respond(renamed('Late name'))));
  fireEvent(window, new Event('profile-changed'));
  expect(reads()).toHaveLength(2);
  expect(screen.queryByRole('heading', { name: 'Late name' })).toBeNull();
});

it('retains names after a failed refresh and retries on navigation back to the game', async () => {
  const original = fetcher.getMockImplementation()!;
  render(<App />);
  await screen.findByRole('grid');
  fetcher.mockImplementation((path, options) =>
    path === '/api/games/identity-game'
      ? Promise.reject(new Error('offline'))
      : original(path, options),
  );
  await act(async () => fireEvent(window, new Event('focus')));
  expect(screen.getByRole('heading', { name: 'Thor' })).toBeVisible();
  await act(async () => navigate('/play'));
  current = renamed('Freya');
  fetcher.mockImplementation(original);
  await act(async () => navigate('/games/identity-game'));
  await screen.findByRole('heading', { name: 'Freya' });
  await waitFor(() => expect(reads()).toHaveLength(3));
});
