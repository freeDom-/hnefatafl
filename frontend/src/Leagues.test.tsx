import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import type { Account } from './Auth';
import { setLanguage } from './i18n';
import Leagues from './Leagues';
import type {
  CurrentLeague,
  LeagueGame,
  LeagueHealth,
  LeagueProgress,
  LeagueQueueEntry,
  LeagueQueueStatus,
  LeagueSeason,
} from './leagueTypes';

const auth = vi.hoisted(() => ({ account: null as Account | null }));
vi.mock('./Auth', () => ({ useAuth: () => auth }));
vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  api: vi.fn(),
}));

const serverTime = '2035-02-15T12:01:15Z';
const season: LeagueSeason = {
  id: '2035-02',
  starts_at: '2035-02-01T00:00:00Z',
  ends_at: '2035-03-01T00:00:00Z',
  grace_ends_at: '2035-03-02T00:00:00Z',
  status: 'current',
  revision: 4,
  stored: true,
};
const archive: LeagueSeason = {
  ...season,
  id: '2035-01',
  starts_at: '2035-01-01T00:00:00Z',
  ends_at: season.starts_at,
  grace_ends_at: '2035-02-02T00:00:00Z',
  status: 'finalized',
  revision: 7,
};
const entry: LeagueQueueEntry = {
  status: 'queued',
  request_id: '6ec95718-2f97-457f-a5e8-e76e1809fb59',
  ruleset: 'brandubh',
  joined_at: '2035-02-15T12:00:00Z',
  expires_at: '2035-02-15T12:10:00Z',
  rating_window: 300,
};
const idle: LeagueQueueStatus = {
  status: 'idle',
  active_game: null,
  queue: null,
  eligible: true,
  paused: false,
  enabled: true,
  clocks_enabled: true,
  server_time: serverTime,
};
const queued: LeagueQueueStatus = { ...idle, status: 'queued', queue: entry };
const officialId = 'online-' + 'a'.repeat(32);
const gameHref = `/online/games/${officialId}`;
const player = {
  id: 'keeper',
  display_name: 'Fjord Keeper',
  username: 'keeper',
  portrait_id: 'shieldbearer',
};
const game: LeagueGame = {
  game_id: officialId,
  game_status: 'completed',
  paired_at: entry.joined_at,
  opponent: player,
  side: 'defenders',
  result: 'win',
  state: 'late',
  corrected: true,
  result_revision: 2,
  game_href: gameHref,
  replay_href: `${gameHref}/review`,
};
const health: LeagueHealth = {
  settings: { paused: false, version: 11 },
  worker: {
    running: true,
    last_checked_at: serverTime,
    last_success_at: serverTime,
    last_error: null,
  },
  queue: { total: 0, expired: 0, by_ruleset: {}, stale: [] },
  settlement: { pending: 0, failed: 0, oldest_pending_seconds: null },
  rebuilds: [],
};
const empty: LeagueProgress = {
  tier: null,
  next_tier: null,
  points: 0,
  wins: 0,
  draws: 0,
  losses: 0,
  played: 0,
  qualified: false,
  qualifying_results: 3,
  rank: null,
};
const promoted: LeagueProgress = {
  ...empty,
  tier: 'nidavellir',
  previous_tier: 'midgard',
  tier_changed_at: serverTime,
  next_tier: {
    id: 'alfheim',
    minimum_points: 15,
    points_needed: 6,
    progress_points: 3,
    progress_total: 9,
  },
  points: 9,
  wins: 3,
  played: 3,
  qualified: true,
  rank: 2,
};

