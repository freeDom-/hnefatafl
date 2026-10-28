import { expect, test } from './auth-fixture';
import { historicalGame } from './historical-fixture';

test('interface scale changes spacing and text, persists and composes with responsive spacing', async ({
  page,
}) => {
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'To the table' }).click();
  const root = page.locator('html');
  await expect(root).toHaveAttribute('data-density', 'default');
  const measure = () =>
    page.locator('.game-details').evaluate((node) => ({
      padding: parseFloat(getComputedStyle(node).paddingLeft),
      font: parseFloat(getComputedStyle(node.querySelector('h2')!).fontSize),
    }));
  const initial = await measure();
  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Compact', exact: true }).check();
  await expect(root).toHaveAttribute('data-density', 'compact');
  const compact = await measure();
  expect(compact.padding).toBeLessThan(initial.padding);
  expect(compact.font).toBeLessThan(initial.font);
  await page.getByRole('button', { name: 'Back to the table' }).click();
  await page.reload();
  await expect(root).toHaveAttribute('data-density', 'compact');
  expect(await measure()).toEqual(compact);
  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await expect(page.getByRole('radio', { name: 'Compact', exact: true })).toBeChecked();
  await page.getByRole('radio', { name: 'Large', exact: true }).check();
  await expect(root).toHaveAttribute('data-density', 'large');
  const large = await measure();
  expect(large.padding).toBeGreaterThan(initial.padding);
  expect(large.font).toBeGreaterThan(initial.font);
  await page.getByRole('button', { name: 'Back to the table' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('appearance changes real artwork and colors, persists, and preserves touch gameplay', async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ colorScheme: 'dark' });
  // Keep the artwork count and saved turn stable while exercising real moves.
  const response = await historicalGame(request, {
    data: { ruleset: 'tablut', mode: 'human-human' },
  });
  expect(response.ok()).toBe(true);
  const game = await response.json();
  await page.goto(`/games/${game.id}`);
  const root = page.locator('html');
  const board = page.getByRole('grid', { name: 'tablut game board' });
  await expect(root).toHaveAttribute('data-color-mode', 'dark');
  await expect(root).toHaveAttribute('data-token-theme', 'wood');
  await expect(root).toHaveAttribute('data-board-theme', 'walnut');
  await expect(board.locator('.token-wood')).toHaveCount(25);
  await expect(board.locator('.king [data-king-design="fjord-diadem"]')).toBeVisible();
  const empty = board.getByRole('gridcell', { name: 'b9, empty', exact: true });
  const originalColor = await empty.evaluate((cell) => getComputedStyle(cell).backgroundColor);

  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Light', exact: true }).check();
  await page.getByRole('radio', { name: 'Viking figures' }).check();
  await page.getByRole('radio', { name: 'Golden oak' }).check();
  const previewKing = await page.locator('.token-preview .token-viking.king').boundingBox();
  expect(previewKing!.width).toBeLessThan(50);
  expect(previewKing!.height).toBeLessThan(55);
  await page.getByRole('button', { name: 'Back to the table' }).click();
  await expect(root).toHaveAttribute('data-color-mode', 'light');
  await expect(board.locator('svg[data-token-art="viking"]')).toHaveCount(25);
  const oakColor = await empty.evaluate((cell) => getComputedStyle(cell).backgroundColor);
  expect(oakColor).not.toBe(originalColor);
  await page.reload();
  await expect(root).toHaveAttribute('data-color-mode', 'light');
  await expect(board.locator('svg[data-token-art="viking"]')).toHaveCount(25);

  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Dark', exact: true }).check();
  await page.getByRole('radio', { name: 'Carved wood' }).check();
  await page.getByRole('radio', { name: 'Fjord slate' }).check();
  await page.getByRole('button', { name: 'Back to the table' }).click();
  await expect(board.locator('.token-wood')).toHaveCount(25);
  expect(await empty.evaluate((cell) => getComputedStyle(cell).backgroundColor)).not.toBe(oakColor);
  await board.getByRole('gridcell', { name: 'e7, defender', exact: true }).click();
  await board
    .getByRole('gridcell', { name: /empty, legal move/ })
    .first()
    .click();
  await expect(page.getByLabel('Attackers to move')).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);

  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Wood', exact: true }).check();
  await page.getByRole('button', { name: 'Back to the table' }).click();
  await expect(root).toHaveAttribute('data-color-mode', 'wood');
  await expect(root).toHaveCSS('color-scheme', 'dark');
  await expect(root).toHaveCSS('background-color', 'rgb(33, 24, 18)');
  await expect(page.locator('[data-wood-ornaments]')).toBeVisible();
  await expect(page.locator('[data-wood-motif]')).toHaveCount(4);
  await expect(board.locator('.token-wood')).toHaveCount(25);
  await page.reload();
  await expect(root).toHaveAttribute('data-color-mode', 'wood');
  await expect(root).toHaveAttribute('data-board-theme', 'slate');
  await expect(page.getByLabel('Attackers to move')).toBeVisible();

  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Follow device' }).check();
  await page.getByRole('button', { name: 'Close appearance settings' }).click();
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(root).toHaveAttribute('data-color-mode', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(root).toHaveAttribute('data-color-mode', 'dark');
  await expect(page.locator('[data-wood-ornaments]')).toHaveCount(0);
  expect(errors).toEqual([]);
});
