import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import fixture from './onlineFixture.testdata.json';
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
  await screen.findByRole('heading', { name: 'The Great Hall' });
  expect(screen.getByRole('button', { name: 'Battlefield' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Online Play' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Online Play' })).not.toHaveAttribute('aria-current');
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
  expect(screen.getByRole('button', { name: 'Battlefield' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Online Play' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(fetcher.mock.calls.some(([url]) => /\/api\/games\/|analytics|analysis/.test(url))).toBe(
    false,
  );
  expect(localStorage.getItem('hnefatafl-game')).toBe('retained-local');
  fireEvent.click(screen.getByRole('button', { name: 'Hall of records' }));
  await waitFor(() => expect(location.pathname).toBe('/archive'));
});
it('opens the online lobby from the permanent navigation entry', async () => {
  history.replaceState(null, '', '/dashboard');
  const fetcher = lobbyFetcher();
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  const online = await screen.findByRole('button', { name: 'Online Play' });
  expect(online).toBeVisible();
  fireEvent.click(online);
  await waitFor(() => expect(location.pathname).toBe('/online'));
  expect(screen.getByRole('button', { name: 'Online Play' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(screen.getByRole('button', { name: 'Battlefield' })).not.toHaveAttribute('aria-current');
});
it('keeps Online Play active on online game and review routes', async () => {
  const id = (fixture.active as { id: string }).id;
  const base = lobbyFetcher();
  const fetcher = vi.fn((url: string) =>
    url.endsWith(`/online/games/${id}`) || url.endsWith(`/personal/online/${id}`)
      ? Promise.resolve({ ok: true, status: 200, json: async () => fixture.active } as Response)
      : base(url),
  );
  vi.stubGlobal('fetch', fetcher);

  for (const path of [`/online/games/${id}`, `/online/games/${id}/review`]) {
    history.replaceState(null, '', path);
    render(<App />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Online Play' })).toHaveAttribute(
        'aria-current',
        'page',
      ),
    );
    expect(screen.getByRole('button', { name: 'Battlefield' })).not.toHaveAttribute('aria-current');
    cleanup();
  }
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
  expect(screen.getByRole('button', { name: 'Battlefield' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(screen.getByRole('button', { name: 'Online Play' })).not.toHaveAttribute('aria-current');
});
