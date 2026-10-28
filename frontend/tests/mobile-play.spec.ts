import { expect, test, type Page } from './auth-fixture';
import { historicalGame } from './historical-fixture';
import { gameTab, openGameOptions, closeGameOptions } from './play-helpers';

async function fits(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const board = document.querySelector('.board')!.getBoundingClientRect();
        const toolbar = document.querySelector('.board-toolbar')!.getBoundingClientRect();
        const panel = document.querySelector('.mobile-inspector')!.getBoundingClientRect();
        return (
          document.documentElement.scrollHeight <= innerHeight &&
          document.documentElement.scrollWidth <= innerWidth &&
          board.top >= 0 &&
          board.bottom <= toolbar.top &&
          (innerWidth > innerHeight ? board.right <= panel.left : toolbar.bottom <= panel.top) &&
          panel.bottom <= innerHeight &&
          Math.abs(board.width - board.height) < 2
        );
      }),
    )
    .toBe(true);
}

test('mobile board, tabs, paged replay and advisor stay within the viewport', async ({
  page,
  request,
}, info) => {
  test.skip(
    info.project.name !== 'mobile',
    'Phone-specific screen layout; desktop gameplay has separate coverage.',
  );
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  for (const [ruleset, size] of [
    ['brandubh', 7],
    ['tablut', 9],
    ['copenhagen', 11],
  ] as const) {
    const response = await historicalGame(request, { data: { ruleset, mode: 'human-human' } });
    expect(response.ok()).toBe(true);
    let game = await response.json();
    for (let n = 0; n < 8 && !game.winner; n++) {
      const move = game.legal_moves[(n * 17 + 3) % game.legal_moves.length];
      const result = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/moves`, {
        data: { ...move, version: game.version },
      });
      expect(result.ok()).toBe(true);
      game = await result.json();
    }
    await page.setViewportSize({ width: 390, height: 780 });
    await page.goto('/games/' + game.id);
    await expect(page.getByRole('gridcell')).toHaveCount(size * size);
    await fits(page);
    const original = await page.locator('.board').boundingBox();
    await gameTab(page, 'players');
    await expect(page.getByRole('tabpanel').getByText('Captured', { exact: true })).toBeVisible();
    await fits(page);
    expect(await page.locator('.board').boundingBox()).toEqual(original);
    await gameTab(page, 'analytics');
    await page.getByRole('button', { name: 'Move advisor', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Move advisor', exact: true })
      .selectOption('profile:baseline');
    await closeGameOptions(page);
    await expect(page.getByRole('meter')).toHaveCount(1);
    await expect(page.locator('.hint-overlay')).toBeVisible();
    await fits(page);
    await gameTab(page, 'moves');
    await expect(page.getByRole('button', { name: /Replay to move/ })).toHaveCount(2);
    await page.getByRole('button', { name: 'Earlier moves' }).click();
    const seek = page.getByRole('button', { name: /Replay to move/ }).first();
    const ply = Number((await seek.getAttribute('aria-label'))!.match(/\d+/)![0]);
    await seek.click();
    await expect(page.locator('.mobile-play-subtitle')).toContainText('Move ' + ply);
    await fits(page);
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(page.getByRole('button', { name: /Replay to move/ })).toHaveCount(1);
    await fits(page);
    await openGameOptions(page);
    await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
    await closeGameOptions(page);
    await expect(page.getByRole('tab', { name: /^Züge/ })).toBeVisible();
    await fits(page);
    await openGameOptions(page);
    await page.getByRole('combobox', { name: 'Sprache', exact: true }).selectOption('en');
    await closeGameOptions(page);
    await page.setViewportSize({ width: 844, height: 390 });
    await fits(page);
  }
  expect(errors).toEqual([]);
});
