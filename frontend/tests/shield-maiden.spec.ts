import { expect, test } from '@playwright/test';

test('Shield maiden runs in real AI versus AI games', async ({ page }) => {
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Begin a new saga', exact: true });
  await setup.getByRole('radio', { name: /Brandubh/ }).check();
  await setup.getByRole('button', { name: 'AI vs AI', exact: true }).click();
  await setup.getByRole('combobox', { name: 'Attackers AI profile' }).selectOption('shield_maiden');
  await setup.getByRole('combobox', { name: 'Defenders AI profile' }).selectOption('baseline');
  await expect(setup.getByRole('combobox', { name: 'Attackers AI profile' })).toContainText(
    'Shield maiden · Intermediate',
  );
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/api/games') && r.request().method() === 'POST',
  );
  await setup.getByRole('button', { name: 'To the table' }).click();
  const response = await created;
  expect(response.ok()).toBeTruthy();
  const game = await response.json();
  expect(game.players.attackers.config).toMatchObject({
    algorithm: 'shield_maiden',
    heuristic_version: 1,
  });
  await page.getByRole('button', { name: /Pause AI/ }).click();
  await expect(page.getByRole('button', { name: /Resume AI/ })).toBeVisible();
});

test('Shield maiden is localized and selectable for training and advisors', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  const opponent = page.getByRole('combobox', { name: 'Evaluation opponent', exact: true });
  await opponent.selectOption('shield_maiden');
  await expect(
    page.getByText(
      'Shield maiden uses a stronger tactical heuristic, designed for low evaluation overhead.',
    ),
  ).toBeVisible();
  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
  await expect(page.getByRole('option', { name: 'Schildmaid', exact: true })).toHaveCount(1);
  await page.getByRole('combobox', { name: 'Sprache', exact: true }).selectOption('en');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: /New game/ }).click();
  await page.getByRole('radio', { name: /Brandubh/ }).check();
  await page.getByRole('button', { name: 'To the table' }).click();
  const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
  await analytics
    .getByRole('combobox', { name: 'Move advisor', exact: true })
    .selectOption('profile:shield_maiden');
  const panel = page.getByRole('region', { name: 'Agent evaluations' });
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  await expect(panel).toContainText('Shield maiden');
});
