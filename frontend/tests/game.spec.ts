import { expect, test } from '@playwright/test';

test('real gameplay, undo, redo, rules, records and mobile layout', async ({ page }, testInfo) => {
  const playerName = `Browser Astrid ${testInfo.project.name}`;
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('dialog', { name: 'Begin a new saga' })).toBeVisible();
  await page.getByRole('button', { name: 'To the table' }).click();
  const board = page.getByRole('grid', { name: 'tablut game board' });
  await expect(board.getByRole('gridcell')).toHaveCount(81);
  await board.getByRole('gridcell', { name: 'e7, defender', exact: true }).click();
  const destination = board.getByRole('gridcell', { name: /empty, legal move/ }).first();
  const name = await destination.getAttribute('aria-label');
  await destination.click();
  await expect(page.getByText('Attackers to move', { exact: true })).toBeVisible();
  await expect(
    board.getByRole('gridcell', {
      name: name!.replace('empty, legal move', 'defender'),
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Undo move' }).click();
  await expect(page.getByText('Defenders to move', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Redo move' }).click();
  await expect(page.getByText('Attackers to move', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Read the rules' }).click();
  await expect(page.getByRole('dialog', { name: 'Tablut (market rules) rules' })).toBeVisible();
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Recorded games' })).toBeVisible();
  await page.getByLabel('Display name').fill(playerName);
  await page.getByRole('button', { name: 'Create player' }).click();
  await expect(page.locator('.profile-list').getByText(playerName, { exact: true })).toBeVisible();
  await page
    .getByRole('button', { name: /Tablut.*Attacker vs Defender/ })
    .first()
    .click();
  await expect(page.getByRole('button', { name: 'Replay to move 1', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Starting position' }).click();
  await expect(page.getByText('Defenders to move', { exact: true })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
  expect(errors).toEqual([]);
});

test('all rulesets and computer playback use the actual API', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('radio', { name: /Brandubh/ }).check();
  await page.getByRole('button', { name: 'AI vs AI', exact: true }).click();
  await page.getByRole('combobox', { name: 'Attackers AI profile' }).selectOption('baseline');
  await page.getByRole('combobox', { name: 'Defenders AI profile' }).selectOption('baseline');
  await page.getByRole('checkbox', { name: 'Use a fixed seed', exact: true }).check();
  await page.getByLabel('Game seed', { exact: true }).fill('3');
  await page.getByRole('button', { name: 'To the table' }).click();
  await expect(
    page.getByRole('grid', { name: 'brandubh game board' }).getByRole('gridcell'),
  ).toHaveCount(49);
  await expect(page.getByRole('button', { name: 'Replay to move 1', exact: true })).toBeVisible({
    timeout: 20000,
  });
  await page.getByRole('button', { name: 'Pause AI', exact: false }).click();
  await expect(page.getByRole('button', { name: /Resume AI/ })).toBeVisible();
  await page.getByRole('combobox', { name: 'AI playback speed' }).selectOption({ label: 'Fast' });
  await page.getByRole('button', { name: /Resume AI/ }).click();
  const outcome = page.locator('.turn-banner.finished strong');
  await expect(outcome).toHaveText(/^(Attackers win|Defenders win|An evenly matched saga)$/, {
    timeout: 60000,
  });
  const outcomeText = await outcome.innerText();
  const archiveResult = outcomeText === 'An evenly matched saga' ? 'Draw' : outcomeText;
  const completedBoard = page.getByRole('grid', { name: 'brandubh game board' });
  await expect(completedBoard.locator('button:not(:disabled)')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Replay to move 2', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  const savedGame = page.locator('.records-list .record').first();
  await expect(savedGame).toContainText('Brandubh');
  await expect(savedGame).toContainText(archiveResult);
  await savedGame.click();
  await expect(page.locator('.turn-banner.finished strong')).toHaveText(outcomeText);
  await expect(
    page.getByRole('grid', { name: 'brandubh game board' }).locator('button:not(:disabled)'),
  ).toHaveCount(0);
  await page.getByRole('button', { name: /New game/ }).click();
  await page.getByRole('radio', { name: /Copenhagen/ }).check();
  await page.getByRole('button', { name: 'To the table' }).click();
  await expect(
    page.getByRole('grid', { name: 'copenhagen game board' }).getByRole('gridcell'),
  ).toHaveCount(121);
});

test('training dashboard starts, monitors and registers a real CPU smoke model', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.name === 'mobile',
    'CPU training is verified once; mobile gameplay is checked separately.',
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await page.getByLabel('Model name', { exact: true }).fill('Browser smoke strategist');
  await page.getByRole('combobox', { name: 'Training preset', exact: true }).selectOption('smoke');
  await page.getByRole('button', { name: 'Start training', exact: false }).click();
  await expect(page.getByRole('status')).toContainText('training job has started');
  await expect(page.locator('.job-detail .job-status')).toHaveText('completed', { timeout: 60000 });
  await expect(page.getByRole('img', { name: 'Loss by iteration', exact: true })).toBeVisible();
  await expect(
    page.getByRole('img', { name: 'Evaluation by iteration', exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('.model-card').getByRole('heading', { name: 'Browser smoke strategist' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resume from checkpoint' })).toBeEnabled();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: /New game/ }).click();
  await page.getByRole('button', { name: 'Human vs AI', exact: true }).click();
  await page.getByLabel('Your side').selectOption('attackers');
  await page.getByLabel('Defenders algorithm').selectOption('neural');
  const option = page
    .getByRole('combobox', { name: 'Defenders trained model', exact: true })
    .locator('option')
    .filter({ hasText: 'Browser smoke strategist' })
    .first();
  await page
    .getByRole('combobox', { name: 'Defenders trained model', exact: true })
    .selectOption((await option.getAttribute('value')) ?? '');
  await page.getByRole('button', { name: 'To the table' }).click();
  await expect(page.getByText('Attackers to move', { exact: true })).toBeVisible({
    timeout: 20000,
  });
});
