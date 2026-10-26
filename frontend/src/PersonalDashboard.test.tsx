import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PersonalDashboard from './PersonalDashboard';
import fixture from './onlineFixture.testdata.json';
import { setLanguage } from './i18n';
const identity = vi.hoisted(() => ({ id: 'archer', permissions: ['play'] }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
const local = {
  games: [],
  totals: {
    total_games: 4,
    completed_games: 1,
    in_progress: 3,
    attackers_wins: 0,
    defenders_wins: 0,
    draws: 1,
  },
};
const online = {
  games: [],
  totals: { played: 2, wins: 1, losses: 1, draws: 0, abandoned_games: 1 },
  overview: {},
};
const row = {
  id: fixture.replay.game.id,
  category: 'online',
  ruleset: 'brandubh',
  status: 'completed',
  winner: 'attackers',
  attackers: 'Archer',
  defenders: 'Keeper',
  recorded_at: '2035-01-01T00:00:00Z',
};
const recent = { games: [row], matching: 1, totals: [], next_cursor: null, limit: 3 };
const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status === 200, status, json: async () => body } as Response);
let serve: (url: string) => Promise<Response>;
const fetcher = vi.fn((url: string, _opts?: RequestInit) => serve(url));
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
  identity.id = 'archer';
  history.replaceState(null, '', '/dashboard');
  setLanguage('en', false);
  fetcher.mockClear();
  serve = (url) =>
    respond(
      url.includes('/single-player?')
        ? local
        : url.includes('/personal/online?')
          ? online
          : url.includes('/history/')
            ? recent
            : url.includes('/online/games?')
              ? { games: [], limit: 25, offset: 0 }
              : url.includes('/availability')
                ? { enabled: false, eligible: false }
                : [],
    );
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});
it('combines existing lobby with distinct personal stats, shared latest records and upcoming leaderboard without guest reads', async () => {
  const open = vi.fn();
  vi.stubGlobal('indexedDB', { open });
  const start = vi.fn();
  render(<PersonalDashboard onNewSingle={start} />);
  const link = await screen.findByRole('link', { name: /Archer · Keeper/ });
  expect(link).toHaveAttribute('href', `/archive?review=online&selected=${row.id}`);
  expect(link.closest('ul')).toHaveClass('recorded-games');
  expect(screen.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  expect(screen.getByText('Upcoming in Phase 6')).toBeVisible();
  expect(screen.queryByText(/Guest games stay/)).toBeNull();
  expect(screen.queryByText('Local players')).toBeNull();
  expect(screen.queryByRole('link', { name: 'Guest play on this device' })).toBeNull();
  expect(fetcher.mock.calls.some(([url]) => url === '/api/history/games?dashboard=true')).toBe(
    true,
  );
  fireEvent.click(screen.getByRole('button', { name: 'New single-player game' }));
  expect(start).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'New online game' }));
  await screen.findByRole('heading', { name: 'Create an online game' });
  expect(open).not.toHaveBeenCalled();
});
it('shows stale retry and recovers empty recent results', async () => {
  serve = (url) =>
    url.includes('/history/')
      ? respond({}, 503)
      : respond(
          url.includes('/games?')
            ? { games: [] }
            : url.includes('/personal/')
              ? { ...local, ...online }
              : [],
        );
  render(<PersonalDashboard />);
  await screen.findByRole('button', { name: 'Retry' });
  serve = () => respond({ ...recent, games: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('No completed games yet.');
});
it('aborts old-account history/statistics and rejects late responses', async () => {
  let finish!: (response: Response) => void;
  const previous = serve;
  serve = (url) =>
    url.includes('/history/')
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : previous(url);
  const view = render(<PersonalDashboard />);
  const signals = fetcher.mock.calls.map(([, opts]) => opts?.signal as AbortSignal);
  identity.id = 'keeper';
  serve = (url) => (url.includes('/history/') ? respond({ ...recent, games: [] }) : previous(url));
  view.rerender(<PersonalDashboard />);
  await screen.findByText('No completed games yet.');
  await act(async () => finish(await respond(recent)));
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  expect(screen.queryByRole('link', { name: /Archer · Keeper/ })).toBeNull();
});
it('retains canonical private account replay at its existing deep link', async () => {
  history.replaceState(null, '', '/dashboard/single-player/local-owned');
  serve = () =>
    respond({
      game: {
        id: 'local-owned',
        category: 'account-single-player',
        ruleset: 'brandubh',
        status: 'in_progress',
        winner: null,
      },
      validated: true,
      states: fixture.replay.states,
      moves: fixture.replay.game.moves,
    });
  render(<PersonalDashboard />);
  await screen.findByRole('grid');
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('/api/personal/single-player/local-owned/replay');
});
it('translates dashboard actions in German', async () => {
  setLanguage('de', false);
  render(<PersonalDashboard />);
  await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(3));
  expect(screen.getByRole('heading', { name: 'Übersicht' })).toBeVisible();
});
