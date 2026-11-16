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
/** A running account-owned game payload for the active-hero board poll. */
const boardGame = (overrides: Record<string, unknown> = {}) => ({
  id: 'local-1',
  ruleset: 'brandubh',
  size: 7,
  board: Array(49).fill('.'),
  turn: 'attackers',
  ply: 0,
  winner: null,
  recorded_winner: null,
  reason: null,
  moves: [],
  cursor: 0,
  total_moves: 0,
  legal_moves: [],
  mode: 'local',
  players: {
    attackers: { name: 'Archer', kind: 'human', config: {} },
    defenders: { name: 'Keeper', kind: 'human', config: {} },
  },
  version: 1,
  captured: { attackers: 0, defenders: 0 },
  created_at: '2035-01-01T00:00:00Z',
  ...overrides,
});
const runningLocalGame = {
  id: 'local-1',
  ruleset: 'brandubh',
  status: 'in_progress',
  players: { attackers: { name: 'Archer' }, defenders: { name: 'Keeper' } },
};
const runningSingleGame = {
  id: 'single-1',
  ruleset: 'brandubh',
  status: 'in_progress',
  mode: 'human-ai',
  winner: null,
  plies: 0,
  players: {
    attackers: { name: 'Archer', kind: 'human' },
    defenders: { name: 'Seer', kind: 'ai' },
  },
};
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
  vi.useRealTimers();
  setLanguage('en', false);
  localStorage.removeItem('hnefatafl-leaderboard-ruleset');
});
it('renders the bundled Forge Keeper artwork for the server-selected earned portrait', async () => {
  const previous = serve;
  serve = (url) =>
    url.includes('/portraits/catalog')
      ? respond({
          ...portraits,
          items: [
            ...portraits.items,
            { id: 'forge-keeper', name: 'Forge Keeper', unlock_achievement_id: 'ai-first-victory' },
          ],
        })
      : url.includes('/profile')
        ? respond({ ...profile, portrait_id: 'forge-keeper' })
        : previous(url);
  render(<PersonalDashboard />);
  await screen.findByText('Forge Keeper');
  expect(document.querySelector('.dashboard-portrait img')).toHaveAttribute(
    'src',
    '/assets/arena-v2/assets/portraits/forge-keeper.webp',
  );
});
it('shows the verification warning before creation actions and disables only online creation', async () => {
  identity.email_verified = false;
  const previous = serve;
  serve = (url) =>
    url.includes('/availability') ? respond({ enabled: true, eligible: false }) : previous(url);
  render(<PersonalDashboard />);
  await screen.findByRole('heading', { name: 'Start a new saga.' });
  const warning = screen.getByText(
    'Verify your email address before creating or joining online games.',
  );
  expect(screen.getByRole('link', { name: 'Verify your email in your profile.' })).toHaveAttribute(
    'href',
    '/profile#email-verification',
  );
  expect(
    warning.compareDocumentPosition(screen.getByRole('button', { name: 'Play online' })) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(warning).toHaveAttribute('role', 'alert');
  expect(screen.getByRole('button', { name: 'Play online' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Play vs AI' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Local 1v1' })).toBeEnabled();
  expect(screen.queryByText(/Creating and joining require/)).toBeNull();
});
it('renders the reference layout with real standing cards, records and leaderboard without device-local reads', async () => {
  const open = vi.fn();
  vi.stubGlobal('indexedDB', { open });
  const start = vi.fn();
  render(<PersonalDashboard onNewSingle={start} />);
  const link = await screen.findByRole('link', { name: /Open review: .*Archer vs Keeper/ });
  expect(link).toHaveAttribute('href', `/archive?review=online&selected=${row.id}`);
  expect(link.closest('.record-strip-item')).toBe(link);
  expect(link.closest('ul')).toHaveClass('record-strip');
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
  // The compact record strip is the dashboard's last row: at most three records,
  // each a single link, and the archive link.
  const recentSection = document.querySelector('.dashboard-recent')!;
  expect(recentSection.querySelectorAll('.record-strip-item')).toHaveLength(1);
  expect(recentSection.querySelector('.record-strip-item')).toHaveAttribute(
    'href',
    `/archive?review=online&selected=${row.id}`,
  );
  expect(document.querySelectorAll('.recorded-games')).toHaveLength(0);
  expect(screen.queryByText('Local players')).toBeNull();
  expect(fetcher.mock.calls.some(([url]) => url === '/api/history/games?dashboard=true')).toBe(
    true,
  );
  expect(fetcher.mock.calls.some(([url]) => url === '/api/personal/progress')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Play vs AI' }));
  expect(start).toHaveBeenCalledOnce();
  // Online creation lives on the Play entry, which owns the lobby dialog.
  fireEvent.click(screen.getByRole('button', { name: 'Play online' }));
  expect(location.pathname + location.search).toBe('/play?setup=online');
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
  const card = document.querySelector('.dashboard-game-list > li')!;
  expect(within(card as HTMLElement).getByText('Your turn')).toBeVisible();
  expect(within(card as HTMLElement).getByRole('link', { name: 'Continue' })).toHaveAttribute(
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
    url.includes('/single-player?')
      ? respond({ ...local, totals: { ...local.totals, in_progress: 0 } })
      : url.includes('/personal/online?')
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
  expect(screen.queryByRole('link', { name: /Open review: .*Archer vs Keeper/ })).toBeNull();
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

it.each(['en', 'de'] as const)(
  'uses account aggregates beyond 25 online and five local previews in %s without expanding the queue',
  async (language) => {
    setLanguage(language, false);
    const previous = serve;
    const onlineGames = Array.from({ length: 25 }, (_, i) => ({
      ...activeOnlineGame,
      id: i ? `online-${i + 1}` : 'online-1',
    }));
    const localGames = Array.from({ length: 5 }, (_, i) => ({
      id: `local-${i}`,
      ruleset: 'brandubh',
      status: 'in_progress',
    }));
    serve = (url) =>
      url.includes('/personal/online?')
        ? respond({
            ...online,
            games: onlineGames,
            overview: { your_turn: 40, other_active: 10, waiting: 2 },
          })
        : url.includes('/personal/local?')
          ? respond({
              ...localRunning,
              games: localGames,
              totals: { ...localRunning.totals, in_progress: 12 },
            })
          : previous(url);
    render(<PersonalDashboard />);
    await screen.findByText(
      language === 'en' ? '52 games await your move' : '52 Partien warten auf deinen Zug',
    );
    expect(document.querySelectorAll('.dashboard-game-list > li')).toHaveLength(30);
    expect(
      screen.getByText(
        language === 'en'
          ? '30 of 67 running games shown.'
          : '30 von 67 laufenden Partien werden angezeigt.',
      ),
    ).toBeVisible();
    expect(fetcher.mock.calls.some(([url]) => url.includes('/personal/online?limit=25'))).toBe(
      true,
    );
    expect(fetcher.mock.calls.some(([url]) => url.includes('/personal/local?limit=5'))).toBe(true);
  },
);
it('does not claim YOUR TURN when the opponent moves in the selected online game', async () => {
  const previous = serve;
  const game = {
    ...activeOnlineGame,
    your_turn: false,
    turn: 'defenders',
    position: { ...activeOnlineGame.position, turn: 'defenders' },
  };
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({
          ...online,
          games: [game],
          overview: { your_turn: 0, other_active: 1, waiting: 0 },
        })
      : url.includes('/personal/online/')
        ? respond(game)
        : previous(url);
  render(<PersonalDashboard />);
  await screen.findByRole('heading', { name: 'Your match is waiting.' });
  expect(document.querySelector('.arena-kicker')).not.toHaveTextContent('YOUR TURN');
  expect(screen.getByText('No games await your move.')).toBeVisible();
});
it.each(['waiting', 'completed'])(
  'never displays a countdown for an online game that becomes %s',
  async (status) => {
    const previous = serve;
    serve = (url) =>
      url.includes('/personal/online?')
        ? respond({ ...online, games: [activeOnlineGame], overview: { your_turn: 1 } })
        : url.includes('/personal/online/')
          ? respond({ ...activeOnlineGame, status })
          : previous(url);
    render(<PersonalDashboard />);
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([url]) => url.includes('/personal/online/online-1'))).toBe(
        true,
      ),
    );
    await screen.findByText('Archer vs Keeper');
    expect(document.querySelector('.arena-hero-meta [aria-label$=" remaining"]')).toBeNull();
    await waitFor(() =>
      expect(document.querySelector('.arena-kicker')).toHaveTextContent(
        status === 'waiting' ? 'Waiting' : 'Completed',
      ),
    );
    expect(document.querySelector('.arena-kicker')).not.toHaveTextContent('YOUR TURN');
  },
);

it.each(['per_move', 'total'])(
  'uses the authoritative active deadline for %s rather than inventing time from balances',
  async (time_control) => {
    const previous = serve;
    const now = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2035-01-01T00:00:00Z'));
    const game = {
      ...activeOnlineGame,
      time_control,
      time_per_move: 999,
      remaining_seconds: { attackers: 999, defenders: 999 },
      deadline: '2035-01-01T00:00:45Z',
    };
    serve = (url) =>
      url.includes('/personal/online?')
        ? respond({ ...online, games: [game], overview: { your_turn: 1 } })
        : url.includes('/personal/online/')
          ? respond(game)
          : previous(url);
    try {
      render(<PersonalDashboard />);
      expect(await screen.findByLabelText('45s remaining')).toBeVisible();
    } finally {
      cleanup();
      now.mockRestore();
    }
  },
);

it('discards delayed aggregate totals on account switches', async () => {
  const previous = serve;
  let finishOnline!: (response: Response) => void;
  let finishLocal!: (response: Response) => void;
  serve = (url) =>
    url.includes('/personal/online?')
      ? new Promise((resolve) => {
          finishOnline = resolve;
        })
      : url.includes('/personal/local?')
        ? new Promise((resolve) => {
            finishLocal = resolve;
          })
        : previous(url);
  const view = render(<PersonalDashboard />);
  identity.id = 'keeper';
  serve = previous;
  view.rerender(<PersonalDashboard />);
  await screen.findByText('No games await your move.');
  await act(async () => {
    finishOnline(await respond({ ...online, overview: { your_turn: 40 } }));
    finishLocal(
      await respond({ ...localRunning, totals: { ...localRunning.totals, in_progress: 12 } }),
    );
  });
  expect(screen.queryByText('52 games await your move')).toBeNull();
  expect(document.querySelector('.dashboard-status-badge')).toHaveTextContent(
    'No games await your move.',
  );
});

it('shows stale local totals as unavailable and recovers through the existing retry', async () => {
  const previous = serve;
  serve = (url) => (url.includes('/personal/local?') ? respond({}, 503) : previous(url));
  render(<PersonalDashboard />);
  await screen.findByText('Game totals unavailable or stale.');
  expect(screen.getByRole('alert')).toHaveTextContent('History unavailable or stale.');
  serve = (url) =>
    url.includes('/personal/local?')
      ? respond({ ...localRunning, totals: { ...localRunning.totals, in_progress: 12 } })
      : previous(url);
  fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('12 games await your move')).toBeVisible();
});
it('does not describe an empty bounded preview as an empty account', async () => {
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({ ...online, games: [], overview: { your_turn: 40, other_active: 0, waiting: 0 } })
      : previous(url);
  render(<PersonalDashboard />);
  expect(await screen.findByText('40 games await your move')).toBeVisible();
  expect(screen.getByText('0 of 43 running games shown.')).toBeVisible();
  expect(screen.getAllByText('No running games in this preview.')).toHaveLength(2);
});

it('bounds a quiet Dashboard to ten periodic HTTP reads per minute without a hero', async () => {
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  fetcher.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetcher).toHaveBeenCalledTimes(10); // Auth is tested separately: at most one/minute.
  const paths = fetcher.mock.calls.map(([path]) => path);
  expect(paths.filter((path) => path.includes('/personal/online?'))).toHaveLength(6);
  for (const fragment of [
    '/single-player?',
    '/personal/local?',
    '/history/games?',
    '/leaderboard?',
  ]) {
    expect(paths.filter((path) => path.includes(fragment))).toHaveLength(1);
  }
});

