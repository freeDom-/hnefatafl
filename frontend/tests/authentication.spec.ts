import { test, expect } from '@playwright/test';

test('login, registration placeholder, regular-user navigation, reload and logout', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign up / Register' }).click();
  await expect(page.getByRole('status')).toHaveText('Registration is not available yet.');
  await page.getByLabel('Username or email').fill('BrowserUser');
  await page.getByLabel('Password', { exact: true }).fill('browser-fixture-only-password');
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'AI workshop', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tournaments', exact: true })).toHaveCount(0);
  await page.reload();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  const status = await page.evaluate(async () => (await fetch('/api/training/jobs')).status);
  expect(status).toBe(403);
  await page.goto('/training');
  await expect(page.getByRole('heading', { name: 'Access denied' })).toBeVisible();
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible();
  expect(await page.evaluate(async () => (await fetch('/api/games')).status)).toBe(401);
});
