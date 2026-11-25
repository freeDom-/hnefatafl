import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { logoutFromAccount, openDestination, primaryNavigation } from '../tests/play-helpers';

const origin = 'http://127.0.0.1:4177';
const fixture = 'http://127.0.0.1:8006';
const leagues = '/api/leagues';
const gamePath = (id: string) => `/api/online/games/${id}`;
const current = `${leagues}/current?ruleset=brandubh`;
const themes = ['classic-table', 'kings-table', 'iron-ash', 'saga-hall'];
type JoinBody = { ruleset: string; request_id: string; expected_generation: string | null };

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
  const refresh = page.getByRole('button', { name: 'Refresh leagues' });
  await refresh.focus();
  await expect(refresh).toBeFocused();
  expect(await refresh.evaluate((button) => button.matches(':focus-visible'))).toBe(true);
  expect((await refresh.boundingBox())!.height).toBeGreaterThanOrEqual(24);
  expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(
    true,
  );
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
  await expect(page.getByRole('button', { name: 'Ligadaten aktualisieren' })).toBeVisible();
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

test('a committed join whose response is lost survives route navigation and reload without a replay', async ({
  page,
}, info) => {
  const solo = await login(page.request, `LeagueSolo${info.project.name}`);
  await brandubh(page);
  let posts = 0;
  const pendingCount = () =>
    page.evaluate(
      (prefix) => Object.keys(localStorage).filter((key) => key.startsWith(prefix)).length,
      `hnefatafl-league-join:${encodeURIComponent(solo.id)}:`,
    );
  const pattern = '**/api/leagues/queue';
  page.on('request', (request) => {
    if (request.url().endsWith(`${leagues}/queue`) && request.method() === 'POST') posts++;
  });
  for (const transition of ['navigation', 'reload']) {
    let committed!: (id: string) => void;
    const accepted = new Promise<string>((resolve) => {
      committed = resolve;
    });
    let disconnected!: () => void;
    const release = new Promise<void>((resolve) => {
      disconnected = resolve;
    });
    const dropped: Parameters<Page['route']>[1] = async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const outcome = await route.fetch();
      expect(outcome.ok()).toBe(true);
      committed((await outcome.json()).queue.request_id);
      await release;
      await route.abort().catch(() => {});
    };
    await page.route(pattern, dropped);
    try {
      await join(page);
      const id = await accepted;
      expect(await pendingCount()).toBe(1);
      if (transition === 'navigation') {
        await openDestination(page, 'dashboard');
        await openDestination(page, 'leagues');
      } else {
        await page.reload();
      }
      disconnected();
      await expect(page.getByText('Waiting for an opponent', { exact: true })).toBeVisible();
      await expect.poll(pendingCount).toBe(0);
      expect((await json(page.request, `${leagues}/queue`)).queue.request_id).toBe(id);
    } finally {
      disconnected();
      await page.unroute(pattern, dropped);
    }
    await page.getByRole('button', { name: 'Cancel queue', exact: true }).click();
    await watchQueue(page.request, 'cancelled');
  }
  expect(posts).toBe(2);
});