it('keeps the active hero at five seconds with at most twenty-two Dashboard HTTP reads per minute', async () => {
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({ ...online, games: [activeOnlineGame], overview: { your_turn: 1 } })
      : url.includes('/personal/online/')
        ? respond({ ...activeOnlineGame, version: 1 })
        : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  fetcher.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetcher).toHaveBeenCalledTimes(22);
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/personal/online/'))).toHaveLength(
    12,
  );
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  try {
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    fetcher.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180000);
    });
    expect(fetcher).not.toHaveBeenCalled();
  } finally {
    visibility.mockRestore();
  }
});

it('refreshes the authoritative queue when a hero version changes but ignores unchanged clock snapshots', async () => {
  let version = 1;
  let yourTurn = true;
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({
          ...online,
          games: [{ ...activeOnlineGame, your_turn: yourTurn }],
          overview: { your_turn: yourTurn ? 1 : 0 },
        })
      : url.includes('/personal/online/')
        ? respond({ ...activeOnlineGame, version, own_side: yourTurn ? 'attackers' : 'defenders' })
        : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  fetcher.mockClear();
  version = 2;
  yourTurn = false;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/personal/online?'))).toHaveLength(1);
  expect(screen.getByText('No games await your move.')).toBeVisible();
  fetcher.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/personal/online/'))).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/personal/online?'))).toHaveLength(0);
});

