import { historicalGame } from './historical-fixture';
import { openCollection } from './workshop-helpers';
import { expect, test, type APIRequestContext } from './auth-fixture';

const api = 'http://127.0.0.1:8001/api';

test('only named opponents with fixed presets are offered and the dialog closes on outside press', async ({
  page,
}) => {
  await page.goto('/dashboard?setup=single-player');
  const setup = page.getByRole('dialog', { name: 'Begin a new saga', exact: true });
  await setup.getByRole('radio', { name: /Brandubh/ }).check();
  await setup.getByRole('button', { name: 'AI vs AI', exact: true }).click();
  const attackers = setup.locator('.player-config').nth(0);
  const defenders = setup.locator('.player-config').nth(1);
  await attackers.getByRole('combobox', { name: 'Attackers AI profile' }).selectOption('baseline');
  await expect(attackers.getByRole('spinbutton')).toHaveCount(0);
  await expect(attackers.getByRole('combobox', { name: /algorithm/ })).toHaveCount(0);
  await expect(attackers).toContainText('Time limit (seconds): 1');
  await attackers
    .getByRole('combobox', { name: 'Attackers AI profile' })
    .selectOption('alpha_beta');
  await defenders.getByRole('combobox', { name: 'Defenders AI profile' }).selectOption('mcts');
  for (const fields of [attackers, defenders]) {
    await expect(fields.getByRole('spinbutton')).toHaveCount(0);
    await expect(fields.getByRole('combobox', { name: /algorithm/ })).toHaveCount(0);
  }
  await expect(setup.getByRole('combobox', { name: /move advisor|move suggestions/i })).toHaveCount(
    0,
  );
  const created = page.waitForResponse(
    (response) => response.url().endsWith('/api/games') && response.request().method() === 'POST',
  );
  await setup.getByRole('button', { name: 'To the table' }).click();
  const response = await created;
  expect(response.ok()).toBe(true);
  const game = await response.json();
  expect(game.players.attackers.config).toMatchObject({
    algorithm: 'alpha_beta',
    depth: 5,
    time_limit: 2,
  });
  expect(game.players.defenders.config).toMatchObject({
    algorithm: 'mcts',
    iterations: 5000,
    temperature: 0,
    exploration: 1.4,
    time_limit: 2,
  });
  await page.getByRole('button', { name: /Pause AI/ }).click();
  await page.getByRole('button', { name: /New (?:single-player )?game/ }).click();
  await expect(setup.getByRole('option', { name: 'Trained model', exact: true })).toHaveCount(0);
  await expect(setup.getByRole('spinbutton')).toHaveCount(0);
  // A drag that begins inside the dialog must not close it when released on the backdrop.
  const title = setup.getByRole('heading', { name: 'Begin a new saga' });
  await title.scrollIntoViewIfNeeded();
  const heading = await title.boundingBox();
  expect(heading).not.toBeNull();
  await page.mouse.move(heading!.x + heading!.width / 2, heading!.y + heading!.height / 2);
  await page.mouse.down();
  await page.mouse.move(2, 2);
  await page.mouse.up();
  await expect(setup).toBeVisible();
  // An outside press closes immediately, before releasing the mouse button.
  await page.mouse.down();
  await expect(setup).toHaveCount(0);
  await page.mouse.up();
});

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

test('historical trained opponents retain names, runes and legal play while new setup stays preset-only', async ({
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
  const response = await historicalGame(request, {
    data: {
      ruleset: 'brandubh',
      mode: 'ai-ai',
      seed: 711,
      players: {
        attackers: {
          name: ulf.name,
          profile_id: 'neural',
          config: { model_id: ulf.id, iterations: 2, time_limit: 0.05 },
        },
        defenders: {
          name: freydis.name,
          profile_id: 'neural',
          config: { model_id: freydis.id, iterations: 2, time_limit: 0.05 },
        },
      },
    },
  });
  expect(response.ok()).toBe(true);
  const game = await response.json();
  await page.goto('/games/' + game.id);
  await page.getByRole('button', { name: /Pause AI/ }).click();
  await expect(page.getByRole('button', { name: 'Restart game', exact: true })).toHaveCount(0);
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
  await setup.getByRole('button', { name: 'AI vs AI', exact: true }).click();
  await expect(setup.getByRole('button', { name: 'AI vs AI', exact: true })).toHaveClass('active');
  await expect(setup.getByRole('combobox', { name: 'Attackers AI profile' })).toHaveValue(
    'baseline',
  );
  await expect(setup.getByRole('option', { name: /UI Ulf|UI Freydis/ })).toHaveCount(0);
  await expect(setup.getByRole('spinbutton')).toHaveCount(0);
  await setup.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await openCollection(page);
  const ulfCard = page
    .getByRole('article')
    .filter({ has: page.getByRole('heading', { name: ulf.name, exact: true }) });
  await expect(ulfCard.locator('[data-rune]')).toHaveAttribute('data-rune', ulf.rune);
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();
  const row = page.locator('.recorded-games tbody tr').filter({ hasText: ulf.name });
  await expect(row).toContainText(freydis.name);
  expect(game.players.attackers.rune).toBe(ulf.rune);
  expect(game.players.defenders.rune).toBe(freydis.rune);
  await expect(page.locator('.header-note')).toHaveCount(0);
  await expect(page.locator('p.eyebrow')).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  expect(errors).toEqual([]);
});
