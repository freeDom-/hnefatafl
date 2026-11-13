import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PersonalDashboard from './PersonalDashboard';
import fixture from './onlineFixture.testdata.json';
import { setLanguage } from './i18n';
const identity = vi.hoisted(() => ({ id: 'archer', permissions: ['play'], email_verified: true }));
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
  statistics: {
    groups: [
      {
        ruleset: 'brandubh',
        mode: 'human-ai',
        played: 4,
        wins: 2,
        losses: 1,
        draws: 1,
        win_rate: 2 / 3,
        win_rate_attackers: 0.5,
        win_rate_defenders: 1,
        average_game_length: 30,
        average_game_length_unit: 'moves',
      },
    ],
    coverage: { unattributed_games: 1 },
  },
};
const online = {
  games: [],
  totals: { played: 2, wins: 1, losses: 1, draws: 0, abandoned_games: 1 },
  statistics: {
    groups: [
      {
        ruleset: 'brandubh',
        mode: 'single',
        played: 2,
        wins: 1,
        losses: 1,
        draws: 0,
        timed_out: 1,
        rating: 1537,
        win_rate: 0.5,
        win_rate_attackers: 0.5,
        win_rate_defenders: null,
        average_game_length: 12,
        average_game_length_unit: 'moves',
      },
    ],
    coverage: { unattributed_games: 0 },
  },
  overview: {},
};
const localRunning = {
  games: [],
  totals: {
    total_games: 0,
    completed_games: 0,
    in_progress: 0,
    attackers_wins: 0,
    defenders_wins: 0,
    draws: 0,
    average_game_length: 0,
  },
};
const progress = {
  as_of: '2035-01-01T00:00:00Z',
  streaks: {
    rated_win_current: 3,
    rated_win_best: 8,
    by_ruleset: [{ ruleset: 'brandubh', rated_win_current: 3, rated_win_best: 8, rated_games: 12 }],
  },
};
const portraits = {
  catalog_version: 1,
  default_portrait_id: 'shieldbearer',
  items: [
    { id: 'shieldbearer', name: 'Shieldbearer', unlock_achievement_id: null },
    { id: 'shieldmaiden', name: 'Shieldmaiden', unlock_achievement_id: 'first-rated-win' },
  ],
};
const achievements = {
  catalog_version: 1,
  portrait_catalog_version: 1,
  items: [
    { id: 'first-rated-win', unlocked: true, portrait_id: 'shieldmaiden' },
    { id: 'defender-hold', unlocked: false, portrait_id: 'iron-helm' },
  ],
  summary: { unlocked: 1, total: 2 },
  as_of: '2035-01-01T00:00:00Z',
};
const profile = {
  username: 'archer',
  email: 'archer@example.test',
  email_verified: true,
  display_name: 'Archer',
  country_code: null,
  city: null,
  date_of_birth: null,
  legacy_birth_year: null,
  legacy_private_fields: [],
  country_codes: ['DE'],
  portrait_id: 'shieldbearer',
};
const activeOnlineGame = {
  id: 'online-1',
  category: 'online',
  status: 'active',
  ruleset: 'brandubh',
  time_per_move: 259200,
  time_control: 'per_move',
  increment_seconds: 0,
  remaining_seconds: null,
  deadline: '2035-01-01T14:00:00Z',
  rated: true,
  own_side: 'attackers',
  your_turn: true,
  turn: 'attackers',
  players: { attackers: 'Archer', defenders: 'Keeper' },
  position: {
    ruleset: 'brandubh',
    board: [
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      'K',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
      '.',
    ],
    turn: 'attackers',
    ply: 0,
    winner: null,
    reason: null,
  },
  terminal_king_square: null,
  moves: [],
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
const leaderboard = {
  ruleset: 'tablut',
  policy: { version: 1, initial_rating: 1500, k_factor: 32, provisional_games: 10 },
  entries: [
    {
      account_id: 'archer',
      name: 'Archer',
      rating: 1516,
      rated_games: 12,
      wins: 7,
      losses: 5,
      draws: 0,
      rank: 1,
      provisional: false,
    },
  ],
  total: 1,
  limit: 5,
  offset: 0,
  me: {
    account_id: 'archer',
    name: 'Archer',
    rating: 1516,
    rated_games: 12,
    wins: 7,
    losses: 5,
    draws: 0,
    rank: 1,
    provisional: false,
  },
};
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
  identity.email_verified = true;
  history.replaceState(null, '', '/dashboard');
  setLanguage('en', false);
  // The dashboard's standing cards follow the same stored pool as the compact
  // leaderboard, so a deterministic pool keeps the fixture meaningful.
  localStorage.setItem('hnefatafl-leaderboard-ruleset', 'brandubh');
  fetcher.mockClear();
  serve = (url) =>
    url.includes('/portraits/catalog')
      ? respond(portraits)
      : url.includes('/personal/achievements')
        ? respond(achievements)
        : url.includes('/personal/progress')
          ? respond(progress)
          : url.includes('/profile')
            ? respond(profile)
            : url.includes('/personal/online/')
              ? respond(activeOnlineGame)
              : url.includes('/single-player?')
                ? respond(local)
                : url.includes('/personal/online?')
                  ? respond(online)
                  : url.includes('/personal/local?')
                    ? respond(localRunning)
                    : url.includes('/history/')
                      ? respond(recent)
                      : url.includes('/leaderboard')
                        ? respond(leaderboard)
                        : url.includes('/online/games?')
                          ? respond({ games: [], limit: 25, offset: 0 })
                          : url.includes('/availability')
                            ? respond({ enabled: false, eligible: false })
                            : respond([]);
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
  localStorage.removeItem('hnefatafl-leaderboard-ruleset');
});
it('shows the verification warning before creation actions and disables only online creation', async () => {
  identity.email_verified = false;
  const previous = serve;
  serve = (url) =>
    url.includes('/availability') ? respond({ enabled: true, eligible: false }) : previous(url);
  render(<PersonalDashboard />);
  await screen.findByRole('heading', { name: 'Online games' });
  const warning = screen.getByText(
    'Verify your email address before creating or joining online games.',
  );
  expect(screen.getByRole('link', { name: 'Verify your email in your profile.' })).toHaveAttribute(
    'href',
    '/profile#email-verification',
  );
  expect(
    warning.compareDocumentPosition(screen.getByRole('button', { name: 'New online game' })) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(warning).toHaveAttribute('role', 'alert');
  expect(screen.getByRole('button', { name: 'New online game' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Play vs AI' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Local 1v1' })).toBeEnabled();
  expect(screen.queryByText(/Creating and joining require/)).toBeNull();
});
it('renders the reference layout with real standing cards, records and leaderboard without device-local reads', async () => {
  const open = vi.fn();
  vi.stubGlobal('indexedDB', { open });
  const start = vi.fn();
  render(<PersonalDashboard onNewSingle={start} />);
  const link = await screen.findByRole('link', { name: 'Open review: Archer vs Keeper' });
  expect(link).toHaveAttribute('href', `/archive?review=online&selected=${row.id}`);
  expect(link.closest('table')).toHaveClass('recorded-games');
  expect(screen.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  // The compact table shows the localized rating; the rank summary repeats it.
  expect(
    await within(screen.getByRole('table', { name: 'Leaderboard' })).findByText('1,516'),
  ).toBeVisible();
  // The four standing cards read the same ruleset pool as the leaderboard.
  const elo = screen.getByRole('heading', { name: 'Brandubh Elo' }).closest('article')!;
  expect(within(elo).getByText('1,537')).toBeVisible();
  expect(within(elo).getByText('2 rated games')).toBeVisible();
  const winRate = screen.getByRole('heading', { name: 'Win rate' }).closest('article')!;
  expect(within(winRate).getByText('50%')).toBeVisible();
  const streak = screen.getByRole('heading', { name: 'Current streak' }).closest('article')!;
  expect(within(streak).getByText('3 wins')).toBeVisible();
  expect(within(streak).getByText('Highest: 8')).toBeVisible();
  const portraits = screen.getByRole('heading', { name: 'Unlocked portraits' }).closest('article')!;
  expect(within(portraits).getByText('2 / 2')).toBeVisible();
  expect(screen.queryByText('Timed-out games')).toBeNull();
  expect(screen.queryByRole('table', { name: /results by side/ })).toBeNull();
  // The reference sections keep their order: hero then games that need you in
  // the main column; profile, the four standing cards and the standings in the
  // right column, so the cards sit directly beneath the profile.
  const main = Array.from(document.querySelector('.dashboard-main')!.children).map(
    (node) => node.className,
  );
  expect(main.findIndex((value) => value.includes('arena-hero'))).toBe(0);
  expect(main.findIndex((value) => value.includes('dashboard-needs-you'))).toBe(1);
  const aside = Array.from(document.querySelector('.dashboard-aside')!.children).map(
    (node) => node.className,
  );
  expect(aside.findIndex((value) => value.includes('dashboard-profile'))).toBe(0);
  expect(aside.findIndex((value) => value.includes('dashboard-stats'))).toBe(1);
  expect(aside.findIndex((value) => value.includes('leaderboard-compact'))).toBe(2);
  // The lobby and the records keep their existing real sections below.
  const overview = Array.from(document.querySelector('.dashboard-overview')!.children).map(
    (node) => node.className,
  );
  expect(overview.findIndex((value) => value.includes('dashboard-lobby'))).toBeLessThan(
    overview.findIndex((value) => value.includes('dashboard-recent')),
  );
  expect(screen.queryByText('Local players')).toBeNull();
  expect(fetcher.mock.calls.some(([url]) => url === '/api/history/games?dashboard=true')).toBe(
    true,
  );
  expect(fetcher.mock.calls.some(([url]) => url === '/api/personal/progress')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  expect(start).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'New online game' }));
  await screen.findByRole('heading', { name: 'Create an online game' });
  expect(open).not.toHaveBeenCalled();
});
it('shows the real active board and your-turn cards for an owned running game', async () => {
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({ ...online, games: [activeOnlineGame] })
      : previous(url);
  render(<PersonalDashboard />);
  await screen.findByRole('grid');
  expect(screen.getByRole('heading', { name: 'One move closer to victory.' })).toBeVisible();
  expect(screen.getByText('Archer vs Keeper')).toBeVisible();
  expect(screen.getByText('Attackers to move')).toBeVisible();
  const card = screen.getByRole('listitem');
  expect(within(card).getByText('Your turn')).toBeVisible();
  expect(within(card).getByRole('link', { name: 'Continue' })).toHaveAttribute(
    'href',
    '/online/games/online-1',
  );
  expect(screen.getByRole('link', { name: /Continue game/ })).toHaveAttribute(
    'href',
    '/online/games/online-1',
  );
});
it('shows the four standing cards, keeps the detailed statistics link and moves the badges off the dashboard', async () => {
  render(<PersonalDashboard />);
  await screen.findByRole('heading', { name: 'Brandubh Elo' });
  expect(document.querySelectorAll('.dashboard-stat')).toHaveLength(4);
  expect(document.querySelectorAll('.performance-badge')).toHaveLength(0);
  expect(screen.getByRole('link', { name: 'Detailed statistics' })).toHaveAttribute(
    'href',
    '/statistics',
  );
  expect(screen.getByRole('link', { name: 'Game archive →' })).toHaveAttribute('href', '/archive');
  expect(screen.queryByText('Average game length')).toBeNull();
});
it('shows explicit empty states when no rated pool, streak or portrait data exists', async () => {
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({ ...online, statistics: { groups: [], coverage: { unattributed_games: 0 } } })
      : url.includes('/personal/progress')
        ? respond({
            as_of: '2035-01-01T00:00:00Z',
            streaks: {
              rated_win_current: 0,
              rated_win_best: 0,
              // The server always lists every pool; a zero-rated pool stays empty.
              by_ruleset: [
                { ruleset: 'brandubh', rated_win_current: 0, rated_win_best: 0, rated_games: 0 },
              ],
            },
          })
        : previous(url);
  render(<PersonalDashboard />);
  await screen.findByRole('heading', { name: 'Brandubh Elo' });
  expect(screen.getByText('No rated games in this pool yet.')).toBeVisible();
  expect(screen.getByText('No rated win streak yet.')).toBeVisible();
  expect(
    within(document.querySelector('.arena-hero') as HTMLElement).getByText(
      'No games are currently running.',
    ),
  ).toBeVisible();
  expect(
    within(document.querySelector('.dashboard-needs-you') as HTMLElement).getByText(
      'No games are currently running.',
    ),
  ).toBeVisible();
});
it('shows stale retry and recovers empty recent results', async () => {
  serve = (url) =>
    url.includes('/history/')
      ? respond({}, 503)
      : url.includes('/games?')
        ? respond({ games: [] })
        : url.includes('/personal/')
          ? respond({ ...local, ...online })
          : respond([]);
  render(<PersonalDashboard />);
  const retries = await screen.findAllByRole('button', { name: 'Retry' });
  expect(retries).toHaveLength(1);
  serve = () => respond({ ...recent, games: [] });
  fireEvent.click(retries[0]);
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
  expect(screen.queryByRole('link', { name: 'Open review: Archer vs Keeper' })).toBeNull();
  expect(document.querySelectorAll('.dashboard-stat')).toHaveLength(4);
});
it('retains canonical private account replay at its existing deep link', async () => {
  history.replaceState(null, '', '/dashboard/single-player/local-owned');
  serve = (url) =>
    url.includes('/ai/profiles') || url.includes('/models')
      ? respond([])
      : url.includes('/analytics')
        ? respond({ advisor: null, time_limit: 5, show_win_chances: false, revision: 0 })
        : respond({
            game: {
              id: 'local-owned',
              category: 'account-single-player',
              ruleset: 'brandubh',
              mode: 'human-ai',
              status: 'in_progress',
              winner: null,
              plies: fixture.replay.game.moves.length,
              players: {
                attackers: { name: 'Odin', kind: 'human' },
                defenders: { name: 'Jarl', kind: 'human' },
              },
            },
            game_id: 'local-owned',
            version: 3,
            validated: true,
            states: fixture.replay.states,
            moves: fixture.replay.game.moves,
          });
  render(<PersonalDashboard />);
  await screen.findByRole('grid');
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  expect(
    fetcher.mock.calls.some(([url]) => url === '/api/personal/single-player/local-owned/replay'),
  ).toBe(true);
  expect(fetcher.mock.calls.some(([url]) => url.includes('/moves') || url.includes('/seek'))).toBe(
    false,
  );
});
it('translates dashboard actions in German', async () => {
  setLanguage('de', false);
  render(<PersonalDashboard />);
  await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(3));
  expect(screen.getByRole('heading', { name: 'Übersicht' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Kriegerprofil' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Partien, die dich brauchen' })).toBeVisible();
});
