import { expect, test, type APIRequestContext } from '@playwright/test';

const api = 'http://127.0.0.1:8001/api';
async function trainedModel(request: APIRequestContext, name: string, seed: number) {
  const started = await request.post(`${api}/training/jobs`, {
    data: {
      kind: 'train',
      preset: 'smoke',
      ruleset: 'brandubh',
      model_name: name,
      seed,
      mcts_time_limit: 0.05,
      max_game_plies: 4,
      evaluation_max_plies: 4,
      device: 'cpu',
    },
  });
  expect(started.ok()).toBe(true);
  const job = await started.json();
  await expect
    .poll(
      async () => {
        const response = await request.get(`${api}/training/jobs/${job.id}`);
        return (await response.json()).status;
      },
      { timeout: 60000 },
    )
    .toBe('completed');
  const response = await request.get(`${api}/models`);
  const model = (await response.json()).find(
    (candidate: { name: string }) => candidate.name === name,
  );
  expect(model).toBeTruthy();
  return model as { id: string; name: string; rune: string };
}

test('distinct trained opponents keep their IDs and displayed names, and every game option survives reload', async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const ulf = await trainedModel(request, `UI Ulf ${info.project.name}`, 40171);
  const freydis = await trainedModel(request, `UI Freydis ${info.project.name}`, 40172);
  expect(ulf.id).not.toBe(freydis.id);
  expect(ulf.rune).toBeTruthy();
  expect(freydis.rune).toBeTruthy();
  expect(ulf.rune).not.toBe(freydis.rune);
  await page.goto('/');
  await page.getByRole('radio', { name: /Brandubh/ }).check();
  await page.getByRole('button', { name: 'AI vs AI', exact: true }).click();
  await page
    .getByRole('combobox', { name: 'Attackers trained model', exact: true })
    .selectOption(ulf.id);
  await page
    .getByRole('combobox', { name: 'Defenders trained model', exact: true })
    .selectOption(freydis.id);
  for (const fields of await page.locator('.player-config').all()) {
    await fields.getByRole('spinbutton', { name: 'Search iterations', exact: true }).fill('2');
    await fields
      .getByRole('spinbutton', { name: 'Time limit (seconds)', exact: true })
      .fill('0.05');
  }
  await page
    .getByRole('combobox', { name: 'Agent for move suggestions', exact: true })
    .selectOption('profile:baseline');
  await page.getByRole('checkbox', { name: 'Use a fixed seed', exact: true }).check();
  await page.getByRole('spinbutton', { name: 'Game seed', exact: true }).fill('711');
  const created = page.waitForResponse(
    (response) => response.url().endsWith('/api/games') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'To the table', exact: false }).click();
  const response = await created;
  expect(response.ok()).toBe(true);
  const game = await response.json();
  expect(game.players.attackers).toMatchObject({
    name: ulf.name,
    model_name: ulf.name,
    config: { model_id: ulf.id },
  });
  expect(game.players.defenders).toMatchObject({
    name: freydis.name,
    model_name: freydis.name,
    config: { model_id: freydis.id },
  });
  await expect(page.locator('.army h3').nth(0)).toContainText(ulf.name);
  await expect(page.locator('.army h3').nth(1)).toContainText(freydis.name);
  await expect(page.locator('.army-symbol [data-rune]').nth(0)).toHaveAttribute(
    'data-rune',
    ulf.rune,
  );
  await expect(page.locator('.army-symbol [data-rune]').nth(1)).toHaveAttribute(
    'data-rune',
    freydis.rune,
  );
  await page.reload();
  await page.getByRole('button', { name: 'New game', exact: false }).click();
  const setup = page.getByRole('dialog', { name: 'Begin a new saga', exact: true });
  await expect(setup.getByRole('radio', { name: /Brandubh/ })).toBeChecked();
  await expect(setup.getByRole('button', { name: 'AI vs AI', exact: true })).toHaveClass('active');
  await expect(
    setup.getByRole('combobox', { name: 'Attackers trained model', exact: true }),
  ).toHaveValue(ulf.id);
  await expect(
    setup.getByRole('combobox', { name: 'Defenders trained model', exact: true }),
  ).toHaveValue(freydis.id);
  for (const fields of await setup.locator('.player-config').all()) {
    await expect(
      fields.getByRole('spinbutton', { name: 'Search iterations', exact: true }),
    ).toHaveValue('2');
    await expect(
      fields.getByRole('spinbutton', { name: 'Time limit (seconds)', exact: true }),
    ).toHaveValue('0.05');
  }
  await expect(
    setup.getByRole('combobox', { name: 'Agent for move suggestions', exact: true }),
  ).toHaveValue('profile:baseline');
  await expect(
    setup.getByRole('checkbox', { name: 'Use a fixed seed', exact: true }),
  ).toBeChecked();
  await expect(setup.getByRole('spinbutton', { name: 'Game seed', exact: true })).toHaveValue(
    '711',
  );
  await expect(
    setup
      .getByRole('combobox', { name: 'Attackers trained model', exact: true })
      .locator(`option[value="${ulf.id}"]`),
  ).toContainText(ulf.rune);
  await setup.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  const ulfCard = page
    .getByRole('article')
    .filter({ has: page.getByRole('heading', { name: ulf.name, exact: true }) });
  await expect(ulfCard.locator('[data-rune]')).toHaveAttribute('data-rune', ulf.rune);
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  await expect(
    page.locator(`.record-entry[data-game-id="${game.id}"] [data-rune]`).nth(0),
  ).toHaveAttribute('data-rune', ulf.rune);
  await expect(
    page.locator(`.record-entry[data-game-id="${game.id}"] [data-rune]`).nth(1),
  ).toHaveAttribute('data-rune', freydis.rune);
  await expect(page.locator('.header-note')).toHaveCount(0);
  await expect(page.locator('p.eyebrow')).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  expect(errors).toEqual([]);
});
