import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
const password = 'personal-browser-fixture-password';
test.beforeEach(async ({ request }) => {
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
});
async function credentials(page: Page, name: string) {
  await page.getByLabel('Username or email').fill(name);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
}
async function login(page: Page, name = 'PersonalArcher', path = '/') {
  await page.goto(path);
  await credentials(page, name);
}
async function snapshot(request: APIRequestContext) {
  return (await request.get('http://127.0.0.1:8010/snapshot')).json();
}
const rows = (page: Page) => page.locator('.recorded-games tbody tr');
const accountPanel = (page: Page) => page.locator('[aria-labelledby="personal-account-heading"]');
const onlinePanel = (page: Page) => page.locator('[aria-labelledby="personal-online-heading"]');

test('default landing, shared latest three and exact review survive filtering and refresh on desktop/mobile', async ({
  page,
  context,
  request,
}) => {
  await login(page);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(nav.getByRole('button', { name: 'Play', exact: true })).toHaveCount(0);
  await expect(nav.getByRole('button', { name: 'Online', exact: true })).toHaveCount(0);
  await expect(page.getByText(/Guest games stay/)).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Guest play', exact: true })).toHaveCount(0);
  await expect(page.getByText('Local players')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  const latest = await (await context.request.get('/api/history/games?dashboard=true')).json();
  await expect(rows(page)).toHaveCount(3);
  expect(
    await rows(page)
      .locator('a')
      .evaluateAll((links) => links.map((a) => new URL(a.href).searchParams.get('selected'))),
  ).toEqual(latest.games.map((g: { id: string }) => g.id));
  const state = await snapshot(request);
  expect(
    latest.games.every(
      (g: { id: string }) =>
        ![state.ids.cancelled, state.ids.voided, state.ids.account].includes(g.id),
    ),
  ).toBe(true);
  await page.screenshot({ path: test.info().outputPath('dashboard.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // The whole row is clickable, not only the date link.
  await rows(page).first().locator('td').first().click({ force: true });
  await expect(page).toHaveURL(new RegExp(`selected=${latest.games[0].id}`));
  await expect(page.getByRole('grid')).toBeVisible();
  await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('tablut');
  await expect(rows(page).filter({ hasText: 'brandubh' })).toHaveCount(0);
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Selected game review' })).toContainText(
    latest.games[0].id,
  );
  await page.reload();
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Selected game review' })).toContainText(
    latest.games[0].id,
  );
  await expect(page.getByLabel('Game type')).toHaveCount(0);
  await expect(page.getByLabel('Player name')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('normal and admin Hall authorization, filters, own-only Dashboard and active analysis restrictions use real sessions', async ({
  page,
  context,
  request,
}) => {
  const state = await snapshot(request);
  for (const name of ['PersonalObserver', 'PersonalKeeper', 'PersonalOdin']) {
    await login(page, name);
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    const personal = await (await context.request.get('/api/history/games?dashboard=true')).json();
    if (name === 'PersonalOdin')
      expect(personal.games.map((g: { id: string }) => g.id)).toEqual([state.ids.admin_account]);
    if (name === 'PersonalObserver') expect(personal.games).toEqual([]);
    expect(
      (
        await context.request.get('/api/personal/single-player/' + state.ids.account + '/replay')
      ).status(),
    ).toBe(404);
    expect((await context.request.get('/api/online/games')).status()).toBe(200);
    expect((await context.request.get('/api/online/games/' + state.ids.active)).status()).toBe(200);
    const me = await (await context.request.get('/api/auth/me')).json();
    expect(
      (
        await context.request.post('/api/games/' + state.ids.active + '/analysis', {
          headers: { Origin: 'http://127.0.0.1:8009', 'X-CSRF-Token': me.csrf_token },
          data: { version: 1, cursor: 0 },
        })
      ).status(),
    ).toBe(409);
    await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
    if (name === 'PersonalOdin') {
      await page.getByLabel('Game type').selectOption('account-single-player');
      await page.getByLabel('Player name').fill('PersonalArcher');
      const ownList = await (
        await context.request.get(
          '/api/history/games?category=account-single-player&player=PersonalArcher',
        )
      ).json();
      await expect(rows(page)).toHaveCount(Math.min(20, ownList.matching));
      await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('tablut');
      await expect(rows(page)).toHaveCount(1);
      const filtered = await (
        await context.request.get(
          '/api/history/games?category=account-single-player&player=PersonalArcher&ruleset=tablut',
        )
      ).json();
      expect(filtered.matching).toBe(1);
      expect(filtered.totals[0].games).toBe(1);
      await page.goto(
        `/archive?ruleset=copenhagen&review=account-single-player&selected=${state.ids.account}`,
      );
      await expect(page.getByText('No games match these filters.')).toBeVisible();
      await expect(page.getByRole('grid')).toBeVisible();
      await expect(page.locator('.detail-row', { hasText: 'Recorded result' })).toContainText(
        'Draw',
      );
      await page.getByRole('button', { name: 'Next move', exact: true }).click();
      await expect(page.getByText('Move 1 of 8')).toBeVisible();
    } else {
      // A normal user may filter their own account records (empty here) but
      // never use the all-account name filter.
      const ownAccount = await context.request.get(
        '/api/history/games?category=account-single-player',
      );
      expect(ownAccount.status()).toBe(200);
      expect((await ownAccount.json()).games).toEqual([]);
      expect((await context.request.get('/api/history/games?player=PersonalArcher')).status()).toBe(
        403,
      );
      expect(
        (
          await context.request.get(
            '/api/history/account-single-player/' + state.ids.account + '/replay',
          )
        ).status(),
      ).toBe(404);
      if (name === 'PersonalObserver')
        expect(
          (
            await context.request.get('/api/history/online/' + state.ids.completed + '/replay')
          ).status(),
        ).toBe(404);
      await expect(page.getByLabel('Game type')).toHaveCount(0);
    }
    await page.getByRole('button', { name: 'Log out', exact: true }).click();
  }
});

test('safe post-login and obsolete redirects retain query/selection, dedicated account and online deep links reconnect', async ({
  page,
  request,
}) => {
  const state = await snapshot(request);
  await login(
    page,
    'PersonalArcher',
    `/archive?ruleset=tablut&review=online&selected=${state.ids.completed}`,
  );
  await expect(page).toHaveURL(new RegExp(`selected=${state.ids.completed}`));
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.locator('.detail-row', { hasText: 'Recorded result' })).toContainText(
    'Attackers win · concession',
  );
  await page.goto('/online?mine=true&selected=kept');
  await expect(page).toHaveURL(/\/dashboard\?mine=true&selected=kept&setup=online$/);
  await expect(page.getByRole('tab', { name: 'My games', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page.goto('/play?ruleset=brandubh');
  await expect(page).toHaveURL(/\/dashboard\?ruleset=brandubh&setup=single-player$/);
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.goto('/games/' + state.ids.unfinished_account);
  await expect(page.getByRole('grid')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('grid')).toBeVisible();
  await page.goto('/online/games/' + state.ids.active);
  await expect(page.getByRole('heading', { name: 'Online game', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('grid')).toBeVisible();
  await page.goto('/dashboard/single-player/' + state.ids.account);
  await expect(page.locator('.detail-row', { hasText: 'Recorded result' })).toContainText('Draw');
});

test('starts account single-player from Dashboard and reconnects at its dedicated route', async ({
  page,
  context,
}) => {
  await login(page);
  await page.getByRole('button', { name: 'New single-player game', exact: true }).click();
  await page.getByRole('radio', { name: /Brandubh/ }).check();
  await page.getByRole('combobox', { name: 'Your side', exact: true }).selectOption('attackers');
  await page.getByRole('button', { name: 'To the table', exact: true }).click();
  await expect(page).toHaveURL(/\/games\/(?!online-)[A-Za-z0-9_-]+$/);
  await expect(page.getByRole('grid')).toBeVisible();
  const path = new URL(page.url()).pathname;
  await page.reload();
  await expect(page.getByRole('grid')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe(path);
  await page.getByRole('button', { name: 'Dashboard', exact: true }).click();
  const game = await (await context.request.get('/api' + path)).json();
  const me = await (await context.request.get('/api/auth/me')).json();
  const changed = await context.request.post('/api' + path + '/moves', {
    headers: { Origin: 'http://127.0.0.1:8009', 'X-CSRF-Token': me.csrf_token },
    data: { version: game.version, ...game.legal_moves[0] },
  });
  expect(changed.ok()).toBe(true);
  const reload = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api' + path && response.request().method() === 'GET',
  );
  await page.getByRole('link', { name: 'Resume single-player', exact: true }).click();
  expect((await (await reload).json()).version).toBe((await changed.json()).version);
  await expect(page.getByRole('grid')).toBeVisible();
});

test('late real history/statistics responses cannot populate a new account after logout', async ({
  page,
}) => {
  await login(page);
  await expect(rows(page)).toHaveCount(3);
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let received!: () => void;
  const arrived = new Promise<void>((resolve) => {
    received = resolve;
  });
  let count = 0;
  const armed = new Set<string>();
  await page.route(
    /\/api\/(?:history\/games|personal\/(?:single-player|online))\?/,
    async (route) => {
      const key = new URL(route.request().url()).pathname;
      if (armed.has(key)) return route.continue();
      armed.add(key);
      const response = await route.fetch();
      if (++count === 3) received();
      await held;
      await route.fulfill({ response }).catch(() => {});
    },
  );
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await arrived;
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await credentials(page, 'PersonalObserver');
  await expect(page.getByText('No completed games yet.')).toBeVisible();
  release();
  await page.unrouteAll({ behavior: 'wait' });
  await expect(rows(page)).toHaveCount(0);
  await expect(accountPanel(page).getByText('No games recorded yet.')).toBeVisible();
  await expect(onlinePanel(page).getByText('No games recorded yet.')).toBeVisible();
});

test('history review is read-only, validated and preserves authoritative records', async ({
  page,
  request,
}) => {
  const before = await snapshot(request);
  const writes: string[] = [];
  page.on('request', (r) => {
    if (
      new URL(r.url()).pathname.startsWith('/api/') &&
      r.method() !== 'GET' &&
      !r.url().includes('/auth/')
    )
      writes.push(r.url());
  });
  await login(page, 'PersonalOdin');
  for (const [category, id] of [
    ['online', before.ids.completed],
    ['online', before.ids.cancelled],
    ['online', before.ids.voided],
    ['account-single-player', before.ids.account],
  ]) {
    await page.goto(`/archive?review=${category}&selected=${id}`);
    await expect(page.getByRole('grid')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Concede', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /analysis|export|seek|undo/i })).toHaveCount(0);
    if (id === before.ids.completed) {
      await page.getByRole('button', { name: 'Next move', exact: true }).click();
      await expect(page.getByText('Move 1 of 1')).toBeVisible();
    }
    if (id === before.ids.voided)
      await expect(page.locator('.detail-row', { hasText: 'Lifecycle' })).toContainText('Voided');
    if (id === before.ids.cancelled)
      await expect(page.locator('.detail-row', { hasText: 'Lifecycle' })).toContainText(
        'Cancelled',
      );
  }
  // Account single-player review may run authorized analysis in separate tables.
  // No authoritative game/result write is allowed from the review.
  expect(writes.filter((url) => !/\/analysis$/.test(new URL(url).pathname))).toEqual([]);
  expect((await snapshot(request)).fingerprint).toBe(before.fingerprint);
});

test('Dashboard lobby has shared card spacing in loading, empty, populated and error states', async ({
  page,
}) => {
  let state: 'loading' | 'empty' | 'populated' | 'error' = 'loading';
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/online/games?*', async (route) => {
    if (state === 'loading') await held;
    if (state === 'error') {
      await route.fulfill({ status: 503, json: { detail: 'Temporarily unavailable' } });
      return;
    }
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      response,
      json: { ...data, games: state === 'empty' ? [] : data.games.slice(0, 2) },
    });
  });
  await login(page);
  const lobby = page.getByRole('region', { name: 'Online lobby', exact: true });
  const spacing = async (name: string) => {
    const dimensions = await page.evaluate(() => {
      const card = document.querySelector<HTMLElement>('.dashboard-lobby')!;
      const reference = document.querySelector<HTMLElement>('.personal-section')!;
      const overview = document.querySelector<HTMLElement>('.dashboard-overview')!;
      const panels = Array.from(document.querySelectorAll<HTMLElement>('.personal-section'));
      const rect = card.getBoundingClientRect();
      const css = getComputedStyle(card);
      return {
        left: parseFloat(css.paddingLeft),
        right: parseFloat(css.paddingRight),
        reference: parseFloat(getComputedStyle(reference).paddingLeft),
        heading:
          card.querySelector('h2')!.getBoundingClientRect().left -
          rect.left -
          parseFloat(css.borderLeftWidth),
        gap: rect.top - Math.max(...panels.map((panel) => panel.getBoundingClientRect().bottom)),
        expectedGap: parseFloat(getComputedStyle(overview).rowGap),
        contained: Array.from(card.querySelectorAll('h2, h3, p, button, article')).every((node) => {
          const child = node.getBoundingClientRect();
          return child.left >= rect.left && child.right <= rect.right + 1;
        }),
        fits:
          document.documentElement.scrollWidth <= innerWidth &&
          card.scrollWidth <= card.clientWidth,
      };
    });
    expect(dimensions.left).toBeCloseTo(dimensions.reference, 1);
    expect(dimensions.right).toBeCloseTo(dimensions.reference, 1);
    expect(dimensions.heading).toBeCloseTo(dimensions.reference, 1);
    expect(dimensions.gap).toBeCloseTo(dimensions.expectedGap, 1);
    expect(dimensions.contained).toBe(true);
    expect(dimensions.fits).toBe(true);
    await lobby.screenshot({ path: test.info().outputPath(`lobby-${name}.png`) });
  };
  await expect(lobby.getByText('Loading online games…')).toBeVisible();
  await spacing('loading');
  state = 'empty';
  release();
  await expect(lobby.getByText('No games on this page.')).toBeVisible();
  await spacing('empty');
  state = 'populated';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(lobby.locator('.online-card')).toHaveCount(2);
  await spacing('populated');
  await page.screenshot({
    path: test.info().outputPath('dashboard-shared-spacing.png'),
    fullPage: true,
  });
  state = 'error';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(lobby.getByRole('button', { name: 'Retry connection' })).toBeVisible();
  await spacing('error');
  state = 'populated';
  await lobby.getByRole('button', { name: 'Retry connection' }).click();
  await expect(lobby.getByRole('button', { name: 'Retry connection' })).toHaveCount(0);
  await page.getByRole('button', { name: 'New online game', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Create an online game' });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByText('Disconnecting, closing the browser or logging out does not pause clocks.'),
  ).toHaveCount(0);
});
test('Hall account review runs the advisor and shows winning chances', async ({
  page,
  request,
}) => {
  const state = await snapshot(request);
  await login(
    page,
    'PersonalArcher',
    `/archive?review=account-single-player&selected=${state.ids.account}`,
  );
  await expect(page.getByRole('grid')).toBeVisible();
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  await analytics
    .getByRole('combobox', { name: 'Move advisor', exact: true })
    .selectOption('profile:baseline');
  const panel = page.getByRole('region', { name: 'Agent evaluations' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('meter').first()).toBeVisible();
  await expect(
    page.getByRole('grid', { name: 'brandubh game board' }).locator('.hint-overlay'),
  ).toBeVisible();
  // The review stays read-only while the historical advisor runs.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('compact and full leaderboards default to Copenhagen and persist ruleset browsing', async ({
  page,
}) => {
  await login(page);
  const board = page.locator('.leaderboard-compact');
  await expect(board.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  // Default is Copenhagen; the fixture only has rated Tablut games.
  await expect(board.getByLabel('Ruleset')).toHaveValue('copenhagen');
  await expect(board.getByText('No rated games in this pool yet.')).toBeVisible();
  await board.getByLabel('Ruleset').selectOption('tablut');
  await expect(board.getByText(/PersonalArcher/)).toBeVisible();
  await expect(board.getByText(/PersonalKeeper/)).toBeVisible();
  await expect(board.locator('table.leaderboard')).toHaveCount(1);
  await expect(board.getByText('Qualified standings')).toHaveCount(0);
  await expect(board.getByText(/Your standing/)).toHaveCount(0);
  await board.screenshot({ path: test.info().outputPath('leaderboard-compact.png') });
  // The same selection persists to the full view and across reload.
  await page.goto('/leaderboard');
  await expect(page.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  await expect(page.getByLabel('Ruleset')).toHaveValue('tablut');
  await expect(page.getByRole('cell', { name: /PersonalArcher/ })).toBeVisible();
  await page.getByLabel('Ruleset').selectOption('copenhagen');
  await expect(page.getByText('No rated games in this pool yet.')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Ruleset')).toHaveValue('copenhagen');
  await page.screenshot({
    path: test.info().outputPath('leaderboard-full.png'),
    fullPage: true,
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('desktop play screens fit 1920x1080 without document scrolling', async ({ page, request }) => {
  const state = await snapshot(request);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await login(page);
  const fits = () =>
    page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    }));
  // Online active game uses the shared layout and a viewport-capped board.
  await page.goto('/online/games/' + state.ids.active);
  await expect(page.getByRole('heading', { name: 'Online game', exact: true })).toBeVisible();
  await expect(page.getByRole('grid')).toBeVisible();
  const online = await fits();
  expect(online.scrollHeight).toBeLessThanOrEqual(online.innerHeight + 1);
  // Single-player unfinished game shares the same wrapper and heading.
  await page.goto('/games/' + state.ids.unfinished_account);
  await expect(page.getByRole('heading', { name: 'Single player game' })).toBeVisible();
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByText('Every move tells a saga.')).toHaveCount(0);
  const single = await fits();
  expect(single.scrollHeight).toBeLessThanOrEqual(single.innerHeight + 1);
});
