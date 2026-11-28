import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from './auth-fixture';
import { activeOnlineGame, seedReferenceStandings, seedEmptyGameLists } from './arena-fixture';
import { historicalGame } from './historical-fixture';
import { openWorkshop, openProfile, openDestination } from './play-helpers';
import { openCollection, openCreation, selectWorkshopJob } from './workshop-helpers';
import { workshopComparisonFixture, workshopFixture, workshopFixtures } from './workshop-fixtures';
import type { Job, Model, TrainingWorker } from '../src/types';
import type { CurrentLeague, LeagueGame, LeagueSeason, LeagueStanding } from '../src/leagueTypes';

const output = 'test-results/documentation';
const origin = 'http://127.0.0.1:4173';
const api = 'http://127.0.0.1:8001/api';

async function settled(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
}

async function capture(page: Page, name: string, fullPage = false) {
  await settled(page);
  await page.screenshot({ path: `${output}/${name}.jpg`, type: 'jpeg', quality: 86, fullPage });
}

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
const archivedSeason: LeagueSeason = {
  ...season,
  id: '2035-01',
  starts_at: '2035-01-01T00:00:00Z',
  ends_at: season.starts_at,
  grace_ends_at: '2035-02-02T00:00:00Z',
  status: 'finalized',
  revision: 7,
};
const leagueCurrent: CurrentLeague = {
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
  progress: {
    tier: 'nidavellir',
    previous_tier: 'midgard',
    tier_changed_at: serverTime,
    next_tier: {
      id: 'alfheim',
      minimum_points: 15,
      points_needed: 6,
      progress_points: 9,
      progress_total: 15,
    },
    points: 9,
    wins: 3,
    draws: 0,
    losses: 0,
    played: 3,
    qualified: true,
    qualifying_results: 3,
    rank: 2,
  },
  queue: {
    status: 'idle',
    active_game: null,
    queue: null,
    join_generation: null,
    eligible: true,
    paused: false,
    enabled: true,
    clocks_enabled: true,
    server_time: serverTime,
  },
};
const leaguePlayer = (id: string, name: string, portrait: string | null) => ({
  id,
  username: id,
  display_name: name,
  portrait_id: portrait,
});
const standings: LeagueStanding[] = [
  {
    rank: 1,
    player: leaguePlayer('freya', 'Freya', 'shieldmaiden'),
    tier: 'alfheim',
    points: 18,
    wins: 6,
    draws: 0,
    losses: 1,
    played: 7,
  },
  {
    rank: 2,
    player: leaguePlayer('browser-admin', 'BrowserAdmin', 'viking'),
    tier: 'nidavellir',
    points: 9,
    wins: 3,
    draws: 0,
    losses: 0,
    played: 3,
  },
  {
    rank: 3,
    player: leaguePlayer('bjorn', 'Björn', 'berserker'),
    tier: 'midgard',
    points: 4,
    wins: 2,
    draws: 0,
    losses: 2,
    played: 4,
  },
];
const leagueGames: LeagueGame[] = standings.map((standing, index) => ({
  game_id: `online-${'a'.repeat(28)}${index}`,
  game_status: 'completed',
  paired_at: `2035-02-0${index + 1}T12:00:00Z`,
  opponent: standing.player,
  side: index % 2 ? 'defenders' : 'attackers',
  result: index === 2 ? 'loss' : 'win',
  state: 'eligible',
  corrected: false,
  result_revision: 1,
  game_href: `/online/games/online-${'a'.repeat(28)}${index}`,
  replay_href: `/online/games/online-${'a'.repeat(28)}${index}/review`,
}));

