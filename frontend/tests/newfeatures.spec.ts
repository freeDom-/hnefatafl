import { expect, test } from '@playwright/test';

async function finishVariant(
  request: import('@playwright/test').APIRequestContext,
  name: string,
  depth: number,
) {
  const created = await request.post('http://127.0.0.1:8001/api/games', {
    data: {
      ruleset: 'brandubh',
      mode: 'ai-ai',
      seed: 3,
      players: {
        attackers: { name, config: { algorithm: 'alpha_beta', depth, time_limit: 0.1, seed: 3 } },
        defenders: {
          name: 'Saga guardian',
          config: { algorithm: 'baseline', time_limit: 0.1, seed: 4 },
        },
      },
    },
  });
  expect(created.ok()).toBe(true);
  let game = await created.json();
  for (let move = 0; !game.winner && move < 100; move++) {
    const response = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/ai-move`, {
      data: { version: game.version },
    });
    expect(response.ok()).toBe(true);
    game = await response.json();
  }
  expect(game.winner).toBeTruthy();
}

test('player selection/deletion preserves records and AI variants expose their parameters', async ({
  page,
  request,
}, info) => {
  const name = `Freydis ${info.project.name}`;
  const aiName = `Raven Jarl ${info.project.name}`;
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  const fields = page.getByRole('combobox', { name: 'Battlefield', exact: true });
  await expect(fields.locator('option')).toHaveText([
    'All rulesets',
    'Brandubh',
    'Tablut (market rules)',
    'Copenhagen Hnefatafl',
  ]);
  await page.getByLabel('Display name').fill(name);
  await page.getByRole('button', { name: 'Create player' }).click();
  await expect(page.locator('.profile-list').getByText(name, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: /New game/ }).click();
  await expect(page.locator('.ruleset-options strong')).toHaveText([
    'Brandubh',
    'Tablut (market rules)',
    'Copenhagen Hnefatafl',
  ]);
  const selection = page.getByRole('combobox', { name: 'Attackers human player' });
  const humans = await (await request.get('http://127.0.0.1:8001/api/players')).json();
  await selection.selectOption(humans.find((player: { name: string }) => player.name === name).id);
  await expect(page.getByRole('textbox', { name: 'Attackers player name' })).toHaveCount(0);
  const createdGame = page.waitForResponse(
    (response) => response.url().endsWith('/api/games') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'To the table' }).click();
  const gameId = (await (await createdGame).json()).id;
  await expect(page.getByRole('grid')).toBeVisible();
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  await page.getByRole('button', { name: `Delete player ${name}` }).click();
  const confirm = page.getByRole('dialog', { name: `Delete player ${name}?` });
  await expect(confirm).toContainText('saved games, name and statistics remain');
  await confirm.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.locator('.profile-list').getByText(name, { exact: true })).toHaveCount(0);
  await expect(
    page.locator(`.record-entry[data-game-id="${gameId}"] .record`).filter({ hasText: name }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: /New game/ }).click();
  await expect(
    page
      .getByRole('combobox', { name: 'Attackers human player' })
      .locator('option')
      .filter({ hasText: name }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();

  await finishVariant(request, aiName, 1);
  await finishVariant(request, aiName, 2);
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  const rows = page.getByRole('row').filter({ hasText: aiName });
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: 'depth 1' })).toHaveCount(1);
  await expect(rows.filter({ hasText: 'depth 2' })).toHaveCount(1);
  await rows
    .filter({ hasText: 'depth 2' })
    .getByRole('button', { name: `Show AI parameters for ${aiName}`, exact: true })
    .click();
  const parameters = page.getByRole('dialog', { name: `${aiName} AI parameters`, exact: true });
  await expect(parameters.getByText('Search depth', { exact: true })).toBeVisible();
  await parameters.getByRole('button', { name: 'Close AI parameters', exact: true }).click();
  const records = page.locator('.record-entry').filter({ hasText: aiName });
  await expect(records).toHaveCount(2);
  await records.first().getByText('Attacker AI parameters', { exact: false }).click();
  await expect(
    records.first().locator('details[open]').getByText('Random seed', { exact: true }),
  ).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
});

test('workshop suggests Viking names and deletes a model only after confirmation', async ({
  page,
  request,
}, info) => {
  test.skip(
    info.project.name === 'mobile',
    'Real training is verified once on desktop; mobile records/profile controls are checked separately.',
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await expect(
    page.getByRole('combobox', { name: 'Battlefield', exact: true }).locator('option'),
  ).toHaveText(['Brandubh', 'Tablut (market rules)', 'Copenhagen Hnefatafl']);
  const input = page.getByRole('textbox', { name: 'Model name', exact: true });
  const original = await input.inputValue();
  await page.getByRole('button', { name: 'Suggest Viking name' }).click();
  await expect(input).not.toHaveValue(original);
  const name = `Runekeeper delete ${info.project.name}`;
  await input.fill(name);
  await page.getByRole('combobox', { name: 'Training preset', exact: true }).selectOption('smoke');
  await page.getByRole('button', { name: 'Start training', exact: false }).click();
  await expect(page.locator('.job-detail .job-status')).toHaveText('completed', { timeout: 60000 });
  await expect(page.locator('.model-card').getByRole('heading', { name })).toBeVisible();
  await page.getByRole('button', { name: `Delete model ${name}` }).click();
  const confirmation = page.getByRole('dialog', { name: `Delete model ${name}?` });
  await expect(confirmation).toContainText(
    'Completed game histories, statistics and training checkpoints are kept',
  );
  await confirmation.getByRole('button', { name: 'Keep it' }).click();
  await expect(page.locator('.model-card').getByRole('heading', { name })).toBeVisible();
  await page.getByRole('button', { name: `Delete model ${name}` }).click();
  await confirmation.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.locator('.model-card').getByRole('heading', { name })).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText(
    'Saved game history and training checkpoints have been kept',
  );
  const jobs = await (await request.get('http://127.0.0.1:8001/api/training/jobs')).json();
  expect(
    jobs.find((job: { config: { model_name: string } }) => job.config.model_name === name)
      .checkpoint,
  ).toBeTruthy();
});
