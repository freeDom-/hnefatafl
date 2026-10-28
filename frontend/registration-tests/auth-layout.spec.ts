import { test, expect } from '@playwright/test';

test('auth screens share heading spacing, focus and layout across views', async ({ page }) => {
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
    await expect(page.locator('.auth-card h2')).toBeVisible();
    await expect(page.locator('.auth-card h2')).toBeFocused();
    expect(await page.locator('.auth-card').evaluate((card) => getComputedStyle(card).rowGap)).toBe(
      spacing,
    );
    const gap = await page.locator('.auth-card h2').evaluate((h) => {
      const after = h.nextElementSibling!;
      return after.getBoundingClientRect().top - h.getBoundingClientRect().bottom;
    });
    expect(gap).toBeGreaterThanOrEqual(12);
    expect(gap).toBeLessThanOrEqual(24);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
});
