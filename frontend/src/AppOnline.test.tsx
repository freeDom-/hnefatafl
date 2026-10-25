import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, '', '/');
});
it('opens the online shell without loading local-game or assistance adapters, and cleans up when leaving', async () => {
  history.replaceState(null, '', '/online');
  localStorage.setItem('hnefatafl-game', 'local-retained');
  const fetcher = vi.fn((path: string) =>
    Promise.resolve({
      ok: !path.startsWith('/api/games/'),
      status: path.startsWith('/api/games/') ? 404 : 200,
      json: async () =>
        path === '/api/online/availability'
          ? { enabled: false, eligible: false }
          : path.includes('/online/games?')
            ? { games: [], offset: 0, limit: 25 }
            : [],
    } as Response),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  await screen.findByText('No games on this page.');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual(
    expect.arrayContaining(['/api/rulesets', '/api/online/availability']),
  );
  expect(
    fetcher.mock.calls.every(
      ([path]) => path === '/api/rulesets' || path.startsWith('/api/online/'),
    ),
  ).toBe(true);
  expect(localStorage.getItem('hnefatafl-game')).toBe('local-retained');
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  expect(location.pathname).toBe('/');
  await screen.findByRole('heading', { name: 'The king awaits.' });
  expect(screen.queryByRole('heading', { name: 'Online lobby' })).toBeNull();
});
