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
it('offers a separate non-Elo local 1v1 category and reads the rated ledger for progression', async () => {
  history.replaceState(null, '', '/statistics?mode=multiplayer&ruleset=tablut');
  const paths: string[] = [];
  const fetcher = vi.fn(async (path: string) => {
    paths.push(path);
    if (path.startsWith('/api/personal/progress'))
      return response({
        as_of: '2025-02-01T00:00:00Z',
        streaks: { rated_win_current: 0, rated_win_best: 0, by_ruleset: [] },
      });
    if (path.startsWith('/api/personal/ratings/'))
      return response({
        ruleset: 'tablut',
        policy_version: 1,
        current_rating: 1520,
        as_of: '2025-02-01T00:00:00Z',
        legacy_timestamp_gaps: false,
        items: [],
        next_cursor: null,
        limit: 100,
        matching: 0,
      });
    if (path.startsWith('/api/personal/local'))
      return response({
        category: 'local',
        games: [],
        totals: {
          total_games: 3,
          completed_games: 3,
          in_progress: 0,
          attackers_wins: 2,
          defenders_wins: 1,
          draws: 0,
          average_game_length: 21,
        },
        limit: 1,
        matching: 3,
        next_cursor: null,
      });
    return response({ statistics: { groups: [group], coverage: { unattributed_games: 0 } } });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<StatisticsPage />);
  expect(await screen.findByRole('heading', { name: 'Rating history' })).toBeVisible();
  expect(paths).toContain('/api/personal/progress');
  expect(paths).toContain('/api/personal/ratings/tablut/history?limit=100');
  fireEvent.click(screen.getByRole('tab', { name: 'Local 1v1' }));
  await waitFor(() => expect(paths).toContain('/api/personal/local?limit=1'));
  expect(location.search).toContain('mode=local');
  expect(await screen.findByRole('table', { name: 'Local 1v1 totals' })).toBeVisible();
  expect(screen.getByText('Non-Elo · same device')).toBeVisible();
  // Local totals are account-wide, so the ruleset filter would be misleading.
  expect(screen.queryByLabelText('Ruleset')).toBeNull();
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
  // The late single-player response is discarded: its Brandubh results table
  // never appears and the multiplayer view keeps its own empty state.
  expect(screen.queryByRole('table', { name: 'Brandubh results by side' })).toBeNull();
  expect(screen.getByText('No games recorded yet.')).toBeVisible();
});
