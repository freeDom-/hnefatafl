import type { Page } from '@playwright/test';
import type { OnlineGame } from '../src/onlineTypes';
import type { PersonalOnline, PersonalOnlineHistory } from '../src/personalTypes';
const activeOnlineGameId = 'online-f8157aaad48a49d4ad338588bec9a3f5';
const activeBoard = [
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'D',
  '.',
  '.',
  '.',
  'A',
  'A',
  'D',
  'K',
  'D',
  'A',
  'A',
  '.',
  '.',
  '.',
  'D',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
];
export function activeOnlineGame(): OnlineGame {
  const startedAt = '2035-01-01T00:00:00.000000Z';
  return {
    id: activeOnlineGameId,
    category: 'online',
    creator_id: '7fed11edc170408facbcdc6f60c0cf1f',
    creator_side: 'attackers',
    match: {
      id: 'match-1aec358465fa4402bac7f4ef1e8cfb3d',
      mode: 'single',
      side_selection: 'attackers',
      first_creator_side: 'attackers',
      leg: 1,
      games: [{ id: activeOnlineGameId, leg: 1, status: 'active', version: 1 }],
    },
    status: 'active',
    version: 1,
    ruleset: 'brandubh',
    ruleset_version: '1',
    time_per_move: 259200,
    time_control: 'per_move',
    increment_seconds: 0,
    remaining_seconds: null,
    rated: true,
    created_at: startedAt,
    updated_at: startedAt,
    started_at: startedAt,
    deadline: new Date(Date.now() + 14 * 60 * 60 * 1000).toISOString(),
    completed_at: null,
    participants: {
      attackers: { account_id: '7fed11edc170408facbcdc6f60c0cf1f', name: 'BrowserAdmin' },
      defenders: { account_id: 'c224053c2762463cb463894c90ebb8c3', name: 'Keeper' },
    },
    position: {
      ruleset: 'brandubh',
      board: activeBoard,
      turn: 'attackers',
      ply: 0,
      winner: null,
      reason: null,
    },
    terminal_king_square: null,
    captured: { attackers: 0, defenders: 0 },
    moves: [],
    result: null,
  };
}
/** The account-owned history the dashboard lists; one active game in this pool. */
export function activeOnlineHistory(): PersonalOnlineHistory {
  const game = activeOnlineGame();
  const summary: PersonalOnline = {
    id: game.id,
    category: 'online',
    ruleset: game.ruleset,
    status: game.status,
    version: game.version,
    rated: game.rated,
    turn: game.position.turn,
    own_side: 'attackers',
    your_turn: true,
    overdue: false,
    deadline: game.deadline,
    created_at: game.created_at,
    updated_at: game.updated_at,
    players: { attackers: 'BrowserAdmin', defenders: 'Keeper' },
    outcome: null,
    abandoned: false,
    match: { id: game.match.id, mode: game.match.mode, leg: 1, games: game.match.games },
    result: null,
  };
  return {
    category: 'online',
    limit: 25,
    matching: 1,
    next_cursor: null,
    as_of: game.updated_at,
    games: [
      summary,
      {
        ...summary,
        id: 'waiting-reference',
        status: 'waiting',
        your_turn: false,
        own_side: 'defenders',
        players: { attackers: 'Björn', defenders: 'BrowserAdmin' },
      },
      {
        ...summary,
        id: 'second-reference',
        your_turn: false,
        own_side: 'defenders',
        ruleset: 'tablut',
        players: { attackers: 'Astrid', defenders: 'BrowserAdmin' },
      },
    ],
    overview: { your_turn: 1, other_active: 1, waiting: 1, completed: 0, cancelled: 0, voided: 0 },
    totals: { category: 'online', played: 42, wins: 27, losses: 15, draws: 0, abandoned_games: 0 },
    statistics: {
      groups: [
        {
          ruleset: 'brandubh',
          mode: 'single',
          played: 42,
          completed: 42,
          wins: 27,
          losses: 15,
          draws: 0,
          win_rate: 0.64,
          win_rate_attackers: 0.64,
          win_rate_defenders: null,
          average_game_length: 24,
          average_game_length_unit: 'moves',
          rating: 1576,
        },
      ],
      coverage: { unattributed_games: 0 },
    },
  };
}