async function seedLeague(page: Page) {
  await page.route('**/api/leagues/**', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, '');
    if (path === '/leagues/current') return route.fulfill({ json: leagueCurrent });
    if (path === '/leagues/seasons')
      return route.fulfill({ json: { items: [season, archivedSeason], next_cursor: null } });
    if (path === '/leagues/standings')
      return route.fulfill({ json: { season, items: standings, next_cursor: null } });
    if (path === '/leagues/history')
      return route.fulfill({ json: { season, items: leagueGames, next_cursor: null } });
    if (path === '/leagues/progress')
      return route.fulfill({ json: { season: archivedSeason, progress: null } });
    if (path === '/leagues/queue') return route.fulfill({ json: leagueCurrent.queue });
    if (path === '/leagues/admin/health')
      return route.fulfill({
        json: {
          settings: { paused: false, version: 11 },
          worker: {
            running: true,
            last_checked_at: serverTime,
            last_success_at: serverTime,
            last_error: null,
            state: 'idle',
            consecutive_failures: 0,
          },
          queue: { total: 0, expired: 0, by_ruleset: {}, stale: [], stale_next_cursor: null },
          settlement: {
            pending: 0,
            failed: 0,
            oldest_pending_seconds: null,
            max_attempts: 0,
            next_retry_at: null,
          },
          rebuilds: [],
          rebuild_pending: 0,
          rebuild_retries: 0,
        },
      });
    if (path === '/leagues/admin/audit')
      return route.fulfill({ json: { items: [], next_cursor: null } });
    return route.fulfill({ json: { items: [], next_cursor: null } });
  });
}

const models: Model[] = [
  {
    id: 'model-completed',
    name: 'Bjorn · Northern campaign',
    ruleset: 'tablut',
    iteration: 120,
    rune: 'ᛒ',
    created_at: '2026-10-23T10:20:00Z',
    architecture: { name: 'tafl-policy-value-v1', channels: 48, blocks: 3 },
    evaluation: { wins: 34, losses: 12, draws: 2, truncated: 0, score: 0.74 },
  },
  {
    id: 'model-running',
    name: 'Eir · Tablut strategist',
    ruleset: 'tablut',
    iteration: 60,
    rune: 'ᛖ',
    created_at: '2026-10-23T09:00:00Z',
    architecture: { name: 'tafl-policy-value-v1', channels: 48, blocks: 3 },
  },
];

const worker: TrainingWorker = {
  id: 'worker-north-forge',
  name: 'North forge · CUDA',
  capacity: 2,
  status: 'online',
  last_seen: '2026-10-23T10:14:30Z',
  created_at: '2026-10-20T08:00:00Z',
  active_jobs: 1,
  capabilities: {
    device: 'cuda',
    selected: 'cuda',
    cuda_available: true,
    cuda_devices: ['NVIDIA RTX 3060'],
    mps_available: false,
    devices: [],
    default_device: 'cuda',
    package_version: '0.42',
    protocol_version: 1,
    training_symmetry_modes: ['off', 'random_d4'],
    inference_cache_modes: ['off', 'exact'],
    evaluation_batching: true,
    parallel_search: true,
    hyper_adaptation: true,
  },
  compatibility: {
    code: 'compatible',
    reason: 'Exact worker and server version match',
    eligible: true,
    compatible: true,
    worker_version: '0.42',
    server_version: '0.42',
    worker_git_revision: 'f56de1b',
    server_git_revision: 'f56de1b',
    override_active: false,
    protocol_compatible: true,
  },
} as TrainingWorker;

function tuningJob(): Job {
  const base = workshopFixture('tuning', 4, 'completed');
  return {
    ...base,
    kind: 'tune',
    config: { ...base.config, study_name: 'Autumn search' },
    progress: {
      ...base.progress,
      study_name: 'Autumn search',
      completed_trials: 4,
      failed_trials: 0,
      best_trial: 3,
      best_score: 0.71,
      best_iteration: 4,
      sampler_seed: 17,
      source_seed: 42,
      source_best_score: 0.58,
      best_configuration: {
        learning_rate: 0.004,
        weight_decay: 0.02,
        mcts_iterations: 24,
        channels: 48,
        blocks: 3,
      },
      source_configuration: {
        learning_rate: 0.001,
        weight_decay: 0.0001,
        mcts_iterations: 16,
        channels: 48,
        blocks: 3,
      },
      trials: [0, 1, 2, 3].map((number) => ({
        number,
        state: 'COMPLETE',
        score: [0.55, 0.61, 0.58, 0.71][number],
        duration: [81, 92, 88, 96][number],
        parameters: {
          learning_rate: [0.001, 0.003, 0.001, 0.004][number],
          weight_decay: [0.0001, 0.02, 0.0001, 0.02][number],
        },
      })),
    },
  } as Job;
}

