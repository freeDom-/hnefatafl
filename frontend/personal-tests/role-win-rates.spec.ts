import { expect, test, type Page } from '@playwright/test';
import { singleStatistics } from './statistics-fixture';

const password = 'personal-browser-fixture-password';
const themes = ['classic-table', 'kings-table', 'iron-ash', 'saga-hall'];

async function login(page: Page) {
  await page.goto('/dashboard');
  await page.getByLabel('Username or email').fill('PersonalArcher');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'The Great Hall', exact: true })).toBeVisible();
}

test('role win rates stay distinct and balanced across themes and UI scales', async ({
  page,
  request,
}, info) => {
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
  await page.route(/\/api\/personal\/single-player\?/, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ response, json: { ...data, statistics: singleStatistics } });
  });
  await login(page);
  const sizes =
    info.project.name === 'mobile'
      ? [
          { width: 320, height: 640 },
          { width: 390, height: 844 },
          { width: 768, height: 1024 },
        ]
      : [
          { width: 1280, height: 900 },
          { width: 1920, height: 1080 },
          { width: 2560, height: 1440 },
        ];
  for (const theme of themes) {
    for (const size of sizes) {
      await page.setViewportSize(size);
      await page.evaluate(
        (theme) =>
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ version: 2, uiTheme: theme }),
          ),
        theme,
      );
      await page.goto('/statistics?mode=single-player&ruleset=brandubh');
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
      await expect(page.getByRole('heading', { name: 'Your statistics' })).toBeVisible();
      const card = page.locator('.statistics-ruleset-card');
      const metric = (index: number) => card.locator('.statistics-headline-metric').nth(index);
      await expect(metric(0).locator('span')).toHaveText('Win rate');
      await expect(metric(1).locator('span')).toHaveText('Win rate as attacker');
      await expect(metric(2).locator('span')).toHaveText('Win rate as defender');
      await expect(metric(0)).toContainText('20%');
      await expect(metric(1)).toContainText('100%');
      await expect(metric(2)).toContainText('0%');
      await expect(page.getByRole('table', { name: 'Brandubh results by side' })).toBeVisible();
      const geometry = await card.evaluate((node) => {
        const bounds = node.getBoundingClientRect();
        const headline = node.querySelector('.statistics-headline')!.getBoundingClientRect();
        return {
          left: headline.left >= bounds.left - 1,
          right: headline.right <= bounds.right + 1,
          width: headline.width > 0,
        };
      });
      expect(geometry).toEqual({ left: true, right: true, width: true });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      if (size.width === (info.project.name === 'mobile' ? 390 : 1920)) {
        await card.screenshot({
          path: info.outputPath(`role-win-rates-${theme}-${size.width}.png`),
        });
      }
    }
  }
});
