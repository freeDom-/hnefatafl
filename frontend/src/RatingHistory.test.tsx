import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RatingHistory from './RatingHistory';
import { setLanguage } from './i18n';
import type { PersonalProgress, RatingHistory as RatingHistoryData } from './personalTypes';

const authState = vi.hoisted(() => ({ account: { id: 'archer' } as { id: string } | null }));
vi.mock('./Auth', () => ({ useAuth: () => authState }));

const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);

const progress: PersonalProgress = {
  as_of: '2025-02-01T00:00:00Z',
  streaks: {
    rated_win_current: 2,
    rated_win_best: 4,
    by_ruleset: [
      { ruleset: 'brandubh', rated_win_current: 2, rated_win_best: 3, rated_games: 4 },
      { ruleset: 'tablut', rated_win_current: 0, rated_win_best: 1, rated_games: 2 },
      { ruleset: 'copenhagen', rated_win_current: 0, rated_win_best: 0, rated_games: 0 },
    ],
  },
};

/** Newest first, exactly like the ledger projection: a gap, a correction, a win. */
const history = (
  ruleset: string,
  overrides: Partial<RatingHistoryData> = {},
): RatingHistoryData => ({
  ruleset,
  policy_version: 1,
  current_rating: 1516.4,
  as_of: '2025-02-01T00:00:00Z',
  legacy_timestamp_gaps: false,
  items: [
    {
      sequence: 9,
      game_id: 'game-9',
      recorded_at: '2025-01-03T12:00:00Z',
      rating_after: 1530,
      delta: 10,
      result: 'win',
      opponent_display_name: 'Keeper',
      opponent_username: 'Keeper',
      corrected: false,
    },
    {
      sequence: 7,
      game_id: 'game-7',
      recorded_at: '2025-01-02T12:00:00Z',
      rating_after: 1520,
      delta: -8.4,
      result: 'loss',
      opponent_display_name: 'Raven',
      opponent_username: 'Raven',
      corrected: true,
    },
    {
      sequence: 3,
      game_id: 'game-3',
      recorded_at: null,
      rating_after: 1510,
      delta: 10,
      result: 'win',
      opponent_display_name: null,
      corrected: false,
    },
  ],
  next_cursor: null,
  limit: 100,
  matching: 3,
  ...overrides,
});

const plainHistory = (ruleset: string): RatingHistoryData =>
  history(ruleset, {
    legacy_timestamp_gaps: false,
    matching: 1,
    items: [
      {
        ...history(ruleset).items[0],
        game_id: `${ruleset}-only`,
        opponent_display_name: 'Odin',
        opponent_username: 'Odin',
      },
    ],
  });

let serve: (path: string) => Promise<Response>;
const fetcher = vi.fn((path: string) => serve(path));
const rulesetOf = (path: string) => /\/personal\/ratings\/([a-z]+)\/history/.exec(path)?.[1] ?? '';
beforeEach(() => {
  setLanguage('en', false);
  authState.account = { id: 'archer' };
  fetcher.mockClear();
  serve = (path) => {
    if (path.startsWith('/api/personal/progress')) return respond(progress);
    const ruleset = rulesetOf(path);
    return respond(ruleset === 'brandubh' ? history(ruleset) : plainHistory(ruleset));
  };
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});