let queue: LeagueQueueStatus;
let progress: LeagueProgress;
function current() {
  return {
    server_time: serverTime,
    season,
    policy: {
      version: 1,
      rulesets: ['brandubh', 'tablut', 'copenhagen'],
      tiers: [
        { id: 'midgard', minimum_points: 0 },
        { id: 'nidavellir', minimum_points: 6 },
        { id: 'alfheim', minimum_points: 15 },
        { id: 'asgard', minimum_points: 30 },
        { id: 'valhalla', minimum_points: 50 },
      ],
      win_points: 3,
      draw_points: 1,
      loss_points: -1,
      qualifying_results: 3,
      grace_seconds: 86400,
      queue_seconds: 600,
      clock_seconds: 600,
      increment_seconds: 5,
      rating_window: { initial: 200, maximum: 600, step: 100, interval_seconds: 60 },
      pair_daily_limit: 3,
    },
    progress,
    queue,
  } satisfies CurrentLeague;
}

type RequestMock = (
  url: URL,
  body: unknown,
  method: string | undefined,
  signal?: AbortSignal,
) => unknown;

function apiResponses(override?: RequestMock) {
  vi.mocked(api).mockImplementation(async (path, body, method, signal) => {
    const url = new URL(path, location.origin);
    const response = override?.(url, body, method, signal);
    if (response !== undefined) return (await response) as never;
    if (url.pathname === '/leagues/current') return current() as never;
    if (url.pathname === '/leagues/seasons')
      return { items: [season, archive], next_cursor: null } as never;
    if (url.pathname === '/leagues/queue') return queue as never;
    if (url.pathname === '/leagues/admin/health') return health as never;
    if (url.pathname === '/leagues/admin/audit') return { items: [], next_cursor: null } as never;
    return {
      season: url.searchParams.get('season') === archive.id ? archive : season,
      items: [],
      next_cursor: null,
    } as never;
  });
}

const region = (name: string) => within(screen.getByRole('region', { name }));

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
  auth.account = {
    id: 'archer',
    username: 'archer',
    email: 'private-fixture@example.invalid',
    permissions: ['play'],
    role: 'USER',
  };
  queue = idle;
  progress = empty;
  setLanguage('en');
  apiResponses();
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.useRealTimers();
});

it('loads bounded account-scoped reads without polling a second queue snapshot when idle', async () => {
  vi.useFakeTimers();
  render(<Leagues />);
  await act(async () => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByRole('heading', { name: 'Leagues' })).toBeVisible();
  expect(region('Personal progression').getByText('Unranked')).toBeVisible();
  expect(region('Personal progression').getByRole('progressbar')).toHaveAttribute('value', '0');
  expect(screen.getByText('No players have qualified in this ruleset and season.')).toBeVisible();
  expect(
    screen.getByText('You have no official league games in this ruleset and season.'),
  ).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'League administration' })).toBeNull();
  await act(async () => vi.advanceTimersByTimeAsync(30000));
  expect(
    vi
      .mocked(api)
      .mock.calls.map(([path]) => path)
      .sort(),
  ).toEqual([
    '/leagues/current?ruleset=copenhagen',
    '/leagues/history?season=2035-02&ruleset=copenhagen&limit=20',
    '/leagues/seasons?limit=20',
    '/leagues/standings?season=2035-02&ruleset=copenhagen&limit=20',
  ]);
});