async function seedWorkshop(page: Page, jobs: Job[]) {
  await page.route('**/api/training/jobs**', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, '');
    if (path === '/training/jobs') return route.fulfill({ json: jobs });
    const match = path.match(/^\/training\/jobs\/([^/]+)(?:\/(.*))?$/);
    if (match) {
      const job = jobs.find((entry) => entry.id === match[1]);
      if (job && match[2] === 'resume')
        return route.fulfill({
          json: {
            iterations: job.progress?.iteration ?? 1,
            learning_rate: job.config?.learning_rate,
            weight_decay: job.config?.weight_decay,
          },
        });
      if (job) return route.fulfill({ json: job });
    }
    return route.fulfill({ json: jobs });
  });
  await page.route('**/api/models', (route) => route.fulfill({ json: models }));
  await page.route('**/api/training/workers', (route) => route.fulfill({ json: [worker] }));
}

test.beforeAll(() => mkdirSync(output, { recursive: true }));

test('game documentation screenshots use disposable fixtures', async ({ page, request }, info) => {
  test.skip(info.project.name !== 'chromium', 'The documentation viewport is desktop.');
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => {
    if (!localStorage.getItem('hnefatafl-language'))
      localStorage.setItem('hnefatafl-language', 'en');
    localStorage.setItem(
      'hnefatafl-appearance',
      JSON.stringify({
        uiTheme: 'kings-table',
        density: 'default',
        board: 'oak',
        tokens: 'classic',
      }),
    );
  });
  await seedEmptyGameLists(page);
  await seedReferenceStandings(page);
  await seedLeague(page);

  await page.goto('/leaderboard');
  await expect(page.getByRole('table', { name: 'Leaderboard' })).toContainText('Freya');
  await capture(page, 'game-leaderboard');

  await page.goto('/leagues');
  await expect(page.getByRole('heading', { name: 'Leagues', exact: true })).toBeVisible();
  await expect(
    page.getByText('Nidavellir', { exact: true }).filter({ visible: true }).first(),
  ).toBeVisible();
  await capture(page, 'game-leagues', true);

  await openProfile(page);
  await expect(page.getByRole('heading', { name: 'My Profile', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Account Security', exact: true })).toBeVisible();
  await capture(page, 'game-profile', true);

  await page.route('**/api/history/games**', (route) =>
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
          attacker_rating: 1576,
          defender_rating: 1610,
          winner_delta: index === 0 ? 12 : null,
        })),
        matching: 3,
        limit: 20,
        next_cursor: null,
        totals: [
          {
            category: 'online',
            games: 3,
            completed: 3,
            attackers_wins: 1,
            defenders_wins: 1,
            draws: 1,
          },
        ],
      },
    }),
  );
  await page.goto('/archive');
  await expect(page.getByRole('heading', { name: 'Hall of records', exact: true })).toBeVisible();
  await expect(page.getByText('Online multiplayer', { exact: true }).first()).toBeVisible();
  await capture(page, 'game-hall-of-records');

  const online = activeOnlineGame();
  await page.route(`**/api/online/games/${online.id}`, (route) => route.fulfill({ json: online }));
  await page.route('**/api/online/availability', (route) =>
    route.fulfill({ json: { enabled: true, eligible: true } }),
  );
  await page.goto(`/online/games/${online.id}`);
  await expect(page.getByRole('grid')).toBeVisible();
  await capture(page, 'game-online-board');

  const single = await historicalGame(request, {
    data: { ruleset: 'brandubh', mode: 'human-ai', human_side: 'attackers' },
  });
  const saved = await single.json();
  await page.goto(`/archive?review=account-single-player&selected=${saved.id}`);
  await expect(page.getByRole('grid')).toBeVisible();
  await capture(page, 'game-replay');
});

