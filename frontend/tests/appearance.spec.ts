import { expect, test } from '@playwright/test';

test('appearance changes real artwork and colors, persists, and preserves touch gameplay', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/');
  await page.getByRole('button', { name: 'To the table' }).click();
  const root = page.locator('html');
  const board = page.getByRole('grid', { name: 'tablut game board' });
  await expect(root).toHaveAttribute('data-color-mode', 'dark');
  const empty = board.getByRole('gridcell', { name: 'b9, empty', exact: true });
  const originalColor = await empty.evaluate((cell) => getComputedStyle(cell).backgroundColor);

  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Light', exact: true }).check();
  await page.getByRole('radio', { name: 'Viking figures' }).check();
  await page.getByRole('radio', { name: 'Old walnut' }).check();
  const previewKing = await page.locator('.token-preview .token-viking.king').boundingBox();
  expect(previewKing!.width).toBeLessThan(50);
  expect(previewKing!.height).toBeLessThan(55);
  await page.getByRole('button', { name: 'Back to the table' }).click();
  await expect(root).toHaveAttribute('data-color-mode', 'light');
  await expect(board.locator('svg[data-token-art="viking"]')).toHaveCount(25);
  const walnutColor = await empty.evaluate((cell) => getComputedStyle(cell).backgroundColor);
  expect(walnutColor).not.toBe(originalColor);
  await page.reload();
  await expect(root).toHaveAttribute('data-color-mode', 'light');
  await expect(board.locator('svg[data-token-art="viking"]')).toHaveCount(25);

  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Dark', exact: true }).check();
  await page.getByRole('radio', { name: 'Carved wood' }).check();
  await page.getByRole('radio', { name: 'Fjord slate' }).check();
  await page.getByRole('button', { name: 'Back to the table' }).click();
  await expect(board.locator('.token-wood')).toHaveCount(25);
  expect(await empty.evaluate((cell) => getComputedStyle(cell).backgroundColor)).not.toBe(
    walnutColor,
  );
  await board.getByRole('gridcell', { name: 'e7, defender', exact: true }).click();
  await board
    .getByRole('gridcell', { name: /empty, legal move/ })
    .first()
    .click();
  await expect(page.getByText('Attackers to move', { exact: true })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);

  await page.getByRole('button', { name: 'Appearance settings' }).click();
  await page.getByRole('radio', { name: 'Follow device' }).check();
  await page.getByRole('button', { name: 'Close appearance settings' }).click();
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(root).toHaveAttribute('data-color-mode', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(root).toHaveAttribute('data-color-mode', 'dark');
  expect(errors).toEqual([]);
});
