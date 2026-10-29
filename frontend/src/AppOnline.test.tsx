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
it.each(['/', '/online?mine=true'])(
  'redirects %s to Dashboard without losing query or mounting play/analysis',
  async (path) => {
    history.replaceState(null, '', path);
    localStorage.setItem('hnefatafl-game', 'retained-local');
    const fetcher = vi.fn((url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () =>
          ['/api/rulesets', '/api/models', '/api/players', '/api/ai/profiles'].includes(url)
            ? []
            : { games: [], totals: url.includes('/history/') ? [] : {}, overview: {}, limit: 25 },
      } as Response),
    );
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    expect(location.pathname).toBe('/dashboard');
    if (path.includes('mine')) expect(location.search).toContain('mine=true');
    if (path.includes('ruleset')) expect(location.search).toContain('ruleset=tablut');
    await screen.findByText('No games on this page.');
    expect(screen.getByRole('button', { name: 'Play' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Online' })).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => /\/api\/games\/|analytics|analysis/.test(url))).toBe(
      false,
    );
    expect(localStorage.getItem('hnefatafl-game')).toBe('retained-local');
    fireEvent.click(screen.getByRole('button', { name: 'Hall of records' }));
    await waitFor(() => expect(location.pathname).toBe('/archive'));
  },
);
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
  expect(screen.getByRole('link', { name: 'Find online games on Dashboard' })).toHaveAttribute(
    'href',
    '/dashboard',
  );
});