test('signed-out account screenshots use the real account forms', async ({ browser }) => {
  const context = await browser.newContext({
    baseURL: origin,
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  try {
    await page.route('**/api/auth/registration', (route) =>
      route.fulfill({ json: { enabled: true, email_available: true } }),
    );
    for (const [path, name] of [
      ['/login', 'game-login'],
      ['/register', 'game-register'],
      ['/verify-email', 'game-verify-email'],
      ['/forgot-password', 'game-forgot-password'],
    ] as const) {
      await page.goto(path);
      await page
        .getByRole('heading', { name: /HNEFATAFL|Verify|Reset|Create|Welcome/i })
        .first()
        .waitFor();
      await capture(page, name);
    }
  } finally {
    await context.close();
  }
});

test('workshop documentation screenshots use synthetic training records', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'The documentation viewport is desktop.');
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => {
    if (!localStorage.getItem('hnefatafl-language'))
      localStorage.setItem('hnefatafl-language', 'en');
    localStorage.setItem(
      'hnefatafl-appearance',
      JSON.stringify({
        uiTheme: 'kings-table',
        density: 'default',
        board: 'oak',
        tokens: 'classic',
      }),
    );
  });
  const completed = {
    ...workshopFixture('completed', 120, 'completed'),
    model_id: 'model-completed',
  };
  const jobs = [completed, workshopFixture('running', 60), workshopFixture('failed', 15, 'failed')];
  await seedWorkshop(page, jobs);
  const tournament = workshopComparisonFixture();
  await page.route('**/api/tournaments**', (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, '');
    if (path === '/tournaments') return route.fulfill({ json: [tournament] });
    return route.fulfill({ json: tournament });
  });

  await page.goto('/dashboard');
  await openWorkshop(page);
  await expect(page.getByRole('heading', { name: /AI workshop/i })).toBeVisible();
  await capture(page, 'training-overview');

  await openCreation(page);
  await capture(page, 'training-creation');
  await page.getByRole('button', { name: /Advanced configuration/ }).click();
  await page.getByRole('button', { name: /Advanced configuration/ }).scrollIntoViewIfNeeded();
  await capture(page, 'training-creation-advanced');
  await page.getByRole('button', { name: 'Tune parameters', exact: true }).click();
  await page
    .getByText('Parameter search space (JSON)', { exact: false })
    .first()
    .scrollIntoViewIfNeeded();
  await capture(page, 'training-tuning-config');

  await selectWorkshopJob(page, 'completed', 'Bjorn · Northern campaign');
  for (const [tab, name] of [
    ['Learning', 'training-learning'],
    ['Evaluation', 'training-evaluation'],
    ['Performance', 'training-performance'],
    ['Details', 'training-details'],
  ] as const) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await capture(page, name);
  }
  await page.getByRole('button', { name: /Model collection/ }).click();
  await expect(page.locator('.model-card').first()).toBeVisible();
  await page.locator('#model-collection').scrollIntoViewIfNeeded();
  await capture(page, 'training-models');

  await page.getByRole('button', { name: /Remote workers/ }).click();
  await expect(
    page.getByRole('heading', { name: 'North forge · CUDA', exact: true }),
  ).toBeVisible();
  await page.locator('#workshop-workers').scrollIntoViewIfNeeded();
  await capture(page, 'training-workers');

  await openDestination(page, 'tournaments');
  await expect(
    page.getByRole('heading', { name: 'The tournament hall.', exact: true }),
  ).toBeVisible();
  await capture(page, 'training-ai-testing');
  await page.getByRole('button', { name: 'Fixed-role comparison', exact: true }).click();
  await expect(
    page.getByRole('dialog', { name: 'Fixed-role comparison', exact: true }),
  ).toBeVisible();
  await capture(page, 'training-ai-setup');
});
