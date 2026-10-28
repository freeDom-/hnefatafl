import { test, expect } from '@playwright/test';

test('login, registration placeholder, regular-user navigation, reload and logout', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'HNEFATAFL' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign up / Register' }).click();
  await expect(page.getByRole('status')).toHaveText('Registration is not available yet.');
  await page.getByLabel('Username or email').fill('BrowserUser');
  await page.getByLabel('Password', { exact: true }).fill('browser-fixture-only-password');
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'AI workshop', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tournaments', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'New single-player game', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Begin a new saga' });
  await expect(setup.getByText('BrowserUser', { exact: true })).toHaveCount(0);
  await expect(setup.getByLabel('Game mode')).toHaveCount(0);
  await expect(setup.getByRole('button', { name: 'AI vs AI', exact: true })).toHaveCount(0);
  await expect(setup.getByRole('button', { name: 'Pass & play', exact: true })).toHaveCount(0);
  await expect(setup.getByRole('textbox')).toHaveCount(0);
  await expect(setup.getByRole('spinbutton')).toHaveCount(0);
  await expect(setup.getByLabel('Attackers AI profile').getByRole('option')).toHaveText([
    'Shield bearer',
    'Shield maiden',
    'Jarl',
    'Seer',
  ]);
  await setup.getByRole('radio', { name: /Brandubh/ }).check();
  await setup.getByLabel('Your side').selectOption('attackers');
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/api/games') && r.request().method() === 'POST',
  );
  await setup.getByRole('button', { name: 'To the table' }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const game = await response.json();
  expect(game.mode).toBe('human-ai');
  expect(game.players.attackers.name).toBe('BrowserUser');
  const session = await (await page.request.get('/api/auth/me')).json();
  expect(game.players.attackers.account_id).toBe(session.account.id);
  await page.getByRole('button', { name: 'Dashboard', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  const status = await page.evaluate(async () => (await fetch('/api/training/jobs')).status);
  expect(status).toBe(403);
  await page.goto('/training');
  await expect(page.getByRole('heading', { name: 'Access denied' })).toBeVisible();
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'HNEFATAFL' })).toBeVisible();
  expect(await page.evaluate(async () => (await fetch('/api/games')).status)).toBe(401);
});