it('invalidates event-driven result projections after a server correction without creating a poll loop', async () => {
  let wins = 1;
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({
          ...online,
          statistics: {
            ...online.statistics,
            groups: [{ ...online.statistics.groups[0], wins, losses: 2 - wins }],
          },
        })
      : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  fetcher.mockClear();
  wins = 2;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  for (const fragment of ['/personal/progress', '/leaderboard?']) {
    expect(fetcher.mock.calls.some(([path]) => path.includes(fragment))).toBe(true);
  }
  fetcher.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual(['/api/personal/online?limit=25']);
});

it('refreshes achievement progress after a rated result change without a poll loop', async () => {
  let wins = 1;
  let defenderUnlocked = false;
  const previous = serve;
  const catalog = {
    ...portraits,
    items: [
      ...portraits.items,
      { id: 'iron-helm', name: 'Iron Helm', unlock_achievement_id: 'defender-hold' },
    ],
  };
  serve = (url) =>
    url.includes('/portraits/catalog')
      ? respond(catalog)
      : url.includes('/personal/achievements')
        ? respond({
            ...achievements,
            items: achievements.items.map((item) =>
              item.id === 'defender-hold' ? { ...item, unlocked: defenderUnlocked } : item,
            ),
          })
        : url.includes('/personal/online?')
          ? respond({
              ...online,
              statistics: {
                ...online.statistics,
                groups: [{ ...online.statistics.groups[0], wins, losses: 2 - wins }],
              },
            })
          : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  const portraitsCard = () =>
    screen.getByRole('heading', { name: 'Unlocked portraits' }).closest('article')!;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(within(portraitsCard()).getByText('2 / 3')).toBeVisible();
  fetcher.mockClear();
  wins = 2;
  defenderUnlocked = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  // The rated-result change invalidates the event-driven achievement read once.
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(1);
  await act(async () => {
    await Promise.resolve();
  });
  expect(within(portraitsCard()).getByText('3 / 3')).toBeVisible();
  // The achievement read stays event-driven: no extra timer after the change.
  fetcher.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(0);
});

