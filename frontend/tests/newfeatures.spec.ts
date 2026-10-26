import { historicalGame } from './historical-fixture';
import { openCreation, openCollection } from './workshop-helpers';
import { expect, test } from './auth-fixture';

async function finishVariant(
  request: import('@playwright/test').APIRequestContext,
  name: string,
  depth: number,
) {
  const created = await historicalGame(request, {
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

test('account-bound setup and historical custom AI variants preserve records', async ({
  page,
  request,
}, info) => {
  const aiName = `Raven Jarl ${info.project.name}`;
  await page.goto('/dashboard?setup=single-player');
  await expect(page.locator('.ruleset-options strong')).toHaveText([
    'Brandubh',
    'Tablut (market rules)',
    'Copenhagen Hnefatafl',
  ]);
  await expect(page.getByText('Playing as your account')).toBeVisible();
  await expect(page.getByRole('textbox', { name: /player name/ })).toHaveCount(0);
  const createdGame = page.waitForResponse(
    (r) => r.url().endsWith('/api/games') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'To the table' }).click();
  const game = await (await createdGame).json();
  expect(game.players[game.human_side].name).toBe('BrowserAdmin');
  await page.goto('/archive');
  await expect(page.getByText('Local players', { exact: true })).toHaveCount(0);
  await expect(page.locator(`.recorded-games a[href*="selected=${game.id}"]`)).toBeVisible();
  await finishVariant(request, aiName, 1);
  await finishVariant(request, aiName, 2);
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  await page.getByLabel('Player name').fill(aiName);
  await expect(page.locator('.recorded-games li')).toHaveCount(2);
  const stats = await (await request.get('http://127.0.0.1:8001/api/stats')).json();
  const variants = stats.by_player.filter((row: { name: string }) => row.name === aiName);
  expect(variants).toHaveLength(2);
  expect(variants.map((row: { config: { depth: number } }) => row.config.depth).sort()).toEqual([
    1, 2,
  ]);
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
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await openCreation(page);
  await openCollection(page);
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
  await expect(page.locator('.notice[role=status]')).toContainText(
    'Saved game history and training checkpoints have been kept',
  );
  const jobs = await (await request.get('http://127.0.0.1:8001/api/training/jobs')).json();
  expect(
    jobs.find((job: { config: { model_name: string } }) => job.config.model_name === name)
      .checkpoint,
  ).toBeTruthy();
});
