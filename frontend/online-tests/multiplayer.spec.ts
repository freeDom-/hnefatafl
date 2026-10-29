import { openMyGames } from '../tests/play-helpers';
import { test, expect, type Page, type Browser, type BrowserContext } from '@playwright/test';
const password = 'online-browser-fixture-password';
async function login(page: Page, username: string) {
  await page.goto('/online');
  await page.getByLabel('Username or email').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Online games' })).toBeVisible();
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
  await expect(page.getByRole('button', { name: 'Cancel waiting game' })).toHaveClass(/secondary/);
  await expect(page.getByRole('heading', { name: 'Waiting for another player' })).toBeVisible();
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

test('online creation fits desktop screens in two columns and exposes increments only for total clocks', async ({
  page,
  isMobile,
}, info) => {
  test.setTimeout(120000);
  await login(page, 'OnlineArcher');
  const sizes = isMobile
    ? [{ width: 390, height: 844 }]
    : [
        { width: 1920, height: 1080 },
        { width: 1568, height: 785 },
        { width: 1280, height: 720 },
      ];
  for (const size of sizes) {
    await page.setViewportSize(size);
    for (const language of ['en', 'de']) {
      await page.evaluate((value) => localStorage.setItem('hnefatafl-language', value), language);
      await page.goto('/dashboard?setup=online');
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      const control = dialog.getByRole('combobox', {
        name: language === 'en' ? 'Time control' : 'Zeitkontrolle',
        exact: true,
      });
      await expect(control).toBeEnabled();
      for (const density of ['compact', 'default', 'large']) {
        await page.locator('html').evaluate((node, value) => {
          node.dataset.density = value;
        }, density);
        for (const mode of ['per_move', 'total']) {
          await control.selectOption(mode);
          const increment = dialog.getByLabel(
            language === 'en' ? 'Increment per move (seconds)' : 'Zeitbonus pro Zug (Sekunden)',
            { exact: true },
          );
          if (mode === 'total') await expect(increment).toBeVisible();
          else await expect(increment).toHaveCount(0);
          const geometry = await dialog.evaluate((node) => {
            const columns = [...node.querySelectorAll('.online-create-fields > .form-section')].map(
              (column) => column.getBoundingClientRect(),
            );
            const bounds = node.getBoundingClientRect();
            return {
              top: bounds.top,
              bottom: bounds.bottom,
              left: bounds.left,
              right: bounds.right,
              scroll: node.scrollHeight - node.clientHeight,
              columnGap: columns[1].left - columns[0].right,
              sameColumn: Math.abs(columns[1].left - columns[0].left) < 1,
              overflow: document.documentElement.scrollWidth > innerWidth,
            };
          });
          const context = `${size.width}×${size.height}, ${language}, ${density}, ${mode}`;
          expect(geometry.top, context).toBeGreaterThanOrEqual(0);
          expect(geometry.bottom, context).toBeLessThanOrEqual(size.height);
          expect(geometry.left, context).toBeGreaterThanOrEqual(0);
          expect(geometry.right, context).toBeLessThanOrEqual(size.width);
          expect(geometry.overflow, context).toBe(false);
          if (isMobile) expect(geometry.sameColumn, context).toBe(true);
          else {
            expect(geometry.scroll, context).toBeLessThanOrEqual(1);
            expect(geometry.columnGap, context).toBeGreaterThan(0);
          }
          if (
            language === 'en' &&
            density === 'default' &&
            mode === 'total' &&
            (size.width === 1568 || isMobile)
          )
            await page.screenshot({ path: info.outputPath('online-create.png') });
        }
      }
    }
  }
  await page.evaluate(() => localStorage.setItem('hnefatafl-language', 'en'));
  await page.goto('/dashboard?setup=online');
  await page.getByRole('combobox', { name: 'Time control', exact: true }).selectOption('total');
  await page.getByLabel('Increment per move (seconds)', { exact: true }).fill('12');
  await page.getByRole('combobox', { name: 'Time control', exact: true }).selectOption('per_move');
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/online/games') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Create game', exact: true }).click();
  expect((await (await created).json()).game.increment_seconds).toBe(0);
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
  for (const viewport of [
    { width: 1280, height: 720 },
    { width: 3840, height: 2160 },
  ]) {
    await page.setViewportSize(viewport);
    await page.locator('html').evaluate((root, compact) => {
      root.dataset.density = compact ? 'compact' : 'default';
    }, viewport.width === 3840);
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    const geometry = await page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
      const board = box('.board-with-labels');
      return {
        leftGap: board.left - box('.game-sidebar').right,
        rightGap: box('.history-panel').left - board.right,
        controlsBottom: box('.board-toolbar').bottom,
        footerTop: box('.site-footer').top,
        documentWidth: document.documentElement.scrollWidth,
      };
    });
    expect(geometry.leftGap).toBeGreaterThanOrEqual(viewport.width === 3840 ? 80 : 31);
    expect(geometry.rightGap).toBeGreaterThanOrEqual(viewport.width === 3840 ? 80 : 31);
    expect(geometry.controlsBottom).toBeLessThanOrEqual(geometry.footerTop);
    expect(geometry.documentWidth).toBeLessThanOrEqual(viewport.width);
  }
  await page.locator('html').evaluate((root) => (root.dataset.density = 'default'));
  // 1440x1400 keeps the grid column, not the height cap, as the binding
  // constraint: the board column must fill its track instead of shrink-to-fit
  // around its own toolbar content, which changes with the first move.
  await page.setViewportSize({ width: 1440, height: 1400 });
  await expect(page.locator('.game-layout')).toBeVisible();
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
  await page.getByRole('button', { name: 'Replay to move 1', exact: true }).click();
  await expect(page.getByRole('gridcell', { name: 'b7, attacker', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Return to live board' })).toHaveCount(0);
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
  await openMyGames(page);
  const switcher = page.getByRole('navigation', { name: /^(Active games|My games)$/ });
  const sibling = switcher.locator(`[data-game-id="${siblingId}"]`);
  await expect(sibling).toBeVisible();
  await sibling.click();
  await expect(page.getByText('Game 2', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toBeVisible();
  await concede(page);
  // The return game completed; the still-active first leg remains in the switcher.
  await openMyGames(page);
  await switcher.locator(`[data-game-id="${id}"]`).click();
  await expect(page.getByLabel(/(Attackers|Defenders) to move/)).toBeVisible();
});

test('sequential discovers the return game after the first result', async ({
  page,
  browser,
}, testInfo) => {
  // Other cases retain active games, so isolate the one-active-game scenario per project.
  await login(page, `SwitcherArcher${testInfo.project.name}`);
  const other = await opponent(browser, `SwitcherKeeper${testInfo.project.name}`);
  extra.push(other.context);
  const url = await create(page, 'sequential');
  await join(other.page, url);
  await refresh(page);
  const id = new URL(url).pathname.split('/').pop()!;
  await concede(page);
  // The return game is the only active game after the first result.
  const detail = await (await page.request.get(`/api/online/games/${id}`)).json();
  const siblingId = detail.match.games.find((game: { id: string }) => game.id !== id).id;
  await openMyGames(page);
  const switcher = page.getByRole('navigation', { name: /^(Active games|My games)$/ });
  await expect(switcher.getByRole('button')).toHaveCount(1);
  await switcher.locator(`[data-game-id="${siblingId}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/online/games/${siblingId}$`));
  await expect(page.getByText('Game 2', { exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'My games', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(switcher.locator(`[data-game-id="${siblingId}"]`)).toHaveAttribute(
    'aria-current',
    'page',
  );
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

test('disabled admission preserves existing seats; timeout automatically completes the game', async ({
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
  await expect(page.getByText('Defenders win · timeout')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toHaveCount(0);
  const id = new URL(url).pathname.split('/').pop()!;
  const completed = await (await page.request.get(`/api/online/games/${id}`)).json();
  expect(completed.status).toBe('completed');
  expect(completed.result.termination_reason).toBe('timeout');
  await page.goto('/dashboard');
  await expect(page.getByRole('button', { name: 'New online game', exact: true })).toBeDisabled();
  await expect(
    page.getByText('Verify your email address before creating or joining online games.'),
  ).toBeVisible();
  await expect(page.getByText(/Creating and joining require/)).toHaveCount(0);
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

test('total clocks retain per-player balances and increments across moves and reload', async ({
  page,
  browser,
  request,
  isMobile,
}) => {
  await login(page, 'OnlineArcher');
  const other = await opponent(browser);
  extra.push(other.context);
  await page.getByLabel('Time control').selectOption('total');
  await page.getByLabel('Increment per move (seconds)').fill('5');
  const url = await create(page, 'single', 'attackers', '60');
  const id = new URL(url).pathname.split('/').pop()!;
  await join(other.page, url);
  await refresh(page);
  await request.post('http://127.0.0.1:8006/advance', { data: { seconds: 10 } });
  await page.getByRole('gridcell', { name: 'd7, attacker', exact: true }).click();
  await page.getByRole('gridcell', { name: 'b7, empty', exact: true }).click();
  await expect(page.getByLabel('Defenders to move')).toBeVisible();
  const moved = await (await page.request.get(`/api/online/games/${id}`)).json();
  expect(moved.time_control).toBe('total');
  expect(moved.increment_seconds).toBe(5);
  expect(moved.remaining_seconds).toEqual({ attackers: 55, defenders: 60 });
  await page.reload();
  await expect(page.getByRole('grid')).toBeVisible();
  if (isMobile) await page.getByRole('button', { name: 'Game options', exact: true }).click();
  await expect(page.getByText('Total time per player', { exact: true })).toBeVisible();
  await expect(page.getByText('Attackers remaining time', { exact: true })).toBeVisible();
  if (isMobile) await page.keyboard.press('Escape');
  await request.post('http://127.0.0.1:8006/advance', { data: { seconds: 60 } });
  await refresh(page);
  await expect(page.getByText('Attackers win · timeout')).toBeVisible();
  const finished = await (await page.request.get(`/api/online/games/${id}`)).json();
  expect(finished.remaining_seconds).toEqual({ attackers: 55, defenders: 0 });
  expect(finished.result.rating).toHaveLength(2);
});
