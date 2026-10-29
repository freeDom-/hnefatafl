import { expect, test, type Page } from './auth-fixture';
import { historicalGame } from './historical-fixture';
import {
  gameTab,
  openGameOptions,
  closeGameOptions,
  openGameNavigation,
  returnToGame,
} from './play-helpers';

async function fits(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const board = document.querySelector('.board')!.getBoundingClientRect();
        const panel = document.querySelector('.mobile-inspector')!.getBoundingClientRect();
        return (
          document.documentElement.scrollHeight <= innerHeight &&
          document.documentElement.scrollWidth <= innerWidth &&
          board.top >= 0 &&
          (innerWidth > innerHeight ? board.right <= panel.left : board.bottom <= panel.top) &&
          panel.bottom <= innerHeight &&
          Math.abs(board.width - board.height) < 2
        );
      }),
    )
    .toBe(true);
}

test('mobile board, tabs, scrollable replay and advisor stay within the viewport', async ({
  page,
  request,
}, info) => {
  test.skip(
    info.project.name !== 'mobile',
    'Phone-specific screen layout; desktop gameplay has separate coverage.',
  );
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  let previous: { id: string; size: number } | undefined;
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
    await expect(page.locator('.board-toolbar')).toHaveCount(0);
    expect((await page.locator('.mobile-inspector').boundingBox())!.height).toBeGreaterThanOrEqual(
      268,
    );
    const runes = await page.locator('.army-symbol .identity-rune').evaluateAll((nodes) =>
      nodes.map((node) => {
        const rune = node.getBoundingClientRect(),
          parent = node.parentElement!.getBoundingClientRect();
        return (
          Math.abs((rune.left + rune.right) / 2 - (parent.left + parent.right) / 2) < 1 &&
          Math.abs((rune.top + rune.bottom) / 2 - (parent.top + parent.bottom) / 2) < 1 &&
          rune.width <= parent.width + 1 &&
          rune.height <= parent.height + 1
        );
      }),
    );
    expect(runes.length).toBeGreaterThan(0);
    expect(runes.every(Boolean)).toBe(true);
    const original = await page.locator('.board').boundingBox();
    await gameTab(page, 'games');
    await expect(
      page
        .getByRole('navigation', { name: 'My games', exact: true })
        .locator(`[data-game-id="${game.id}"]`),
    ).toBeVisible();
    await fits(page);
    expect(await page.locator('.board').boundingBox()).toEqual(original);
    if (previous) {
      await page
        .getByRole('navigation', { name: 'My games', exact: true })
        .locator(`[data-game-id="${previous.id}"]`)
        .click();
      await expect(page.getByRole('gridcell')).toHaveCount(previous.size * previous.size);
      await gameTab(page, 'games');
      await page
        .getByRole('navigation', { name: 'My games', exact: true })
        .locator(`[data-game-id="${game.id}"]`)
        .click();
      await expect(page.getByRole('gridcell')).toHaveCount(size * size);
      await fits(page);
    }
    await gameTab(page, 'analytics');
    await page.getByRole('button', { name: 'Move advisor', exact: true }).click();
    const menu = page.getByRole('dialog', { name: 'Game options', exact: true });
    await expect(menu.locator('.game-details, .active-game-switcher, .site-header')).toHaveCount(0);
    await expect(menu.getByRole('button', { name: 'Log out' })).toHaveCount(0);
    await expect(menu.getByRole('button', { name: 'New game', exact: true })).toHaveCount(0);
    await expect(menu.getByRole('button', { name: /Resume AI|Pause AI|Next move/ })).toHaveCount(0);
    await expect(menu.getByRole('combobox', { name: 'AI playback speed' })).toHaveCount(0);
    await page
      .getByRole('combobox', { name: 'Move advisor', exact: true })
      .selectOption('profile:baseline');
    await closeGameOptions(page);
    await expect(page.getByRole('meter')).toHaveCount(1);
    await expect(page.locator('.hint-overlay')).toBeVisible();
    await fits(page);
    await gameTab(page, 'moves');
    await expect(page.getByRole('button', { name: /Replay to move/ })).toHaveCount(
      game.moves.length,
    );
    await expect(page.getByRole('navigation', { name: 'Move history pages' })).toHaveCount(0);
    const list = page.locator('.compact-history .move-list');
    expect(await list.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);
    const seek = page.getByRole('button', { name: /Replay to move/ }).first();
    const beforeScroll = await page.locator('.board').boundingBox();
    await seek.scrollIntoViewIfNeeded();
    const browsed = await list.evaluate((node) => node.scrollTop);
    await gameTab(page, 'games');
    await gameTab(page, 'moves');
    expect(await list.evaluate((node) => node.scrollTop)).toBe(browsed);
    await page
      .getByRole('button', { name: /Replay to move/ })
      .last()
      .scrollIntoViewIfNeeded();
    expect(await list.evaluate((node) => node.scrollTop)).toBeGreaterThan(browsed);
    expect(await page.locator('.board').boundingBox()).toEqual(beforeScroll);
    await seek.scrollIntoViewIfNeeded();
    const ply = Number((await seek.getAttribute('aria-label'))!.match(/\d+/)![0]);
    await seek.click();
    await expect(page.locator('.mobile-play-subtitle')).toContainText('Move ' + ply);
    await fits(page);
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(page.getByRole('button', { name: /Replay to move/ })).toHaveCount(
      game.moves.length,
    );
    await fits(page);
    await openGameNavigation(page);
    await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
    await returnToGame(page, `/games/${game.id}`);
    await expect(page.getByRole('tab', { name: /^Züge/ })).toBeVisible();
    await fits(page);
    await openGameNavigation(page);
    await page.getByRole('combobox', { name: 'Sprache', exact: true }).selectOption('en');
    await returnToGame(page, `/games/${game.id}`);
    await page.setViewportSize({ width: 844, height: 390 });
    await fits(page);
    previous = { id: game.id, size };
  }
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'To the table', exact: true }).click();
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByRole('button', { name: /Resume AI|Pause AI/ })).toHaveCount(0);
  for (const size of [
    { width: 390, height: 780 },
    { width: 375, height: 667 },
    { width: 844, height: 390 },
  ]) {
    await page.setViewportSize(size);
    await fits(page);
  }
  await openGameOptions(page);
  const options = page.getByRole('dialog', { name: 'Game options', exact: true });
  await expect(options.getByRole('button', { name: /Resume AI|Pause AI|Next move/ })).toHaveCount(
    0,
  );
  await expect(options.getByRole('combobox', { name: 'AI playback speed' })).toHaveCount(0);
  await closeGameOptions(page);
  expect(errors).toEqual([]);
});
