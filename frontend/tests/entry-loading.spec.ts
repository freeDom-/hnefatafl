import { expect, test } from './auth-fixture';

test('protected entry initializes while application timers are paused', async ({ page }) => {
  const now = new Date();
  await page.clock.install({ time: now });
  await page.clock.pauseAt(new Date(now.getTime() + 1000));
  await page.goto('/dashboard');
  // Do not advance the fake clock to make loading succeed: advisor regressions
  // need a frozen debounce clock before they mount the protected application.
  await expect(page.getByRole('heading', { name: 'The Great Hall', exact: true })).toBeVisible();
  await expect(page.locator('.personal-dashboard')).toBeVisible();
});