/**
 * Seed the dashboard's own game lists so the hero state is deterministic: no
 * single-player or local game, then either no online game (empty state) or the
 * captured active online game (populated hero with the real read-only board).
 */
export async function seedEmptyGameLists(page: Page) {
  await page.route('**/api/personal/single-player**', (route) =>
    route.fulfill({
      json: {
        category: 'account-single-player',
        games: [],
        totals: { played: 0, in_progress: 0 },
        statistics: { groups: [], coverage: { unattributed_games: 0 } },
      },
    }),
  );
  await page.route('**/api/personal/local**', (route) => route.fulfill({ json: { games: [] } }));
}
export function emptyOnlineHistory(): PersonalOnlineHistory {
  return {
    category: 'online',
    limit: 25,
    matching: 0,
    next_cursor: null,
    as_of: '2035-01-01T00:00:00.000000Z',
    games: [],
    overview: { your_turn: 0, other_active: 0, waiting: 0, completed: 0, cancelled: 0, voided: 0 },
    totals: { category: 'online', played: 0, wins: 0, losses: 0, draws: 0, abandoned_games: 0 },
    statistics: { groups: [], coverage: { unattributed_games: 0 } },
  };
}
export async function seedOnlineHistory(page: Page, online: PersonalOnlineHistory) {
  await page.unroute('**/api/personal/online**');
  await page.route('**/api/personal/online**', (route) => {
    const { pathname } = new URL(route.request().url());
    return route.fulfill({
      json: pathname === `/api/personal/online/${activeOnlineGameId}` ? activeOnlineGame() : online,
    });
  });
}

/**
 * Deterministic recent records for the compact strip: three completed records, so
 * the strip renders its full row instead of the empty state in every capture.
 */
export async function seedRecentRecords(page: Page) {
  await page.route('**/api/history/games?dashboard=true', (route) =>
    route.fulfill({
      json: {
        games: ['online', 'account-single-player', 'local'].map((category, index) => ({
          id: `${category}-record-${index}`,
          category,
          ruleset: ['brandubh', 'tablut', 'copenhagen'][index],
          status: 'completed',
          winner: ['defenders', 'attackers', 'draw'][index],
          eligible: true,
          created_at: `2035-01-0${index + 1}T00:00:00.000000Z`,
          recorded_at: `2035-01-0${index + 1}T01:00:00.000000Z`,
          attackers: 'BrowserAdmin',
          defenders: 'Keeper',
          owner: 'BrowserAdmin',
        })),
        matching: 3,
        limit: 3,
        next_cursor: null,
        totals: [],
      },
    }),
  );
}

export async function seedReferenceStandings(page: Page) {
  await page.route('**/api/leaderboard**', (route) => {
    const ruleset = new URL(route.request().url()).searchParams.get('ruleset') ?? 'brandubh';
    const entry = (name: string, rank: number, rating: number) => ({
      account_id: name,
      name,
      rank,
      rating,
      rated_games: 42,
      wins: 27,
      losses: 15,
      draws: 0,
      provisional: false,
    });
    return route.fulfill({
      json: {
        ruleset,
        policy: { version: 1, initial_rating: 1200, k_factor: 32, provisional_games: 10 },
        entries: [entry('Freya', 1, 1824), entry('Björn', 2, 1769)],
        total: 12,
        limit: 5,
        offset: 0,
        me: entry('BrowserAdmin', 12, 1576),
      },
    });
  });
  await page.route('**/api/personal/progress', (route) =>
    route.fulfill({
      json: {
        as_of: '2035-01-01T00:00:00Z',
        streaks: {
          rated_win_current: 3,
          rated_win_best: 8,
          by_ruleset: [
            { ruleset: 'brandubh', rated_games: 42, rated_win_current: 3, rated_win_best: 8 },
          ],
        },
      },
    }),
  );
}
