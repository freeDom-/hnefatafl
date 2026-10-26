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
  await page.getByLabel('Creator side').selectOption(side);
  await page.getByLabel('Match mode').selectOption(mode);
  await page.getByLabel('Seconds per move').fill(seconds);
  await page.getByRole('button', { name: 'Create game', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Online game', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel waiting game' })).toBeEnabled();
  return page.url();
}
async function join(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole('button', { name: 'Join game', exact: true }).click();
  await expect(page.getByText('Active', { exact: true })).toBeVisible();
}
async function refresh(page: Page) {
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
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
  await join(other.page, url);
  await page.getByRole('button', { name: 'Back to online lobby', exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.getByRole('button', { name: 'My games', exact: true }).click();
  await page.getByRole('button', { name: 'Open game', exact: true }).first().click();
  await expect(page).toHaveURL(url);
  await refresh(page);
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toBeVisible();
  await page.getByRole('gridcell', { name: 'd7, attacker', exact: true }).click();
  await page.getByRole('gridcell', { name: 'b7, empty', exact: true }).click();
  await expect(page.getByRole('gridcell', { name: 'b7, attacker', exact: true })).toBeVisible();
  await other.context.setOffline(true);
  await refresh(other.page);
  await expect(other.page.getByText('Stale state. Reconnecting…')).toBeVisible();
  await other.context.setOffline(false);
  await other.page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect(
    other.page.getByRole('gridcell', { name: 'b7, attacker', exact: true }),
  ).toBeVisible();
  await concede(other.page);
  await refresh(page);
  await expect(page.getByText('Attackers win · concession')).toBeVisible();
  await page.getByRole('button', { name: 'View replay' }).click();
  await expect(page.getByText('Historical replay · read only')).toBeVisible();
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
  const nav = page.getByRole('navigation', { name: 'Match games' });
  await expect(nav.getByRole('button')).toHaveCount(2);
  await nav.getByRole('button', { name: /Game 2/ }).click();
  await expect(page.getByText('Attackers: OnlineArcher · You')).toBeVisible();
  await expect(page.getByText('Creator side: Attackers')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toBeVisible();
  await concede(page);
  await page
    .getByRole('navigation', { name: 'Match games' })
    .getByRole('button', { name: /Game 1/ })
    .click();
  await expect(page.getByText('Active', { exact: true })).toBeVisible();
  await expect(
    page
      .getByRole('navigation', { name: 'Match games' })
      .getByRole('button', { name: /Game 2 · Completed/ }),
  ).toBeVisible();
});

test('sequential discovers the return game after the first result', async ({ page, browser }) => {
  await login(page, 'OnlineArcher');
  const other = await opponent(browser);
  extra.push(other.context);
  const url = await create(page, 'sequential');
  await join(other.page, url);
  await refresh(page);
  await expect(
    page.getByRole('navigation', { name: 'Match games' }).getByRole('button'),
  ).toHaveCount(1);
  await concede(page);
  await page
    .getByRole('navigation', { name: 'Match games' })
    .getByRole('button', { name: /Game 2/ })
    .click();
  await expect(page.getByText('Defenders: OnlineArcher · You')).toBeVisible();
  await expect(page.getByText('Creator side: Defenders')).toBeVisible();
  await expect(page.getByText('Active', { exact: true })).toBeVisible();
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
  await page.getByRole('button', { name: 'Back to online lobby' }).click();
  await expect(page.getByRole('button', { name: 'Create game', exact: true })).toBeDisabled();
});