it('refreshes achievements when a local game completes in another session', async () => {
  let version = 1;
  let winner: 'attackers' | null = null;
  let localUnlocked = false;
  const previous = serve;
  const catalog = {
    ...portraits,
    items: [
      ...portraits.items,
      { id: 'forge-keeper', name: 'Forge Keeper', unlock_achievement_id: 'local-first-game' },
    ],
  };
  serve = (url) =>
    url.includes('/games/')
      ? respond(boardGame({ version, winner }))
      : url.includes('/portraits/catalog')
        ? respond(catalog)
        : url.includes('/personal/achievements')
          ? respond({
              ...achievements,
              items: [
                ...achievements.items,
                { id: 'local-first-game', unlocked: localUnlocked, portrait_id: 'forge-keeper' },
              ],
            })
          : url.includes('/personal/local?')
            ? respond({
                ...localRunning,
                games: [runningLocalGame],
                totals: { ...localRunning.totals, in_progress: 1 },
              })
            : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const portraitsCard = () =>
    screen.getByRole('heading', { name: 'Unlocked portraits' }).closest('article')!;
  expect(within(portraitsCard()).getByText('2 / 3')).toBeVisible();
  fetcher.mockClear();
  // Another session finishes the same-device game; the server bumps the version.
  version = 2;
  winner = 'attackers';
  localUnlocked = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(1);
  // A local milestone does not move the rated streak or standing projections.
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/personal/progress')).length).toBe(0);
  expect(within(portraitsCard()).getByText('3 / 3')).toBeVisible();
});

