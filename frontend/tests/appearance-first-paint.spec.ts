import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
const namedThemes = Object.keys(
  JSON.parse(readFileSync('src/appearanceCatalog.json', 'utf8')).themes,
);
for (const theme of [...namedThemes, 'legacy', 'unknown', 'invalid-json']) {
  test(`first paint preserves ${theme} before React and on reload`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.addInitScript((value) => {
      localStorage.setItem(
        'hnefatafl-appearance',
        value === 'invalid-json'
          ? '{'
          : JSON.stringify({
              uiTheme: value,
              mode: 'system',
              tokens: 'classic',
              board: 'slate',
              density: 'large',
            }),
      );
    }, theme);
    await page.route('**/src/main.tsx*', (route) => route.abort());
    await page.goto('/');
    const expected = namedThemes.includes(theme) ? theme : 'kings-table';
    for (let reload = 0; reload < 2; reload++) {
      if (reload) await page.reload();
      await expect(page.locator('#root')).toBeEmpty();
      if (expected) await expect(page.locator('html')).toHaveAttribute('data-ui-theme', expected);
      else await expect(page.locator('html')).not.toHaveAttribute('data-ui-theme');
      await expect(page.locator('html')).not.toHaveAttribute('data-color-mode');
      if (theme !== 'invalid-json') {
        await expect(page.locator('html')).toHaveAttribute('data-density', 'large');
        const saved = await page.evaluate(() =>
          JSON.parse(localStorage.getItem('hnefatafl-appearance')!),
        );
        expect(saved).toMatchObject({
          uiTheme: theme,
          tokens: 'classic',
          board: 'slate',
          mode: 'system',
          density: 'large',
        });
      }
    }
  });
}

for (const [mode, theme] of [
  ['light', 'classic-table'],
  ['dark', 'iron-ash'],
  ['wood', 'saga-hall'],
  ['system', 'kings-table'],
]) {
  test(`migrates old ${mode} before React, preserves preferences and stays stable after normalization`, async ({
    page,
  }) => {
    await page.addInitScript((mode) => {
      if (!localStorage.getItem('hnefatafl-appearance'))
        localStorage.setItem(
          'hnefatafl-appearance',
          JSON.stringify({
            uiTheme: 'legacy',
            mode,
            tokens: 'viking',
            board: 'slate',
            density: 'compact',
          }),
        );
    }, mode);
    await page.route('**/src/main.tsx*', (route) => route.abort());
    await page.goto('/');
    await expect(page.locator('#root')).toBeEmpty();
    await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
    await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    await page.unroute('**/src/main.tsx*');
    await page.reload();
    const expected = {
      version: 2,
      uiTheme: theme,
      tokens: 'viking',
      board: 'slate',
      density: 'compact',
    };
    await expect
      .poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('hnefatafl-appearance')!)))
      .toEqual(expected);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
    expect(
      await page.evaluate(() => JSON.parse(localStorage.getItem('hnefatafl-appearance')!)),
    ).toEqual(expected);
  });
}
