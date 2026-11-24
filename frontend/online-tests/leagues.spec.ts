import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { logoutFromAccount, openDestination, primaryNavigation } from '../tests/play-helpers';

const origin = 'http://127.0.0.1:4177';
const fixture = 'http://127.0.0.1:8006';
const leagues = '/api/leagues';
const gamePath = (id: string) => `/api/online/games/${id}`;
const current = `${leagues}/current?ruleset=brandubh`;
const themes = ['classic-table', 'kings-table', 'iron-ash', 'saga-hall'];

test.use({ actionTimeout: 15000, reducedMotion: 'reduce' });

async function control(context: APIRequestContext, action: string, data?: object) {
  expect((await context.post(`${fixture}/${action}`, { data })).ok()).toBe(true);
}

async function login(context: APIRequestContext, identity: string) {
  const response = await context.post('/api/auth/login', {
    headers: { Origin: origin },
    data: { identity, password: 'online-browser-fixture-password' },
  });
  expect(response.ok()).toBe(true);
  const { account, csrf_token: csrf } = await response.json();
  return {
    context,
    id: account.id as string,
    post: (path: string, data: object) =>
      context.post(path, { headers: { Origin: origin, 'X-CSRF-Token': csrf }, data }),
  };
}

async function json(context: APIRequestContext, path: string) {
  const response = await context.get(path);
  expect(response.ok(), path).toBe(true);
  return response.json();
}