test('logout and an account switch preserve the right unresolved intent and fence late original joins', async ({
  page,
}, info) => {
  const name = `LeagueSolo${info.project.name}`;
  const solo = await login(page.request, name);
  await brandubh(page);
  let captured!: (body: JoinBody) => void;
  const submitted = new Promise<JoinBody>((resolve) => {
    captured = resolve;
  });
  const pattern = '**/api/leagues/queue';
  const dropped: Parameters<Page['route']>[1] = async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    captured(route.request().postDataJSON());
    await route.abort('connectionreset');
  };
  await page.route(pattern, dropped);
  await join(page);
  const old = await submitted;
  const key = `hnefatafl-league-join:${encodeURIComponent(solo.id)}:${old.request_id}`;
  expect(await page.evaluate((id) => !!localStorage.getItem(id), key)).toBe(true);
  await page.unroute(pattern, dropped);
  await logoutFromAccount(page);
  await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
  const other = await login(page.request, `LeagueAdmin${info.project.name}`);
  await brandubh(page);
  expect(await page.evaluate((id) => !!localStorage.getItem(id), key)).toBe(true);
  await expect(page.getByText(/A previous join is unconfirmed/)).toHaveCount(0);
  expect((await json(other.context, `${leagues}/queue`)).queue).toBeNull();
  await logoutFromAccount(page);
  await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
  const restored = await login(page.request, name);
  await brandubh(page);
  await expect(page.getByText(/A previous join is unconfirmed/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry previous join' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Join queue', exact: true })).toBeDisabled();
  expect((await json(page.request, `${leagues}/queue`)).queue).toBeNull();
  await page.getByRole('button', { name: 'Discard pending join' }).click();
  await watchQueue(page.request, 'cancelled');
  const delayed = await restored.post(`${leagues}/queue`, old);
  expect(delayed.ok()).toBe(true);
  expect(await delayed.json()).toMatchObject({ status: 'cancelled', queue: null });
  await join(page);
  await watchQueue(page.request, 'queued');
  const replacement = (await json(page.request, `${leagues}/queue`)).queue.request_id;
  expect(replacement).not.toBe(old.request_id);
  await page.getByRole('button', { name: 'Cancel queue', exact: true }).click();
  await watchQueue(page.request, 'cancelled');
  const replay = await restored.post(`${leagues}/queue`, old);
  expect(await replay.json()).toMatchObject({ status: 'cancelled', queue: null });
  await expect.poll(() => page.evaluate((id) => localStorage.getItem(id), key)).toBeNull();
  expect((await json(page.request, `${leagues}/queue`)).join_generation).toBe(replacement);
});

test('an uncommitted join stays explicit across reload and navigation and retries the original payload', async ({
  page,
}, info) => {
  await login(page.request, `LeagueSolo${info.project.name}`);
  await brandubh(page);
  const pattern = '**/api/leagues/queue';
  const attempts: JoinBody[] = [];
  await page.route(pattern, async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1) return route.abort('connectionreset');
    return route.continue();
  });
  await join(page);
  const retry = page.getByRole('button', { name: 'Retry previous join' });
  await expect(retry).toBeEnabled();
  await page.reload();
  await expect(retry).toBeEnabled();
  await openDestination(page, 'dashboard');
  await openDestination(page, 'leagues');
  await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('tablut');
  await expect(page.getByText('A previous join is unconfirmed for Brandubh.')).toBeVisible();
  await expect(retry).toBeEnabled();
  expect(attempts).toHaveLength(1);
  expect((await json(page.request, `${leagues}/queue`)).queue).toBeNull();
  await retry.click();
  await watchQueue(page.request, 'queued');
  expect(attempts).toEqual([attempts[0], attempts[0]]);
  expect((await json(page.request, `${leagues}/queue`)).queue).toMatchObject({
    request_id: attempts[0].request_id,
    ruleset: 'brandubh',
  });
  await page.getByRole('button', { name: 'Cancel queue', exact: true }).click();
  await watchQueue(page.request, 'cancelled');
});