it('renders verified progression for all three pools with gaps and corrections labelled', async () => {
  render(<RatingHistory ruleset="brandubh" />);
  expect(await screen.findByRole('heading', { name: 'Rating history' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Brandubh' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Tablut' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Copenhagen' })).toBeVisible();
  // Only the applied ledger values are plotted: 1510 → 1520 → 1530.
  const chart = screen.getByRole('img', { name: 'Brandubh rating progression' });
  expect(within(chart).getByText('1,530')).toBeVisible();
  expect(within(chart).getByText('1,510')).toBeVisible();
  expect(
    screen.getByText('1 entries have no recorded date; the ledger order still applies to them.'),
  ).toBeVisible();
  expect(screen.getByText('Rated games: 4')).toBeVisible();
  expect(screen.getAllByText(/Current 2 · Best 3/).length).toBeGreaterThan(0);
  // Corrections and missing dates stay explicit in the entry list.
  fireEvent.click(screen.getAllByText('Rating entries')[0]);
  expect(screen.getByText('Corrected result')).toBeVisible();
  expect(screen.getByText('Date unavailable')).toBeVisible();
  expect(screen.getByText('Keeper')).toBeVisible();
  expect(screen.getByText(/8\.4/)).toBeVisible();
  expect(fetcher.mock.calls.map(([path]) => path)).toContain(
    '/api/personal/ratings/tablut/history?limit=100',
  );
});

it('keeps a pool with no rated games empty and offers retry for a failed pool', async () => {
  serve = (path) => {
    if (path.startsWith('/api/personal/progress')) return respond(progress);
    if (path.includes('/copenhagen/')) return respond({ detail: 'down' }, 503);
    return respond(history(rulesetOf(path), { items: [], matching: 0, current_rating: null }));
  };
  render(<RatingHistory ruleset="all" />);
  await waitFor(() =>
    expect(screen.getAllByText('No rated games in this pool yet.')).toHaveLength(2),
  );
  const alerts = await screen.findAllByRole('alert');
  expect(alerts).toHaveLength(1);
  expect(alerts[0]).toHaveTextContent('Rating history unavailable or stale.');
  serve = (path) =>
    path.startsWith('/api/personal/progress')
      ? respond(progress)
      : respond(
          rulesetOf(path) === 'brandubh' ? history(rulesetOf(path)) : plainHistory(rulesetOf(path)),
        );
  fireEvent.click(within(alerts[0]).getByRole('button', { name: 'Retry' }));
  expect(await screen.findAllByRole('img')).toHaveLength(1);
  expect(screen.getByRole('heading', { name: 'Copenhagen' })).toBeVisible();
});

it('labels the streak from the progress projection and translates into German', async () => {
  setLanguage('de', false);
  render(<RatingHistory ruleset="tablut" />);
  expect(await screen.findByRole('heading', { name: 'Wertungsverlauf' })).toBeVisible();
  expect(screen.getAllByText(/Aktuell 2 · Beste 4/).length).toBeGreaterThan(0);
  expect(screen.getAllByText('Serie gewerteter Siege').length).toBeGreaterThan(0);
  expect(screen.getAllByText('Aktuelle Wertung')).toHaveLength(3);
  fireEvent.click(screen.getAllByText('Wertungseinträge')[0]);
  expect(screen.getByText('Korrigiertes Ergebnis')).toBeVisible();
});

it('loads three pools and progress once and issues no recurring requests', async () => {
  vi.useFakeTimers();
  render(<RatingHistory ruleset="brandubh" />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const paths = fetcher.mock.calls.map(([path]) => path);
  expect(paths.filter((path) => path === '/api/personal/progress')).toHaveLength(1);
  expect(paths.filter((path) => path.startsWith('/api/personal/ratings/'))).toHaveLength(3);
  expect(fetcher).toHaveBeenCalledTimes(4);
  // The old 5 s cadence would have issued a dozen reads within one minute.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetcher).toHaveBeenCalledTimes(4);
});

it('retries only the failed pool on demand and never resumes a timer', async () => {
  vi.useFakeTimers();
  let failing = true;
  serve = (path) => {
    if (path.startsWith('/api/personal/progress')) return respond(progress);
    return failing ? respond({ detail: 'down' }, 503) : respond(history(rulesetOf(path)));
  };
  render(<RatingHistory ruleset="brandubh" />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const alerts = screen.getAllByRole('alert');
  expect(alerts).toHaveLength(3);
  failing = false;
  await act(async () => {
    fireEvent.click(within(alerts[0]).getByRole('button', { name: 'Retry' }));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getAllByRole('alert')).toHaveLength(2);
  expect(screen.getAllByRole('img')).toHaveLength(1);
  expect(fetcher).toHaveBeenCalledTimes(5);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(screen.getAllByRole('alert')).toHaveLength(2);
  expect(fetcher).toHaveBeenCalledTimes(5);
});

it('reloads every pool for a new account and never reuses the old account data', async () => {
  vi.useFakeTimers();
  const view = render(<RatingHistory ruleset="brandubh" />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetcher).toHaveBeenCalledTimes(4);
  expect(screen.getByRole('img', { name: 'Brandubh rating progression' })).toBeVisible();
  // The new account's reads stay unresolved, so any stale data would still show.
  const pending: Array<(response: Response | Promise<Response>) => void> = [];
  serve = () => new Promise<Response>((resolve) => pending.push(resolve));
  authState.account = { id: 'other' };
  act(() => {
    view.rerender(<RatingHistory ruleset="brandubh" />);
  });
  expect(fetcher).toHaveBeenCalledTimes(8);
  expect(screen.queryByRole('img', { name: 'Brandubh rating progression' })).toBeNull();
  expect(screen.getAllByText('Loading rating history…')).toHaveLength(3);
  await act(async () => {
    pending.forEach((resolve, index) => {
      const path = fetcher.mock.calls[4 + index][0];
      resolve(
        path.startsWith('/api/personal/progress')
          ? respond(progress)
          : respond(plainHistory(rulesetOf(path))),
      );
    });
  });
  expect(screen.getByRole('img', { name: 'Brandubh rating progression' })).toBeVisible();
});