async function brandubh(page: Page) {
  await page.goto('/leagues');
  await expect(page.getByRole('heading', { name: 'Leagues', exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('brandubh');
}

async function join(page: Page) {
  await page.getByRole('button', { name: 'Join queue', exact: true }).click();
}

const watchQueue = (context: APIRequestContext, state: string) =>
  expect.poll(async () => (await json(context, `${leagues}/queue`)).status).toBe(state);

const watchPoints = (context: APIRequestContext, points: number) =>
  expect.poll(async () => (await json(context, current)).progress.points).toBe(points);

test.beforeEach(async ({ request }) => control(request, 'reset'));

test('the League route supports both shell layouts, four themes and keyboard navigation without automatic queueing', async ({
  page,
}, info) => {
  await page.goto('/leagues');
  await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Join queue', exact: true })).toHaveCount(0);
  await login(page.request, `LeagueSolo${info.project.name}`);
  await page.goto('/dashboard');
  const queueMutations: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes(`${leagues}/queue`) && request.method() !== 'GET')
      queueMutations.push(request.url());
  });
  const nav = primaryNavigation(page).getByRole('link', { name: 'Leagues', exact: true });
  await nav.focus();
  await expect(nav).toBeFocused();
  expect(await nav.evaluate((element) => element.matches(':focus-visible'))).toBe(true);
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/leagues$/);
  await expect(nav).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'Personal progression' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'League standings' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'League match history' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'League administration' })).toHaveCount(0);
  await expect(page.getByText('Play a league game to begin your seasonal progress.')).toBeVisible();
  await expect(
    page.getByText('No players have qualified in this ruleset and season.'),
  ).toBeVisible();
  await expect(
    page.getByText('You have no official league games in this ruleset and season.'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Join queue', exact: true })).toBeEnabled();
  for (const theme of themes) {
    if (await page.getByRole('combobox', { name: 'Theme', exact: true }).isVisible()) {
      await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
    } else {
      await page.evaluate((uiTheme) => {
        localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme }));
      }, theme);
      await page.reload();
      await expect(page.getByRole('heading', { name: 'Leagues', exact: true })).toBeVisible();
    }
    await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: info.outputPath(`leagues-${theme}.png`), fullPage: true });
  }
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
  await expect(page.getByRole('heading', { name: 'Ligen', exact: true })).toBeVisible();
  await expect(primaryNavigation(page).getByRole('link', { name: 'Ligen' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  expect(queueMutations).toEqual([]);
  await watchQueue(page.request, 'idle');
});

test('queue status survives reload, reconnect and tab cancellation, then reports expiry', async ({
  page,
  request,
}, info) => {
  const solo = await login(page.request, `LeagueSolo${info.project.name}`);
  await brandubh(page);
  await join(page);
  const waiting = page.getByText('Waiting for an opponent', { exact: true });
  await expect(waiting).toBeVisible();
  const initial = await json(page.request, `${leagues}/queue`);
  await page.reload();
  await expect(waiting).toBeVisible();
  expect((await json(page.request, `${leagues}/queue`)).request_id).toBe(initial.request_id);

  const tab = await page.context().newPage();
  try {
    await tab.goto('/leagues');
    await tab.getByRole('button', { name: 'Cancel queue', exact: true }).click();
    await watchQueue(page.request, 'cancelled');
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await expect(page.getByRole('button', { name: 'Join queue', exact: true })).toBeEnabled();
    await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('brandubh');
    await join(page);
    await expect(waiting).toBeVisible();
    const replacement = await json(page.request, `${leagues}/queue`);
    expect(replacement.request_id).not.toBe(initial.request_id);
    const delayed = await solo.post(`${leagues}/queue/cancel`, { request_id: initial.request_id });
    expect(delayed.ok()).toBe(true);
    expect(await delayed.json()).toMatchObject({
      status: 'cancelled',
      queue: { request_id: replacement.request_id },
    });
    await page.context().setOffline(true);
    await control(request, 'advance', { seconds: 601 });
    await page.context().setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await expect(page.getByText('Your queue entry expired.', { exact: true })).toBeVisible();
    const expired = await solo.post(`${leagues}/queue`, {
      request_id: replacement.request_id,
      ruleset: 'brandubh',
    });
    expect(expired.ok()).toBe(true);
    expect((await expired.json()).status).toBe('expired');
    await expect.poll(async () => (await json(solo.context, `${leagues}/queue`)).queue).toBeNull();
  } finally {
    await tab.close();
  }
});

test('a real League game matches after mutual widening and settles Elo and points without an open League page', async ({
  page,
  browser,
  playwright,
  request,
}, info) => {
  const archerName = `LeagueArcher${info.project.name}`;
  const keeperName = `LeagueKeeper${info.project.name}`;
  const firstTime = '2026-01-14T12:00:00Z';
  await control(request, 'league-clock', { at: firstTime });
  await control(request, 'league-rating', { name: archerName, rating: 1750 });
  const other = await browser.newContext({ baseURL: origin });
  const adminContext = await playwright.request.newContext({ baseURL: origin });
  const opponent = await other.newPage();
  const archer = await login(page.request, archerName);
  const keeper = await login(opponent.request, keeperName);
  const admin = await login(adminContext, `LeagueAdmin${info.project.name}`);
  try {
    await page.clock.setFixedTime(new Date(firstTime));
    await opponent.clock.setFixedTime(new Date(firstTime));
    await brandubh(page);
    await brandubh(opponent);
    await join(page);
    await join(opponent);
    await watchQueue(page.request, 'queued');
    await watchQueue(opponent.request, 'queued');
    expect((await json(page.request, `${leagues}/queue`)).rating_window).toBe(200);

    // Both pages leave League before the windows become compatible. The worker,
    // with only read-only test polling, must commit the official online game.
    await openDestination(page, 'dashboard');
    await openDestination(opponent, 'dashboard');
    await control(request, 'advance', { seconds: 61 });
    await watchQueue(page.request, 'matched');
    await watchQueue(opponent.request, 'matched');
    const initial = await json(page.request, `${leagues}/queue`);
    const id = initial.active_game.game_id as string;
    const game = await json(page.request, gamePath(id));
    expect(game).toMatchObject({
      status: 'active',
      rated: true,
      time_control: 'total',
      time_per_move: 600,
      increment_seconds: 5,
    });

    for (const player of [page, opponent]) {
      await player.goto('/leagues');
      await player.getByRole('link', { name: 'Open league game', exact: true }).click();
      await expect(player).toHaveURL(new RegExp(`/online/games/${id}$`));
      await expect(player.getByRole('grid')).toBeVisible();
    }
    const archerAttacks = game.participants.attackers.account_id === archer.id;
    const attacker = archerAttacks ? page : opponent;
    await attacker.getByRole('gridcell', { name: 'd7, attacker', exact: true }).click();
    await attacker.getByRole('gridcell', { name: 'c7, empty', exact: true }).click();
    await expect(
      attacker.getByRole('gridcell', { name: 'c7, attacker', exact: true }),
    ).toBeVisible();
    if (archerAttacks) {
      const before = await json(keeper.context, gamePath(id));
      const move = await keeper.post(`${gamePath(id)}/moves`, {
        version: before.version,
        source: 17,
        target: 16,
      });
      expect(move.ok()).toBe(true);
    }
    await opponent.goto('/dashboard');
    await expect(page.getByRole('button', { name: 'Concede', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Concede', exact: true }).click();
    await page.getByRole('button', { name: 'Confirm concession', exact: true }).click();
    await expect(page.getByText(/win · concession/)).toBeVisible();
    const finished = await json(archer.context, gamePath(id));
    expect(finished.result.rated_eligible).toBe(true);
    const applications = finished.result.rating as {
      account_id: string;
      rating_before: number;
      rating_after: number;
      delta: number;
      expected: number;
    }[];
    expect(applications).toHaveLength(2);
    const winner = applications.find((application) => application.account_id === keeper.id)!;
    const loser = applications.find((application) => application.account_id === archer.id)!;
    expect(winner.rating_before).toBe(1500);
    expect(loser.rating_before).toBe(1750);
    expect(winner.delta).toBeCloseTo(32 * (1 - winner.expected), 8);
    expect(winner.rating_after).toBeCloseTo(winner.rating_before + winner.delta, 8);
    expect(loser.delta).toBeCloseTo(-winner.delta, 8);
    await page.goto('/dashboard');
    await watchPoints(keeper.context, 3);
    expect((await json(archer.context, current)).progress).toMatchObject({
      points: 0,
      losses: 1,
      qualified: false,
    });

    // Use the same authenticated online authority for two more games that
    // qualify both users. Their League page rehydrates from the server.
    const ids = [id];
    for (let gameNumber = 2; gameNumber <= 3; gameNumber++) {
      expect(
        (
          await archer.post(`${leagues}/queue`, { ruleset: 'brandubh', request_id: randomUUID() })
        ).ok(),
      ).toBe(true);
      expect(
        (
          await keeper.post(`${leagues}/queue`, { ruleset: 'brandubh', request_id: randomUUID() })
        ).ok(),
      ).toBe(true);
      await watchQueue(keeper.context, 'matched');
      const nextId = (await json(keeper.context, `${leagues}/queue`)).active_game.game_id;
      expect(ids).not.toContain(nextId);
      ids.push(nextId);
      const next = await json(keeper.context, gamePath(nextId));
      let version = next.version;
      if (next.participants.attackers.account_id !== archer.id) {
        const moved = await keeper.post(`${gamePath(nextId)}/moves`, {
          version,
          source: 3,
          target: 2,
        });
        expect(moved.ok()).toBe(true);
        version = (await moved.json()).game.version;
      }
      expect((await archer.post(`${gamePath(nextId)}/concede`, { version })).ok()).toBe(true);
      await watchPoints(keeper.context, gameNumber * 3);
    }
    await brandubh(opponent);
    await expect(opponent.getByText('Nidavellir', { exact: true }).first()).toBeVisible();
    await expect(opponent.getByText(/^Promoted to Nidavellir · /)).toBeVisible();
    await expect(opponent.getByText('Current rank #1', { exact: true })).toBeVisible();
    const standings = opponent.getByRole('region', { name: 'League standings' });
    await expect(standings.getByRole('row', { name: new RegExp(keeperName) })).toBeVisible();
    const history = opponent.getByRole('region', { name: 'League match history' });
    await expect(history.getByRole('row', { name: new RegExp(archerName) })).toHaveCount(3);
    expect((await json(keeper.context, current)).progress).toMatchObject({
      points: 9,
      wins: 3,
      qualified: true,
      rank: 1,
    });
    await history.locator(`a[href="/online/games/${id}/review"]`).click();
    await expect(opponent).toHaveURL(new RegExp(`/online/games/${id}/review$`));
    await expect(
      opponent.getByText('Read-only review. Replay controls change only your view.').first(),
    ).toBeVisible();

    await control(request, 'league-clock', { at: '2026-02-01T12:00:00Z' });
    await brandubh(opponent);
    await opponent
      .getByRole('combobox', { name: 'Standings season', exact: true })
      .selectOption('2026-01');
    await expect(standings.getByText(/Provisional standings/)).toBeVisible();

    await control(request, 'league-clock', { at: '2026-02-02T12:00:00Z' });
    await brandubh(opponent);
    expect((await json(keeper.context, current)).progress.points).toBe(0);
    await opponent
      .getByRole('combobox', { name: 'Standings season', exact: true })
      .selectOption('2026-01');
    await expect(standings.getByRole('row', { name: new RegExp(keeperName) })).toBeVisible();
    const archive = `${leagues}/standings?ruleset=brandubh&season=2026-01`;
    const before = await json(keeper.context, archive);
    expect(before.season.status).toBe('finalized');
    await expect(standings.getByText('Finalized standings', { exact: false })).toBeVisible();
    const revoked = await admin.post(`${gamePath(id)}/void`, {
      version: finished.version,
      note: 'Disposable fixture: revise an archived League game',
    });
    expect(revoked.ok()).toBe(true);
    await expect
      .poll(async () => (await json(keeper.context, archive)).season.revision)
      .toBeGreaterThan(before.season.revision);
    await opponent.reload();
    await opponent.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('brandubh');
    await opponent
      .getByRole('combobox', { name: 'Standings season', exact: true })
      .selectOption('2026-01');
    await expect(standings.getByRole('row', { name: new RegExp(keeperName) })).toHaveCount(0);
    await expect(
      standings.getByText(`Revision ${before.season.revision + 1}`, { exact: false }),
    ).toBeVisible();
  } finally {
    await other.close();
    await adminContext.dispose();
    await control(request, 'reset');
  }
});

test('changing a League filter and logging out cancel stale protected reads', async ({
  page,
}, info) => {
  await login(page.request, `LeagueSolo${info.project.name}`);
  await brandubh(page);
  await expect(page.getByRole('button', { name: 'Join queue' })).toBeVisible();
  const waiters = new Set<() => void>();
  await page.route(/\/api\/leagues\/current\?/, async (route) => {
    const ruleset = new URL(route.request().url()).searchParams.get('ruleset');
    if (ruleset !== 'tablut') return route.continue();
    await new Promise<void>((release) => waiters.add(release));
    await route.abort().catch(() => {});
  });
  const isStaleRead = (request: { url(): string }) => {
    const url = new URL(request.url());
    return url.pathname === `${leagues}/current` && url.searchParams.get('ruleset') === 'tablut';
  };
  const ruleset = page.getByRole('combobox', { name: 'Ruleset', exact: true });
  try {
    const firstRequest = page.waitForEvent('request', { predicate: isStaleRead });
    const firstAbort = page.waitForEvent('requestfailed', {
      predicate: isStaleRead,
      timeout: 10000,
    });
    await ruleset.selectOption('tablut');
    await firstRequest;
    await ruleset.selectOption('copenhagen');
    expect((await firstAbort).failure()?.errorText).toMatch(/ERR_ABORTED/);

    const secondRequest = page.waitForEvent('request', { predicate: isStaleRead });
    const secondAbort = page.waitForEvent('requestfailed', {
      predicate: isStaleRead,
      timeout: 10000,
    });
    await ruleset.selectOption('tablut');
    await secondRequest;
    await logoutFromAccount(page);
    expect((await secondAbort).failure()?.errorText).toMatch(/ERR_ABORTED/);
    await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Personal progression' })).toHaveCount(0);
  } finally {
    for (const release of waiters) release();
  }
});

test('League administration requires permission and records versioned pause and resume in a private audit', async ({
  page,
  browser,
}, info) => {
  const admin = await login(page.request, `LeagueAdmin${info.project.name}`);
  const other = await browser.newContext({ baseURL: origin });
  const player = await other.newPage();
  const ordinary = await login(player.request, `LeagueSolo${info.project.name}`);
  try {
    expect((await ordinary.context.get(`${leagues}/admin/audit`)).status()).toBe(403);
    await page.goto('/leagues');
    const section = page.getByRole('region', { name: 'League administration' });
    await expect(section.getByText('Running', { exact: true })).toBeVisible();
    const freeze = 'Fixture: pause for League worker maintenance';
    async function change(title: string, reason: string) {
      await section.getByRole('button', { name: title, exact: true }).click();
      const dialog = page.getByRole('dialog', { name: title });
      await expect(dialog.getByLabel('Reason', { exact: true })).toBeFocused();
      await dialog.getByLabel('Reason', { exact: true }).fill(reason);
      await dialog.getByRole('button', { name: title, exact: true }).click();
      await expect(dialog).toHaveCount(0);
    }
    const initial = await json(admin.context, `${leagues}/admin/health`);
    await change('Pause matchmaking', freeze);
    expect((await json(admin.context, `${leagues}/admin/health`)).settings).toMatchObject({
      paused: true,
      version: initial.settings.version + 1,
    });
    await player.goto('/leagues');
    await expect(player.getByText('League matchmaking is paused.', { exact: true })).toBeVisible();
    await expect(player.getByRole('button', { name: 'Join queue', exact: true })).toBeDisabled();
    await expect(player.getByRole('heading', { name: 'League administration' })).toHaveCount(0);
    const obsolete = await admin.post(`${leagues}/admin/matchmaking`, {
      paused: false,
      version: initial.settings.version,
      reason: 'Fixture: reject a stale settings version',
    });
    expect(obsolete.status()).toBe(409);
    await change('Resume matchmaking', 'Fixture: completed League worker maintenance');
    await section.getByText('Private audit history', { exact: true }).click();
    await expect(section.getByText(freeze, { exact: true }).first()).toBeVisible();
    await player.reload();
    await expect(player.getByRole('button', { name: 'Join queue', exact: true })).toBeEnabled();
    await expect(player.getByText(freeze, { exact: true })).toHaveCount(0);
  } finally {
    const latest = await json(admin.context, `${leagues}/admin/health`);
    if (latest.settings.paused)
      expect(
        (
          await admin.post(`${leagues}/admin/matchmaking`, {
            paused: false,
            version: latest.settings.version,
            reason: 'Fixture: restore matchmaking after browser validation',
          })
        ).ok(),
      ).toBe(true);
    await other.close();
  }
});