it('refreshes achievements when a single-player game is won against the AI', async () => {
  let version = 1;
  let winner: 'attackers' | null = null;
  let aiUnlocked = false;
  const previous = serve;
  const catalog = {
    ...portraits,
    items: [
      ...portraits.items,
      { id: 'forge-keeper', name: 'Forge Keeper', unlock_achievement_id: 'ai-first-victory' },
    ],
  };
  serve = (url) =>
    url.includes('/games/')
      ? respond(
          boardGame({
            id: 'single-1',
            mode: 'human-ai',
            version,
            winner,
            players: {
              attackers: { name: 'Archer', kind: 'human', config: {} },
              defenders: { name: 'Seer', kind: 'ai', config: {} },
            },
          }),
        )
      : url.includes('/portraits/catalog')
        ? respond(catalog)
        : url.includes('/personal/achievements')
          ? respond({
              ...achievements,
              items: [
                ...achievements.items,
                { id: 'ai-first-victory', unlocked: aiUnlocked, portrait_id: 'forge-keeper' },
              ],
            })
          : url.includes('/single-player?')
            ? respond({ ...local, games: [runningSingleGame] })
            : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const portraitsCard = () =>
    screen.getByRole('heading', { name: 'Unlocked portraits' }).closest('article')!;
  expect(within(portraitsCard()).getByText('2 / 3')).toBeVisible();
  fetcher.mockClear();
  version = 2;
  winner = 'attackers';
  aiUnlocked = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(1);
  expect(within(portraitsCard()).getByText('3 / 3')).toBeVisible();
});

it('refreshes achievements when the active online hero transitions to completed', async () => {
  let version = 1;
  let status = 'active';
  let winner: 'attackers' | null = null;
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({ ...online, games: [activeOnlineGame] })
      : url.includes('/personal/online/')
        ? respond({
            ...activeOnlineGame,
            version,
            status,
            position: { ...activeOnlineGame.position, winner },
          })
        : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  fetcher.mockClear();
  version = 2;
  status = 'completed';
  winner = 'attackers';
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(1);
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/personal/progress')).length).toBe(1);
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/leaderboard?')).length).toBe(1);
});

it('refreshes achievements for a side-specific correction without changing aggregate totals', async () => {
  let attackersWins = 1;
  let defendersWins = 0;
  const previous = serve;
  const group = () => ({
    ...online.statistics.groups[0],
    wins: 1,
    losses: 1,
    by_side: {
      attackers: {
        played: 1,
        completed: 1,
        wins: attackersWins,
        losses: 1 - attackersWins,
        draws: 0,
        win_rate: attackersWins,
        average_game_length: 12,
        average_game_length_unit: 'moves',
      },
      defenders: {
        played: 1,
        completed: 1,
        wins: defendersWins,
        losses: 1 - defendersWins,
        draws: 0,
        win_rate: defendersWins,
        average_game_length: 12,
        average_game_length_unit: 'moves',
      },
    },
  });
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({
          ...online,
          statistics: { ...online.statistics, groups: [group()] },
        })
      : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  fetcher.mockClear();
  // The same 1-1 record now credits the win to the defending seat instead.
  attackersWins = 0;
  defendersWins = 1;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(1);
  fetcher.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(0);
});

it('keeps event-driven achievement reads idle while hero and aggregate data are stable', async () => {
  const previous = serve;
  serve = (url) =>
    url.includes('/personal/online?')
      ? respond({ ...online, games: [activeOnlineGame] })
      : url.includes('/personal/online/')
        ? respond({ ...activeOnlineGame, version: 1 })
        : previous(url);
  vi.useFakeTimers();
  await act(async () => {
    render(<PersonalDashboard />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  fetcher.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(
    fetcher.mock.calls.filter(([path]) => path.includes('/personal/achievements')).length,
  ).toBe(0);
  expect(fetcher.mock.calls.filter(([path]) => path.includes('/personal/progress')).length).toBe(0);
});

it.each(['en', 'de'] as const)(
  'uses the authoritative singular total in %s even when the bounded preview is empty',
  async (language) => {
    const previous = serve;
    serve = (url) =>
      url.includes('/personal/online?')
        ? respond({ ...online, games: [], overview: { your_turn: 1 } })
        : previous(url);
    setLanguage(language, false);
    render(<PersonalDashboard />);
    expect(
      await screen.findByText(
        language === 'en' ? '1 game awaits your move' : '1 Partie wartet auf deinen Zug',
      ),
    ).toBeVisible();
    expect(document.querySelector('.dashboard-status-badge')).not.toHaveTextContent('1 games');
  },
);