test('tabs make the same recovery choice and a discard supersedes both delayed intents', async ({
  page,
}, info) => {
  const solo = await login(page.request, `LeagueSolo${info.project.name}`);
  await brandubh(page);
  const [firstId, secondId] = [randomUUID(), randomUUID()].sort();
  const canonical = await json(page.request, `${leagues}/queue`);
  expect(canonical.queue).toBeNull();
  const first: JoinBody = {
    ruleset: 'brandubh',
    request_id: firstId,
    expected_generation: canonical.join_generation,
  };
  const second = { ...first, ruleset: 'tablut', request_id: secondId };
  const prefix = `hnefatafl-league-join:${encodeURIComponent(solo.id)}:`;
  await page.evaluate(
    ({ prefix, intents }) => {
      for (const intent of intents)
        localStorage.setItem(
          prefix + intent.request_id,
          JSON.stringify({ ruleset: intent.ruleset, generation: intent.expected_generation }),
        );
    },
    { prefix, intents: [second, first] },
  );
  const message = 'A previous join is unconfirmed for Brandubh.';
  await page.reload();
  await expect(page.getByText(message)).toBeVisible();
  const tab = await page.context().newPage();
  try {
    await tab.goto('/leagues');
    await expect(tab.getByText(message)).toBeVisible();
    await tab.getByRole('button', { name: 'Discard pending join' }).click();
    await expect(tab.getByRole('button', { name: 'Join queue', exact: true })).toBeEnabled();
    await page.bringToFront();
    await expect(page.getByRole('button', { name: 'Join queue', exact: true })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Retry previous join' })).toHaveCount(0);
    const late = await solo.post(`${leagues}/queue`, first);
    expect(await late.json()).toMatchObject({ status: 'cancelled', queue: null });
    expect((await solo.post(`${leagues}/queue`, second)).status()).toBe(409);
    expect(await json(page.request, `${leagues}/queue`)).toMatchObject({
      join_generation: firstId,
      queue: null,
    });
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
    const personal = opponent.getByRole('region', { name: 'Personal progression' });
    await expect(personal.getByText('Season rank #1', { exact: true })).toBeVisible();
    await expect(personal.locator('img')).toHaveAttribute('src', /league-nidavellir\.svg$/);
    await expect(personal.getByText(/Archived season · January 2026/)).toHaveText(
      new RegExp(`Revision ${before.season.revision}$`),
    );
    await expect(
      opponent
        .getByRole('region', { name: 'Matchmaking' })
        .getByText(/Current season · February 2026/),
    ).toBeVisible();
    const ruleset = opponent.getByRole('combobox', { name: 'Ruleset', exact: true });
    await ruleset.selectOption('tablut');
    await expect(
      personal.getByText('You have no eligible league results in this ruleset and season.'),
    ).toBeVisible();
    await ruleset.selectOption('brandubh');
    await expect(personal.getByText('Season rank #1', { exact: true })).toBeVisible();
    const revoked = await admin.post(`${gamePath(id)}/void`, {
      version: finished.version,
      note: 'Disposable fixture: revise an archived League game',
    });
    expect(revoked.ok()).toBe(true);
    await expect
      .poll(async () => (await json(keeper.context, archive)).season.revision)
      .toBeGreaterThan(before.season.revision);
    const refresh = opponent.getByRole('button', { name: 'Refresh leagues' });
    await refresh.click();
    await expect(standings.getByRole('row', { name: new RegExp(keeperName) })).toHaveCount(0);
    await expect(
      standings.getByText(`Revision ${before.season.revision + 1}`, { exact: false }),
    ).toBeVisible();
    await expect(personal.getByText('Unranked', { exact: true })).toBeVisible();
    await expect(personal.getByText(/Qualification · 2 of 3/)).toBeVisible();
    const rebuild = await admin.post(`${leagues}/admin/seasons/2026-01/rebuild`, {
      version: before.season.revision + 1,
      reason: 'Disposable fixture: rebuild the archived projection',
    });
    expect(rebuild.status()).toBe(202);
    await refresh.click();
    await expect(
      personal.getByText('Personal progression will return after the season rebuild completes.'),
    ).toBeVisible();
    await expect(personal.getByText('Unranked', { exact: true })).toHaveCount(0);
    await expect
      .poll(async () => (await json(keeper.context, archive)).season.revision)
      .toBeGreaterThan(before.season.revision + 1);
    await refresh.click();
    await expect(personal.getByText('Unranked', { exact: true })).toBeVisible();
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
    await player.getByRole('button', { name: 'Refresh leagues' }).click();
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
