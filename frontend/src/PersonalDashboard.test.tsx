import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PersonalDashboard from './PersonalDashboard';
import fixture from './onlineFixture.testdata.json';
import { setLanguage } from './i18n';
const identity = vi.hoisted(() => ({ id: 'archer' }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
const summary = {
  id: 'local-owned',
  category: 'account-single-player',
  ruleset: 'brandubh',
  mode: 'human-human',
  created_at: '2026-01-01T00:00:00Z',
  ended_at: null,
  status: 'in_progress',
  winner: null,
  plies: 1,
  players: {
    attackers: { name: 'Archer', kind: 'human' },
    defenders: { name: 'Keeper', kind: 'human' },
  },
};
const totals = {
  total_games: 1,
  completed_games: 0,
  in_progress: 1,
  attackers_wins: 0,
  defenders_wins: 0,
  draws: 0,
  average_game_length: 0,
};
const local = () => ({
  category: 'account-single-player',
  games: [summary],
  totals,
  limit: 10,
  matching: 11,
  next_cursor: 'next-local',
});
const online = () => ({
  category: 'online',
  games: [],
  totals: { category: 'online', played: 0, wins: 0, losses: 0, draws: 0, abandoned_games: 0 },
  overview: { your_turn: 0, other_active: 0, waiting: 0, completed: 0, cancelled: 0, voided: 0 },
  as_of: '2026-01-01T00:00:00Z',
  limit: 10,
  matching: 0,
  next_cursor: null,
});
const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);
let serve: (path: string, opts?: RequestInit) => Promise<Response>;
const fetcher = vi.fn((path: string, opts?: RequestInit) => serve(path, opts));
beforeEach(() => {
  identity.id = 'archer';
  history.replaceState(null, '', '/dashboard');
  setLanguage('en', false);
  fetcher.mockClear();
  serve = (path) => respond(path.includes('/single-player?') ? local() : online());
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
  setLanguage('en', false);
});
it('separates categories and totals, paginates/filters on the server and never opens guest storage', async () => {
  const open = vi.fn();
  vi.stubGlobal('indexedDB', { open });
  render(<PersonalDashboard />);
  await screen.findByRole('link', { name: 'Review account game' });
  expect(screen.getByRole('heading', { name: 'Account single-player' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Online multiplayer' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Guest play on this device' })).toHaveAttribute(
    'href',
    '/guest',
  );
  const account = screen
    .getByRole('heading', { name: 'Account single-player' })
    .closest('section')!;
  fireEvent.click(within(account).getByRole('button', { name: 'Next page' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([path]) => path.includes('cursor=next-local'))).toBe(true),
  );
  fireEvent.change(within(account).getByLabelText('Ruleset'), { target: { value: 'tablut' } });
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([path]) => path.includes('ruleset=tablut'))).toBe(true),
  );
  expect(location.search).not.toContain('sc=');
  expect(
    fetcher.mock.calls.every(
      ([path, opts]) =>
        path.startsWith('/api/personal/') && (!opts?.method || opts.method === 'GET'),
    ),
  ).toBe(true);
  expect(open).not.toHaveBeenCalled();
});
it('shows loading, error/retry, then empty records', async () => {
  let finish!: (response: Response) => void;
  serve = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  render(<PersonalDashboard />);
  expect(screen.getAllByText('Loading your games…')).toHaveLength(2);
  await act(async () => {
    finish(await respond({}, 503));
  });
  await screen.findByRole('button', { name: 'Retry' });
  serve = () => respond(online());
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('No online games match these filters.');
});
it('aborts the old account and rejects a late response even if fetch ignores cancellation', async () => {
  let finish!: (response: Response) => void;
  serve = (path) =>
    path.includes('/single-player?')
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : respond(online());
  const view = render(<PersonalDashboard />);
  const signals = fetcher.mock.calls.map(([, opts]) => opts?.signal as AbortSignal);
  identity.id = 'keeper';
  serve = (path) =>
    respond(
      path.includes('/single-player?')
        ? { ...local(), games: [], matching: 0, next_cursor: null }
        : online(),
    );
  view.rerender(<PersonalDashboard />);
  await screen.findByText('No account games match these filters.');
  await act(async () => {
    finish(await respond(local()));
  });
  expect(signals.every((s) => s.aborted)).toBe(true);
  expect(screen.queryByRole('link', { name: 'Review account game' })).toBeNull();
});
it('reviews canonical account replay with viewer-only controls and no write or analysis adapter', async () => {
  history.replaceState(null, '', '/dashboard/single-player/local-owned');
  serve = () =>
    respond({
      game: summary,
      game_id: summary.id,
      version: 9,
      validated: true,
      cursor: 0,
      moves: fixture.replay.game.moves,
      states: fixture.replay.states,
    });
  render(<PersonalDashboard />);
  await screen.findByRole('grid');
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  fireEvent.click(screen.getAllByRole('gridcell')[1]);
  fireEvent.click(screen.getByRole('button', { name: 'Previous move' }));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('/api/personal/single-player/local-owned/replay');
  expect(screen.queryByRole('button', { name: /analysis/i })).toBeNull();
});
it('translates personal controls in German', async () => {
  setLanguage('de', false);
  render(<PersonalDashboard />);
  await screen.findByRole('link', { name: 'Kontospiel ansehen' });
  expect(screen.getByRole('heading', { name: 'Deine Spiele' })).toBeVisible();
});
