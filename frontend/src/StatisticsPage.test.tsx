import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import StatisticsPage from './StatisticsPage';
const group = {
  ruleset: 'brandubh',
  mode: null,
  played: 1,
  completed: 1,
  wins: 1,
  losses: 0,
  draws: 0,
  win_rate: 1,
  win_rate_attackers: 1,
  win_rate_defenders: null,
  average_game_length: 12,
  average_game_length_unit: 'moves',
};
const response = (value: unknown, status = 200) =>
  ({ ok: status === 200, status, json: async () => value }) as Response;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
});
it('restores mode/ruleset from the URL and uses only existing private statistics APIs', async () => {
  history.replaceState(null, '', '/statistics?mode=multiplayer&ruleset=brandubh');
  const fetcher = vi.fn(async (_path: string) =>
    response({
      statistics: { groups: [{ ...group, rating: 1520 }], coverage: { unattributed_games: 0 } },
    }),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<StatisticsPage />);
  expect(await screen.findByText('Elo 1520')).toBeVisible();
  expect(fetcher.mock.calls[0][0]).toBe('/api/personal/online?section=history&limit=1');
  expect(screen.getByRole('tab', { name: 'Multiplayer' })).toHaveAttribute('aria-selected', 'true');
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'tablut' } });
  expect(location.search).toContain('ruleset=tablut');
  expect(await screen.findByText('No games recorded yet.')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Singleplayer' }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([path]) => path === '/api/personal/single-player?limit=1'),
    ).toBe(true),
  );
  expect(location.search).toContain('mode=single-player');
});
it('discards a late response from the previous mode and recovers from a failed load', async () => {
  history.replaceState(null, '', '/statistics');
  let finish!: (value: Response) => void;
  let failed = true;
  const fetcher = vi.fn((path: string) =>
    path.includes('single-player')
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : Promise.resolve(
          failed
            ? response({ detail: 'Unavailable' }, 503)
            : response({ statistics: { groups: [], coverage: { unattributed_games: 0 } } }),
        ),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<StatisticsPage />);
  fireEvent.click(screen.getByRole('tab', { name: 'Multiplayer' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Statistics unavailable or stale.');
  failed = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('No games recorded yet.')).toBeVisible();
  await act(async () =>
    finish(response({ statistics: { groups: [group], coverage: { unattributed_games: 0 } } })),
  );
  expect(screen.queryByRole('heading', { name: 'Brandubh' })).toBeNull();
});