it('uses authoritative tier changes, route links and sporting ranks after a page change', async () => {
  progress = promoted;
  apiResponses((url) => {
    const selected = url.searchParams.get('season') === archive.id ? archive : season;
    if (url.pathname === '/leagues/standings') {
      const later = url.searchParams.get('cursor') === 'later';
      return {
        season: selected,
        items: [
          {
            ...promoted,
            tier: promoted.tier,
            rank: 2,
            player: later
              ? { ...player, id: 'raven', display_name: 'Raven', username: 'raven' }
              : player,
          },
        ],
        next_cursor: later ? null : 'later',
      };
    }
    if (url.pathname === '/leagues/history')
      return { season: selected, items: [game], next_cursor: null };
  });
  const user = userEvent.setup();
  render(<Leagues />);
  expect(await screen.findByText(/Promoted to Nidavellir/)).toBeVisible();
  expect(screen.getByText('Current rank #2')).toBeVisible();
  expect(document.querySelector('.league-tier img')).toHaveAttribute(
    'src',
    expect.stringContaining('league-nidavellir.svg'),
  );
  expect(region('Personal progression').getByRole('progressbar')).toHaveAttribute('max', '9');
  await screen.findByRole('option', { name: 'January 2035' });
  await user.selectOptions(screen.getByLabelText('Standings season'), archive.id);
  expect(await screen.findByText('Finalized standings', { exact: false })).toHaveTextContent(
    'Revision 7',
  );
  const first = await region('League standings').findByRole('row', { name: /Fjord Keeper/ });
  expect(within(first).getByRole('cell', { name: '2' })).toBeVisible();
  await user.click(region('League standings').getByRole('button', { name: 'Next page' }));
  const second = await region('League standings').findByRole('row', { name: /Raven/ });
  expect(within(second).getByRole('cell', { name: '2' })).toBeVisible();
  expect(
    region('League match history').getByText('Finished after the scoring deadline'),
  ).toBeVisible();
  expect(region('League match history').getByText('Corrected · revision 2')).toBeVisible();
  expect(region('League match history').getByRole('link', { name: 'View replay' })).toHaveAttribute(
    'href',
    `${gameHref}/review`,
  );
  progress = { ...promoted, tier: 'midgard', previous_tier: 'nidavellir', points: 5 };
  await act(async () => window.dispatchEvent(new Event('focus')));
  expect(await screen.findByText(/Demoted to Midgard/)).toBeVisible();
});

it('retries a lost join with the original ID and accepts a matching cancellation without opening historical games', async () => {
  const requests: unknown[] = [];
  apiResponses((url, body, method) => {
    if (url.pathname === '/leagues/queue' && method === 'POST') {
      requests.push(body);
      if (requests.length === 1) return Promise.reject(new TypeError('Network dropped'));
      queue = queued;
      return queue;
    }
    if (url.pathname === '/leagues/queue/cancel') {
      expect(body).toEqual({ request_id: entry.request_id });
      queue = {
        ...idle,
        status: 'matched',
        active_game: { game_id: officialId, ruleset: 'brandubh' },
      };
      return queue;
    }
  });
  const user = userEvent.setup();
  render(<Leagues />);
  await user.selectOptions(screen.getByLabelText('Ruleset'), 'brandubh');
  await user.click(await screen.findByRole('button', { name: 'Join queue' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'The league request could not be completed.',
  );
  await user.click(screen.getByRole('button', { name: 'Join queue' }));
  expect(await screen.findByText('Waiting for an opponent')).toBeVisible();
  expect(screen.getByText('Brandubh queue · ±300 Elo')).toBeVisible();
  expect(screen.getByText('1m 15s waiting', { exact: false })).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  await user.click(screen.getByRole('button', { name: 'Cancel queue' }));
  expect(await screen.findByRole('link', { name: 'Open league game' })).toHaveAttribute(
    'href',
    gameHref,
  );
  queue = { ...idle, status: 'matched' };
  await act(async () => window.dispatchEvent(new Event('online')));
  expect(await screen.findByRole('button', { name: 'Join queue' })).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Open league game' })).toBeNull();
});

it('uses canonical queue status when a reconnect reports expiry or a lost eligibility check', async () => {
  queue = queued;
  render(<Leagues />);
  expect(await screen.findByText('Waiting for an opponent')).toBeVisible();
  queue = { ...idle, status: 'expired', server_time: '2035-02-15T12:10:02Z' };
  await act(async () => window.dispatchEvent(new Event('online')));
  expect(await screen.findByText('Your queue entry expired.')).toBeVisible();
  queue = {
    ...idle,
    eligible: false,
    status: 'cancelled',
    queue: { ...entry, status: 'cancelled' },
  };
  await act(async () => window.dispatchEvent(new Event('online')));
  expect(await screen.findByText('Your queue entry was cancelled.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Join queue' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Cancel queue' })).toBeNull();
});

it('aborts stale reads on ruleset change and removes protected state at logout', async () => {
  let staleSignal: AbortSignal | undefined;
  let finish!: (snapshot: CurrentLeague) => void;
  apiResponses((url, _body, _method, signal) => {
    if (url.pathname === '/leagues/current' && url.searchParams.get('ruleset') === 'brandubh') {
      staleSignal = signal;
      return new Promise<CurrentLeague>((resolve) => {
        finish = resolve;
      });
    }
  });
  const view = render(<Leagues />);
  await screen.findByText('Play a league game to begin your seasonal progress.');
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'brandubh' } });
  await waitFor(() => expect(staleSignal).toBeDefined());
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'tablut' } });
  expect(staleSignal?.aborted).toBe(true);
  await act(async () => finish({ ...current(), progress: promoted }));
  expect(screen.queryByText(/Promoted to/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'brandubh' } });
  auth.account = null;
  view.rerender(<Leagues />);
  expect(staleSignal?.aborted).toBe(true);
  expect(screen.queryByRole('region', { name: 'League standings' })).toBeNull();
});

