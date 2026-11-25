import { expect, test, type Page } from '@playwright/test';

const password = 'personal-browser-fixture-password';
const themes = ['classic-table', 'kings-table', 'iron-ash', 'saga-hall'];

async function login(page: Page) {
  await page.goto('/dashboard');
  await page.getByLabel('Username or email').fill('PersonalArcher');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'The Great Hall', exact: true })).toBeVisible();
}

test('achievement cards omit thresholds but keep progress, rewards and scores', async ({
  page,
  request,
}, info) => {
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
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
      await page.goto('/portraits');
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
      await expect(page.getByRole('heading', { name: 'Hall of Renown' })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Deeds of Valor' })).toBeVisible();
      const cards = page.locator('.portrait-achievement-list > li');
      await expect(cards.first()).toBeVisible();
      const card = page.locator('[data-family="rated-wins"]');
      await expect(card).toBeVisible();
      await expect(card.locator('p').first()).toHaveText(/^Earned tier: \d+ \/ \d+$/);
      await expect(card.locator('p').nth(1)).toHaveText(/^Recorded progress: \d+$/);
      await expect(card).not.toContainText(/threshold/i);
      await expect(card).not.toContainText(/highest achieved/i);
      await expect(card.getByRole('heading', { name: 'Rated online victories' })).toBeVisible();
      await expect(card.locator('.portrait-state')).toBeVisible();
      await expect(card.getByText('Milestone rewards')).toBeVisible();
      await expect(page.getByRole('progressbar', { name: /Achievement Score/ })).toBeVisible();
      await expect(page.getByRole('button', { name: /claim|collect/i })).toHaveCount(0);
      await expect(page.locator('.portrait-gallery > li').first()).toBeVisible();
      for (const achievement of await cards.all()) {
        await expect(achievement).not.toContainText(/threshold/i);
        await expect(achievement).not.toContainText(/highest achieved/i);
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      if (size.width === (info.project.name === 'mobile' ? 390 : 1920)) {
        await page.locator('.portrait-achievements').screenshot({
          path: info.outputPath(`achievement-cards-${theme}-${size.width}.png`),
        });
      }
    }
  }
});
