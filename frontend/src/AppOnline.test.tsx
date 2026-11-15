import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
vi.mock('./Auth', () => ({
  useAuth: () => ({ account: { id: 'archer', permissions: ['play'] }, logout: vi.fn() }),
}));
beforeEach(() => {
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
  localStorage.clear();
  history.replaceState(null, '', '/');
});
const lobbyFetcher = () =>
  vi.fn((url: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () =>
        ['/api/rulesets', '/api/models', '/api/players', '/api/ai/profiles'].includes(url)
          ? []
          : url.includes('/online/availability')
            ? { enabled: true, eligible: true }
            : url.includes('/online/games?')
              ? { games: [], limit: 25, offset: 0, total: 0 }
              : { games: [], totals: url.includes('/history/') ? [] : {}, overview: {}, limit: 25 },
    } as Response),
  );
it('redirects / to the Dashboard account setup without mounting play/analysis', async () => {
  history.replaceState(null, '', '/');
  localStorage.setItem('hnefatafl-game', 'retained-local');
  const fetcher = lobbyFetcher();
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  expect(location.pathname).toBe('/dashboard');
  expect(location.search).toBe('');
  await screen.findByRole('heading', { name: 'Dashboard' });
  expect(screen.getByRole('button', { name: 'Play' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Online' })).toBeNull();
  expect(fetcher.mock.calls.some(([url]) => /\/api\/games\/|analytics|analysis/.test(url))).toBe(
    false,
  );
  expect(localStorage.getItem('hnefatafl-game')).toBe('retained-local');
  fireEvent.click(screen.getByRole('button', { name: 'Hall of records' }));
  await waitFor(() => expect(location.pathname).toBe('/archive'));
});
it('keeps the authenticated lobby listing at /online without losing query or mounting play/analysis', async () => {
  history.replaceState(null, '', '/online?mine=true');
  localStorage.setItem('hnefatafl-game', 'retained-local');
  const fetcher = lobbyFetcher();
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  expect(location.pathname).toBe('/online');
  expect(location.search).toContain('mine=true');
  await screen.findByText('No games on this page.');
  expect(screen.getByRole('button', { name: 'Play' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Online' })).toBeNull();
  expect(fetcher.mock.calls.some(([url]) => /\/api\/games\/|analytics|analysis/.test(url))).toBe(
    false,
  );
  expect(localStorage.getItem('hnefatafl-game')).toBe('retained-local');
  fireEvent.click(screen.getByRole('button', { name: 'Hall of records' }));
  await waitFor(() => expect(location.pathname).toBe('/archive'));
});
it('keeps Play as a page and shows creation actions when there are no running games', async () => {
  history.replaceState(null, '', '/play');
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: async () => ({ games: [] }) } as Response),
    ),
  );
  render(<App />);
  await screen.findByText('No games are currently running.');
  expect(location.pathname).toBe('/play');
  expect(screen.getByRole('button', { name: 'New online game' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'New single-player game' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Find online games in the lobby' })).toHaveAttribute(
    'href',
    '/online',
  );
});
