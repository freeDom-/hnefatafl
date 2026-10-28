import { test, expect, type Page, type Browser, type BrowserContext } from '@playwright/test';
const password = 'online-browser-fixture-password';
async function login(page: Page, username: string) {
  await page.goto('/online');
  await page.getByLabel('Username or email').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Online lobby' })).toBeVisible();
}
async function opponent(browser: Browser, name = 'OnlineKeeper') {
  const context = await browser.newContext();
  const page = await context.newPage();
  await login(page, name);
  return { context, page };
}
async function create(page: Page, mode = 'single', side = 'attackers', seconds = '259200') {
  if (!(await page.getByRole('dialog', { name: 'Create an online game' }).isVisible()))
    await page.getByRole('button', { name: 'New online game', exact: true }).click();
  await page.getByLabel('Creator side').selectOption(side);
  await page.getByLabel('Match mode').selectOption(mode);
  const duration = Number(seconds);
  await page.getByLabel('Days', { exact: true }).fill(String(Math.floor(duration / 86400)));
  await page
    .getByLabel('Hours', { exact: true })
    .fill(String(Math.floor((duration % 86400) / 3600)));
  await page.getByLabel('Minutes', { exact: true }).fill(String((duration % 3600) / 60));
  await page.getByRole('button', { name: 'Create game', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Online game', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel waiting game' })).toBeEnabled();
  return page.url();
}
async function join(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole('button', { name: 'Join game', exact: true }).click();
  await expect(page.getByLabel(/(Attackers|Defenders) to move/)).toBeVisible();
}
async function refresh(page: Page) {
  // The game room has no manual refresh control; wake the automatic poll.
  // Use `online` (not `focus`, which would also re-check and clear an offline session).
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
}
async function concede(page: Page) {
  await page.getByRole('button', { name: 'Concede', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm concession', exact: true }).click();
  await expect(page.getByText(/win · concession/)).toBeVisible();
}
const extra: BrowserContext[] = [];
test.beforeEach(async ({ request }) => {
  await request.post('http://127.0.0.1:8006/reset');
});
test.afterEach(async () => {
  for (const context of extra.splice(0)) await context.close();
});

test('two accounts play, reconnect, finish and view a local read-only replay', async ({
  page,
  browser,
}) => {
  await login(page, 'OnlineArcher');
  const other = await opponent(browser);
  extra.push(other.context);
  const url = await create(page);
  const id = new URL(url).pathname.split('/').pop()!;
  await join(other.page, url);
  await page.goto('/dashboard');
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.getByRole('tab', { name: 'My games', exact: true }).click();
  await page.getByRole('button', { name: 'Open game', exact: true }).first().click();
  await expect(page).toHaveURL(url);
  await refresh(page);
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toBeVisible();
  // 1440x1400 keeps the grid column, not the height cap, as the binding
  // constraint: the board column must fill its track instead of shrink-to-fit
  // around its own toolbar content, which changes with the first move.
  await page.setViewportSize({ width: 1440, height: 1400 });
  const measure = () =>
    page.evaluate(() => {
      const track = getComputedStyle(
        document.querySelector('.game-layout')!,
      ).gridTemplateColumns.split(' ')[1];
      const width = (selector: string) =>
        Math.round(document.querySelector(selector)!.getBoundingClientRect().width);
      return {
        track: Math.round(Number.parseFloat(track)),
        battlefield: width('.battlefield'),
        board: width('.board'),
      };
    });
  const beforeMove = await measure();
  expect(beforeMove.battlefield).toBe(beforeMove.track);
  await page.getByRole('gridcell', { name: 'd7, attacker', exact: true }).click();
  await page.getByRole('gridcell', { name: 'b7, empty', exact: true }).click();
  await expect(page.getByRole('gridcell', { name: 'b7, attacker', exact: true })).toBeVisible();
  expect((await measure()).board).toBe(beforeMove.board);
  const capturedRow = page.locator('.armies .captured-row');
  const afterMove = await (await page.request.get(`/api/online/games/${id}`)).json();
  await expect(capturedRow).toHaveText(
    `Captured ${afterMove.captured.attackers} ${afterMove.captured.defenders}`,
  );
  await other.context.setOffline(true);
  await refresh(other.page);
  await expect(
    other.page.getByText('Online service unavailable or stale. Retry to reconnect.'),
  ).toBeVisible();
  await other.context.setOffline(false);
  await other.page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect(
    other.page.getByRole('gridcell', { name: 'b7, attacker', exact: true }),
  ).toBeVisible();
  await concede(other.page);
  await refresh(page);
  await expect(page.getByText('Attackers win · concession')).toBeVisible();
  expect((await measure()).board).toBe(beforeMove.board);
  const afterConcession = await (await page.request.get(`/api/online/games/${id}`)).json();
  await expect(capturedRow).toHaveText(
    `Captured ${afterConcession.captured.attackers} ${afterConcession.captured.defenders}`,
  );
  await page.getByRole('button', { name: 'View replay' }).click();
  await expect(page.getByRole('button', { name: 'Return to live board' })).toBeVisible();
  await expect(page.getByRole('gridcell', { name: 'd7, attacker', exact: true })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await page.getByRole('button', { name: 'Next move', exact: true }).click();
  await expect(page.getByRole('gridcell', { name: 'b7, attacker', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Return to live board' }).click();
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await expect(page.getByRole('grid')).toHaveCount(0);
});

test('simultaneous games swap sides and keep separate versions', async ({ page, browser }) => {
  await login(page, 'OnlineArcher');
  const other = await opponent(browser);
  extra.push(other.context);
  const url = await create(page, 'simultaneous', 'defenders');
  await join(other.page, url);
  await refresh(page);
  const id = new URL(url).pathname.split('/').pop()!;
  const detail = await (await page.request.get(`/api/online/games/${id}`)).json();
  const siblingId = detail.match.games.find((game: { id: string }) => game.id !== id).id;
  const switcher = page.getByRole('navigation', { name: 'Active games' });
  const sibling = switcher.locator(`[data-game-id="${siblingId}"]`);
  await expect(sibling).toBeVisible();
  await sibling.click();
  await expect(page.getByText('Game 2', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toBeVisible();
  await concede(page);
  // The return game completed; the still-active first leg remains in the switcher.
  await switcher.locator(`[data-game-id="${id}"]`).click();
  await expect(page.getByLabel(/(Attackers|Defenders) to move/)).toBeVisible();
});

test('sequential discovers the return game after the first result', async ({ page, browser }) => {
  await login(page, 'OnlineArcher');
  const other = await opponent(browser);
  extra.push(other.context);
  const url = await create(page, 'sequential');
  await join(other.page, url);
  await refresh(page);
  const id = new URL(url).pathname.split('/').pop()!;
  await concede(page);
  // The return game activates after the first result and is reachable by URL.
  const detail = await (await page.request.get(`/api/online/games/${id}`)).json();
  const siblingId = detail.match.games.find((game: { id: string }) => game.id !== id).id;
  await page.goto(`/online/games/${siblingId}`);
  await expect(page.getByText('Game 2', { exact: true })).toBeVisible();
  await other.page.goto(page.url());
  await concede(other.page);
  await refresh(page);
  await expect(page.getByText('Defenders win · concession')).toBeVisible();
});

test('stale action is reloaded once and authenticated spectators cannot mutate or analyse', async ({
  page,
  browser,
}) => {
  await login(page, 'OnlineArcher');
  const other = await opponent(browser);
  extra.push(other.context);
  const viewer = await opponent(browser, 'OnlineViewer');
  extra.push(viewer.context);
  const url = await create(page, 'single', 'random');
  await expect(page.getByText('Unassigned', { exact: false }).first()).toBeVisible();
  await join(other.page, url);
  await refresh(page);
  await viewer.page.goto(url);
  await expect(viewer.page.getByRole('button', { name: 'Concede', exact: true })).toHaveCount(0);
  const denied = await viewer.page.evaluate(async () => {
    const session = await (await fetch('/api/auth/me')).json();
    const id = location.pathname.split('/').pop();
    const game = await (await fetch(`/api/online/games/${id}`)).json();
    return (
      await fetch(`/api/online/games/${id}/concede`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token },
        body: JSON.stringify({ version: game.version }),
      })
    ).status;
  });
  expect(denied).toBe(403);
  const actor = (await page.getByRole('button', { name: 'Concede', exact: true }).count())
    ? page
    : other.page;
  await actor.route(
    '**/api/online/games/*/concede',
    async (route) => {
      const request = route.request();
      // A competing request commits first; the UI's original version is now stale.
      const competing = await actor.request.post(request.url(), {
        headers: request.headers(),
        data: request.postData(),
      });
      expect(competing.status()).toBe(200);
      await route.continue();
    },
    { times: 1 },
  );
  await actor.getByRole('button', { name: 'Concede', exact: true }).click();
  await actor.getByRole('button', { name: 'Confirm concession', exact: true }).click();
  await expect(actor.getByText(/State changed or adjudication is paused/)).toBeVisible();
  await expect(actor.getByText(/win · concession/)).toBeVisible();
  const guard = await viewer.page.evaluate(
    async () => (await fetch(`/api/games/${location.pathname.split('/').pop()}/analytics`)).status,
  );
  expect(guard).toBe(409);
});

test('disabled admission preserves existing seats; overdue paused game awaits adjudication', async ({
  page,
  browser,
  request,
}) => {
  await login(page, 'OnlineArcher');
  const other = await opponent(browser);
  extra.push(other.context);
  const url = await create(page, 'single', 'attackers', '60');
  await join(other.page, url);
  await refresh(page);
  await request.post('http://127.0.0.1:8006/verification', {
    data: { name: 'OnlineArcher', verified: false },
  });
  await request.post('http://127.0.0.1:8006/admission', { data: { enabled: false } });
  await refresh(page);
  await expect(page.getByText(/New online games and joining are currently disabled/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toBeVisible();
  // Advance the fixture's authoritative clock and the device clock independently.
  await request.post('http://127.0.0.1:8006/advance', { data: { seconds: 120 } });
  await page.clock.setFixedTime(new Date(Date.now() + 120000));
  await page.reload();
  await expect(page.getByText(/Awaiting adjudication/)).toBeVisible();
  await page.getByRole('button', { name: 'Concede', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm concession', exact: true }).click();
  await expect(page.getByText(/State changed or adjudication is paused/)).toBeVisible();
  await expect(page.getByText('Active', { exact: true })).toBeVisible();
  await page.goto('/dashboard');
  await page.getByRole('button', { name: 'New online game', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Create game', exact: true })).toBeDisabled();
});

test('lobby automatically observes creation, join and completion and resynchronizes after reconnect and tab visibility', async ({
  page,
  browser,
}) => {
  await login(page, 'OnlineArcher');
  const viewer = await opponent(browser, 'OnlineViewer');
  extra.push(viewer.context);
  const close = viewer.page
    .getByRole('dialog', { name: 'Create an online game' })
    .getByRole('button', { name: 'Cancel', exact: true });
  await expect(close).toBeVisible();
  await close.click();
  const lobby = viewer.page.getByRole('region', { name: 'Online lobby', exact: true });
  await expect(lobby.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);
  await expect(lobby.getByText('Connected · refreshes automatically')).toHaveCount(0);
  const url = await create(page, 'single', 'attackers', '3600');
  const id = new URL(url).pathname.split('/').pop();
  const card = lobby.locator(`.online-card[data-game-id="${id}"]`);
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('Waiting');
  await viewer.context.setOffline(true);
  await expect(lobby.getByRole('alert')).toContainText('Lobby data is stale');
  const keeper = await opponent(browser);
  extra.push(keeper.context);
  await join(keeper.page, url);
  await viewer.context.setOffline(false);
  await viewer.page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect(card).toContainText('Active');
  await expect(lobby.getByRole('alert')).toHaveCount(0);
  await viewer.page.evaluate(() =>
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }),
  );
  await concede(page);
  await viewer.page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  // Terminal games leave every lobby tab.
  await expect(card).toHaveCount(0);
  await expect(lobby.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);
});

test('online setup shares modal spacing, restores focus on Escape and rejects invalid aggregate durations', async ({
  page,
}) => {
  await login(page, 'OnlineArcher');
  const online = page.getByRole('dialog', { name: 'Create an online game', exact: true });
  await expect(online).toBeVisible();
  const modalPadding = await online.evaluate((n) => getComputedStyle(n).padding);
  const formPadding = await online.locator('form').evaluate((n) => getComputedStyle(n).padding);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'New single-player game', exact: true }).click();
  const single = page.getByRole('dialog', { name: 'Begin a new saga' });
  await expect(single).toBeVisible();
  expect(await single.evaluate((n) => getComputedStyle(n).padding)).toBe(modalPadding);
  expect(await single.locator('form').evaluate((n) => getComputedStyle(n).padding)).toBe(
    formPadding,
  );
  await page.keyboard.press('Escape');
  const trigger = page.getByRole('button', { name: 'New online game', exact: true });
  await trigger.click();
  await expect(online).toBeVisible();
  expect(await online.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  const ruleset = online.getByRole('combobox', { name: 'Ruleset', exact: true });
  await expect(ruleset).toBeEnabled();
  await ruleset.focus();
  await expect(ruleset).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await trigger.click();
  for (const [label, max] of [
    ['Days', '14'],
    ['Hours', '23'],
    ['Minutes', '59'],
  ]) {
    const input = online.getByLabel(label, { exact: true });
    await expect(input).toHaveAttribute('min', '0');
    await expect(input).toHaveAttribute('max', max);
    await expect(input).toHaveAttribute('step', '1');
    await input.fill('0');
  }
  const posts: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/online/games')
      posts.push(r.postData()!);
  });
  await online.getByRole('button', { name: 'Create game', exact: true }).click();
  await expect(online.getByRole('alert')).toContainText('at least 1 minute');
  expect(posts).toEqual([]);
  await online.getByLabel('Days', { exact: true }).fill('14');
  await online.getByLabel('Minutes', { exact: true }).fill('1');
  await online.getByRole('button', { name: 'Create game', exact: true }).click();
  await expect(online.getByRole('alert')).toContainText('at most 14 days');
  expect(posts).toEqual([]);
  await online.getByLabel('Minutes', { exact: true }).fill('0');
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/api/online/games') && r.request().method() === 'POST',
  );
  await online.getByRole('button', { name: 'Create game', exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  expect((await response.json()).game.time_per_move).toBe(1209600);
  expect(JSON.parse(posts[0]).time_per_move).toBe(1209600);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
