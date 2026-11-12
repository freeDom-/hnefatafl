import { mkdirSync } from 'node:fs';
import { expect, test } from './auth-fixture';

// Chromium is the only configured browser, so these screenshots run wherever
// `npm --prefix frontend run test:e2e` is available with an installed Chromium.
const themes = ['kings-table', 'iron-ash', 'saga-hall'] as const;
const output = 'test-results/arena-themes';

test.beforeAll(() => {
  mkdirSync(output, { recursive: true });
});

for (const theme of themes) {
  test(`captures the ${theme} UI theme on desktop and mobile`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: value }));
    }, theme);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
    await page.screenshot({ path: `${output}/${theme}-desktop.png`, fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
    await page.screenshot({ path: `${output}/${theme}-mobile.png`, fullPage: true });
  });
}

test('keeps the legacy appearance when no UI theme is stored', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await expect(page.locator('html')).not.toHaveAttribute('data-ui-theme');
  await page.screenshot({ path: `${output}/legacy-default-desktop.png`, fullPage: true });
});
