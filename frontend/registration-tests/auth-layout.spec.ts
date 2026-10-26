import { test, expect } from '@playwright/test';

test('auth screens share heading spacing and place the keyboard-accessible guest anchor below login', async ({
  page,
}) => {
  await page.goto('/login');
  const login = page.getByRole('button', { name: 'Log in', exact: true });
  const guest = page.getByRole('link', { name: 'Play as guest on this device', exact: true });
  await expect(login).toBeVisible();
  await expect(guest).toHaveAttribute('href', '/guest');
  const primary = await login.boundingBox(),
    secondary = await guest.boundingBox();
  expect(secondary!.y).toBeGreaterThanOrEqual(primary!.y + primary!.height + 12);
  expect(secondary!.height).toBeGreaterThanOrEqual(44);
  await login.focus();
  await page.keyboard.press('Tab');
  await expect(guest).toBeFocused();
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
    await expect(
      page.getByRole('link', { name: 'Play as guest on this device', exact: true }),
    ).toHaveAttribute('href', '/guest');
  }
});
