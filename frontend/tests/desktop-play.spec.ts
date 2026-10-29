import { expect, test } from './auth-fixture';
import { historicalGame } from './historical-fixture';

test('desktop play uses the available space without overlapping at any interface scale', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'Desktop geometry; mobile has separate coverage.');
  const response = await historicalGame(request, {
    data: { ruleset: 'copenhagen', mode: 'human-human' },
  });
  expect(response.ok()).toBe(true);
  const game = await response.json();
  const moved = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/moves`, {
    data: { ...game.legal_moves[0], version: game.version },
  });
  expect(moved.ok()).toBe(true);
  await page.goto(`/games/${game.id}`);
  await page.getByRole('button', { name: /Starting position/ }).click();
  await expect(page.getByText('Read-only history', { exact: true })).toBeVisible();

  for (const viewport of [
    { width: 1280, height: 720 },
    { width: 1366, height: 620 },
    { width: 1920, height: 730 },
    { width: 1920, height: 1080 },
    { width: 2560, height: 1440 },
    { width: 3840, height: 2160 },
  ]) {
    await page.setViewportSize(viewport);
    for (const density of ['compact', 'default', 'large']) {
      // Exercise the same density tokens as Appearance, without leaving history.
      await page.locator('html').evaluate((root, value) => (root.dataset.density = value), density);
      // Let size-container queries settle after viewport/density changes.
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      const geometry = await page.evaluate(() => {
        const box = (selector: string) =>
          document.querySelector(selector)!.getBoundingClientRect().toJSON();
        return {
          sidebar: box('.game-sidebar'),
          board: box('.board'),
          labels: box('.board-with-labels'),
          history: box('.desktop-inspector'),
          status: box('.history-review-status'),
          headings: box('.history-columns'),
          footer: box('.site-footer'),
          documentWidth: document.documentElement.scrollWidth,
          documentHeight: document.documentElement.scrollHeight,
        };
      });
      const context = `${viewport.width}×${viewport.height}, ${density}`;
      expect(geometry.sidebar.width, context).toBeGreaterThanOrEqual(250);
      expect(geometry.history.width, context).toBeGreaterThanOrEqual(260);
      expect(geometry.status.bottom, context).toBeLessThanOrEqual(geometry.headings.top);
      expect(geometry.labels.left - geometry.sidebar.right, context).toBeGreaterThanOrEqual(31);
      expect(geometry.history.left - geometry.labels.right, context).toBeGreaterThanOrEqual(31);
      expect(geometry.labels.bottom, context).toBeLessThanOrEqual(geometry.footer.top);
      expect(geometry.sidebar.bottom, context).toBeLessThanOrEqual(geometry.footer.top);
      expect(geometry.board.width, context).toBeGreaterThan(300);
      expect(Math.abs(geometry.board.width - geometry.board.height), context).toBeLessThan(1);
      expect(geometry.documentWidth, context).toBeLessThanOrEqual(viewport.width);
      expect(geometry.documentHeight, context).toBeLessThanOrEqual(viewport.height + 1);
      if (viewport.width === 3840) {
        expect(geometry.labels.left - geometry.sidebar.right, context).toBeGreaterThanOrEqual(80);
        expect(geometry.history.left - geometry.labels.right, context).toBeGreaterThanOrEqual(80);
        expect(geometry.sidebar.width, context).toBeGreaterThanOrEqual(400);
        expect(geometry.history.width, context).toBeGreaterThanOrEqual(420);
        expect(geometry.board.width, context).toBeGreaterThan(1500);
      }
      if (
        (viewport.width === 1280 && density === 'default') ||
        (viewport.width === 3840 && density === 'compact')
      ) {
        await page.screenshot({ path: info.outputPath(`${viewport.width}-${density}.png`) });
      }
    }
  }
});

test('short desktop windows have one scroll surface per tab, including long histories', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'Desktop height and nested-scroll regression.');
  const created = await historicalGame(request, {
    data: { ruleset: 'copenhagen', mode: 'human-human' },
  });
  expect(created.ok()).toBe(true);
  let game = await created.json();
  for (let n = 0; n < 60 && !game.winner; n++) {
    const move = game.legal_moves[(n * 37 + 11) % game.legal_moves.length];
    const response = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/moves`, {
      data: { ...move, version: game.version },
    });
    expect(response.ok()).toBe(true);
    game = await response.json();
  }
  expect(game.total_moves).toBeGreaterThan(24);
  await page.setViewportSize({ width: 1920, height: 730 });
  await page.goto(`/games/${game.id}`);
  await expect(page.locator('.desktop-inspector')).toBeVisible();
  await expect(page.locator('.game-details .detail-row')).toHaveCount(0);
  await expect(page.locator('.armies .player-game-details')).toContainText('Random seed');
  const board = await page.locator('.board').boundingBox();
  const assertSingleScroller = async (selector: string) => {
    const state = await page.locator(selector).evaluate((node) => {
      const scrolls = (element: Element) =>
        /auto|scroll/.test(getComputedStyle(element).overflowY) &&
        element.scrollHeight > element.clientHeight + 1;
      const parents = [];
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        if (scrolls(parent)) parents.push(parent.className || parent.tagName);
      }
      return { scrolls: scrolls(node), parents };
    });
    expect(state).toEqual({ scrolls: true, parents: [] });
  };
  await assertSingleScroller('.move-list');
  await page.locator('.move-list').evaluate((node) => (node.scrollTop = 0));
  await page.getByRole('button', { name: /Starting position/ }).click();
  await expect(page.getByText('Read-only history', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Analytics', exact: true }).click();
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  await analytics
    .getByRole('combobox', { name: 'Move advisor', exact: true })
    .selectOption('custom');
  await analytics.getByRole('combobox', { name: 'Algorithm', exact: true }).selectOption('mcts');
  await assertSingleScroller('.desktop-tab-content:not([hidden])');
  await expect(
    analytics.getByRole('spinbutton', { name: 'Temperature', exact: true }),
  ).toBeAttached();
  await page.getByRole('tab', { name: new RegExp(`^Moves ${game.total_moves}$`) }).click();
  await assertSingleScroller('.move-list');
  expect(await page.locator('.board').boundingBox()).toEqual(board);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBe(
    true,
  );
  await page.screenshot({ path: info.outputPath('1920-730-history.png') });
});
