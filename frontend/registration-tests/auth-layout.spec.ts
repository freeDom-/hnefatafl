import { test, expect } from '@playwright/test';

test('auth headings remain accessible and focusable without reserving visual space', async ({
  page,
}) => {
  await page.goto('/login');
  const login = page.getByRole('button', { name: 'Log in', exact: true });
  await expect(login).toBeVisible();
  const spacing = await page
    .locator('.auth-card')
    .evaluate((card) => getComputedStyle(card).rowGap);
  for (const path of [
    '/register',
    '/forgot-password',
    '/verify-email',
    '/reset-password#token=layout-only-invalid-token',
  ]) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 2 })).toHaveCount(1);
    await expect(page.locator('.auth-card h2')).toBeFocused();
    expect(await page.locator('.auth-card').evaluate((card) => getComputedStyle(card).rowGap)).toBe(
      spacing,
    );
    const heading = await page.locator('.auth-card h2').boundingBox();
    expect(heading!.width).toBeLessThanOrEqual(1);
    expect(heading!.height).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
});