it('sends version-guarded admin commands with a reason and localizes denied state', async () => {
  auth.account!.permissions.push('accounts');
  apiResponses((url) => {
    if (url.pathname === '/leagues/admin/matchmaking') return { paused: true, version: 12 };
  });
  const user = userEvent.setup();
  render(<Leagues />);
  await user.click(await screen.findByRole('button', { name: 'Pause matchmaking' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Pause matchmaking' }));
  await user.type(dialog.getByLabelText('Reason'), 'Scheduled maintenance');
  await user.click(dialog.getByRole('button', { name: 'Pause matchmaking' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/leagues/admin/matchmaking',
      { paused: true, version: 11, reason: 'Scheduled maintenance' },
      'POST',
      expect.any(AbortSignal),
    ),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  queue = { ...idle, paused: true };
  await act(async () => {
    setLanguage('de');
    window.dispatchEvent(new Event('online'));
  });
  expect(await screen.findByRole('heading', { name: 'Ligen' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Ligaverwaltung' })).toBeVisible();
  expect(screen.getByText('Die Liga-Spielersuche ist pausiert.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Warteschlange beitreten' })).toBeDisabled();
});

it('uses a localized public error message without rendering server details', async () => {
  setLanguage('de');
  apiResponses((url, _body, method) => {
    if (url.pathname === '/leagues/queue' && method === 'POST')
      return Promise.reject(new ApiError(429, 'Sensitive situation detail', 'league_rate_limited'));
  });
  render(<Leagues />);
  fireEvent.click(await screen.findByRole('button', { name: 'Warteschlange beitreten' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Das Limit für Ligaanfragen ist erreicht.',
  );
  expect(screen.queryByText('Sensitive situation detail')).toBeNull();
});

it('refreshes current totals after a finished pending game settles and stops polling history', async () => {
  vi.useFakeTimers();
  let history = { ...game, state: 'pending' as LeagueGame['state'] };
  apiResponses((url) => {
    if (url.pathname === '/leagues/history') return { season, items: [history], next_cursor: null };
  });
  render(<Leagues />);
  await act(async () => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByText('Pending settlement')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Refresh match history' })).toBeVisible();
  history = { ...game, state: 'eligible' };
  progress = promoted;
  await act(async () => vi.advanceTimersByTimeAsync(8000));
  expect(screen.getByText('Counts toward the season')).toBeVisible();
  expect(screen.getByText('Current rank #2')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Refresh match history' })).toBeNull();
  await act(async () => vi.advanceTimersByTimeAsync(32000));
  const paths = vi.mocked(api).mock.calls.map(([path]) => path);
  expect(paths.filter((path) => path.startsWith('/leagues/history'))).toHaveLength(2);
  expect(paths.filter((path) => path.startsWith('/leagues/current'))).toHaveLength(2);
});
