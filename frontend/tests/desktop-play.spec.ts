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
          history: box('.history-panel'),
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
      expect(geometry.board.width, context).toBeGreaterThan(350);
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
