import { expect, test } from '@playwright/test';

test('advisor highlights a legal move and original estimates survive replay, reload and localization', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('radio', { name: /Brandubh/ }).check();
  const advisor = page.getByRole('combobox', { name: 'Agent for move suggestions' });
  await expect(advisor).toHaveValue('');
  await advisor.selectOption('profile:baseline');
  await page.getByRole('button', { name: 'To the table' }).click();
  const panel = page.getByRole('region', { name: 'Agent evaluations' });
  await expect(panel.getByRole('meter')).toHaveCount(1);
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  const initialValue = await panel.getByRole('meter').getAttribute('aria-valuenow');
  const board = page.getByRole('grid', { name: 'brandubh game board' });
  await expect(board.locator('.hint-overlay')).toBeVisible();
  await expect(board.locator('.hint-source')).toHaveCount(1);
  await expect(board.locator('.hint-target')).toHaveCount(1);
  await board.locator('.hint-source').click();
  await board.locator('.hint-target').click();
  await expect(page.getByRole('button', { name: 'Replay to move 1', exact: true })).toBeVisible();
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  await page.getByRole('button', { name: 'Undo move', exact: true }).click();
  await expect(panel.getByRole('meter')).toHaveAttribute('aria-valuenow', initialValue!);
  await page.reload();
  await expect(panel.getByRole('meter')).toHaveAttribute('aria-valuenow', initialValue!);

  await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.getByRole('heading', { name: 'Gewinnchancen', exact: true })).toBeVisible();
  await expect(page.getByRole('gridcell', { name: /König/ })).toHaveCount(1);
  await page.getByRole('button', { name: 'Darstellungseinstellungen', exact: true }).click();
  await page.getByRole('radio', { name: 'Holz', exact: true }).check();
  await page
    .getByRole('button', { name: 'Darstellungseinstellungen schließen', exact: true })
    .click();
  await expect(page.locator('html')).toHaveAttribute('data-color-mode', 'wood');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.locator('html')).toHaveAttribute('data-color-mode', 'wood');
  await expect(page.getByRole('heading', { name: 'Gewinnchancen', exact: true })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  expect(errors).toEqual([]);
});

test('two opponents retain independent estimates for the same side to move', async ({
  page,
  request,
}) => {
  const response = await request.post('http://127.0.0.1:8001/api/games', {
    data: {
      ruleset: 'brandubh',
      mode: 'ai-ai',
      seed: 3,
      players: {
        attackers: {
          name: 'Evaluation Hugin',
          config: { algorithm: 'baseline', time_limit: 0.05 },
        },
        defenders: {
          name: 'Evaluation Munin',
          config: { algorithm: 'alpha_beta', depth: 1, time_limit: 0.05 },
        },
      },
    },
  });
  expect(response.ok()).toBe(true);
  const game = await response.json();
  const evaluated = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/analysis`, {
    data: { version: game.version, cursor: game.cursor },
  });
  expect(evaluated.ok()).toBe(true);
  const snapshot = await evaluated.json();
  expect(snapshot.evaluations).toHaveLength(2);
  await page.addInitScript((id) => localStorage.setItem('hnefatafl-game', id), game.id);
  await page.goto('/');
  await page.getByRole('button', { name: /Pause AI/ }).click();
  const meters = page.getByRole('region', { name: 'Agent evaluations' }).getByRole('meter');
  await expect(meters).toHaveCount(2);
  for (const agent of snapshot.evaluations) {
    await expect(
      page.getByRole('meter', { name: `${agent.name} estimate for Attackers` }),
    ).toHaveAttribute('aria-valuenow', String(Math.round(agent.win_probability * 1000) / 10));
  }
  await expect(page.getByText('For the current player: Attackers', { exact: true })).toBeVisible();
});
