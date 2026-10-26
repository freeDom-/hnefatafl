import { openCollection } from './workshop-helpers';
import { expect, test, type APIRequestContext, type Page } from './auth-fixture';

const api = 'http://127.0.0.1:8001/api';

async function currentGame(page: Page, request: APIRequestContext) {
  await expect.poll(() => page.evaluate(() => localStorage.getItem('hnefatafl-game'))).toBeTruthy();
  const id = await page.evaluate(() => localStorage.getItem('hnefatafl-game'));
  expect(id).toBeTruthy();
  const response = await request.get(`${api}/games/${id}`);
  expect(response.ok()).toBe(true);
  return response.json();
}

async function completedGame(request: APIRequestContext, name: string) {
  const response = await request.post(`${api}/games`, {
    data: {
      ruleset: 'brandubh',
      mode: 'ai-ai',
      seed: 3,
      players: {
        attackers: { name, config: { algorithm: 'baseline', time_limit: 0.1 } },
        defenders: {
          name: `${name} guardian`,
          config: { algorithm: 'baseline', time_limit: 0.1 },
        },
      },
    },
  });
  expect(response.ok()).toBe(true);
  let game = await response.json();
  for (let ply = 0; !game.winner && ply < 100; ply++) {
    const moved = await request.post(`${api}/games/${game.id}/ai-move`, {
      data: { version: game.version },
    });
    expect(moved.ok()).toBe(true);
    game = await moved.json();
  }
  expect(game.winner).toBeTruthy();
  return game;
}

async function statistics(request: APIRequestContext, query = '') {
  const response = await request.get(`${api}/stats${query}`);
  expect(response.ok()).toBe(true);
  return response.json();
}

test('completed games have no restart control, including after undo in replay', async ({
  page,
  request,
}, info) => {
  const completed = await completedGame(request, `No restart ${info.project.name}`);
  await page.goto('/dashboard?setup=single-player');
  await page.evaluate((id) => localStorage.setItem('hnefatafl-game', id), completed.id);
  await page.goto('/games/' + completed.id);
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Restart game', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Undo move', exact: true }).click();
  await expect
    .poll(async () => (await currentGame(page, request)).cursor)
    .toBe(completed.cursor - 1);
  await expect(page.getByRole('button', { name: 'Restart game', exact: true })).toHaveCount(0);
  const replay = await currentGame(page, request);
  const denied = await request.post(`${api}/games/${replay.id}/restart`, {
    data: { version: replay.version },
  });
  expect(denied.status()).toBe(409);
});

test('new games and restarts receive fresh seeds; record deletion refreshes prominent statistics', async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/dashboard?setup=single-player');
  const setup = page.getByRole('dialog', { name: 'Begin a new saga' });
  await expect(setup).toContainText('A fresh random seed');
  await setup.getByRole('button', { name: 'To the table', exact: true }).click();
  const first = await currentGame(page, request);
  expect(Number.isInteger(first.seed)).toBe(true);
  await expect(page.locator('.detail-row').filter({ hasText: 'Random seed' })).toContainText(
    String(first.seed),
  );

  await page.getByRole('button', { name: 'Restart game', exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('hnefatafl-game')))
    .not.toBe(first.id);
  const restarted = await currentGame(page, request);
  expect(restarted.seed).not.toBe(first.seed);
  expect((await request.get(`${api}/games/${first.id}`)).ok()).toBe(true);

  await page.getByRole('button', { name: /New (?:single-player )?game/ }).click();
  await setup.getByRole('radio', { name: /Brandubh/ }).check();
  await setup.getByRole('button', { name: 'To the table', exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('hnefatafl-game')))
    .not.toBe(restarted.id);
  const active = await currentGame(page, request);
  expect(active.seed).not.toBe(first.seed);
  expect(active.seed).not.toBe(restarted.seed);
  await page.getByRole('button', { name: 'Hall of records', exact: true }).click();

  const removed = await request.delete(`${api}/games/${active.id}?version=${active.version}`);
  expect(removed.ok()).toBe(true);
  expect((await request.get(`${api}/games/${active.id}`)).status()).toBe(404);
  const completed = await completedGame(request, `Record cleanup ${info.project.name}`);
  const before = await statistics(request, '?ruleset=brandubh');
  expect(
    (await request.delete(`${api}/games/${completed.id}?version=${completed.version}`)).ok(),
  ).toBe(true);
  const after = await statistics(request, '?ruleset=brandubh');
  expect(after.total_games).toBe(before.total_games - 1);
  expect(after.completed_games).toBe(before.completed_games - 1);
  for (const [winner, field] of [
    ['attackers', 'attackers_wins'],
    ['defenders', 'defenders_wins'],
    ['draw', 'draws'],
  ] as const) {
    expect(after[field]).toBe(before[field] - Number(completed.recorded_winner === winner));
  }
  const remaining = await (await request.get(`${api}/games?ruleset=brandubh`)).json();
  const finished = remaining.filter(
    (game: { recorded_winner: string | null }) => game.recorded_winner,
  );
  const mean = finished.length
    ? finished.reduce(
        (sum: number, game: { recorded_plies: number }) => sum + game.recorded_plies,
        0,
      ) / finished.length
    : 0;
  expect(after.average_game_length).toBe(mean);
  expect(after.attacker_win_rate).toBe(
    finished.length ? after.attackers_wins / finished.length : 0,
  );
  expect(after.defender_win_rate).toBe(
    finished.length ? after.defenders_wins / finished.length : 0,
  );
  expect(after.draw_rate).toBe(finished.length ? after.draws / finished.length : 0);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  expect(errors).toEqual([]);
});

test('workshop shows training seeds and safely discards hidden unfinished model games after opt-in', async ({
  page,
  request,
}, info) => {
  test.skip(
    info.project.name === 'mobile',
    'Actual training/model cleanup is checked once on desktop; mobile record management runs separately.',
  );
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const finished = await completedGame(request, 'Kept after model cleanup');
  const modelName = 'Eir seed and cleanup';
  const launched = await request.post(`${api}/training/jobs`, {
    data: {
      preset: 'smoke',
      ruleset: 'brandubh',
      device: 'cpu',
      mcts_time_limit: 0.1,
      seed: 104729,
      model_name: modelName,
    },
  });
  expect(launched.ok()).toBe(true);
  const job = await launched.json();
  await expect
    .poll(async () => (await (await request.get(`${api}/training/jobs/${job.id}`)).json()).status, {
      timeout: 60000,
    })
    .toBe('completed');
  const models = await (await request.get(`${api}/models`)).json();
  const model = models.find((entry: { name: string }) => entry.name === modelName);
  expect(model).toBeTruthy();
  expect(model.training_config.seed).toBe(104729);

  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await openCollection(page);
  const card = page
    .locator('.model-card')
    .filter({ has: page.getByRole('heading', { name: modelName }) });
  await expect(card.locator('.model-training-seed')).toContainText('104729');
  expect(model.evaluation.seed).not.toBe(model.training_config.seed);
  await expect(card.locator('.model-training-seed')).not.toContainText(
    String(model.evaluation.seed),
  );

  await page.getByRole('button', { name: 'Dashboard', exact: true }).click();
  await page.getByRole('button', { name: /New (?:single-player )?game/ }).click();
  const setup = page.getByRole('dialog', { name: 'Begin a new saga' });
  await setup.getByRole('radio', { name: /Brandubh/ }).check();
  await setup.getByRole('button', { name: 'Human vs AI', exact: true }).click();
  await setup.getByRole('combobox', { name: 'Your side', exact: true }).selectOption('attackers');
  await expect(
    setup.getByRole('combobox', { name: 'Defenders algorithm', exact: true }),
  ).toHaveValue('neural');
  await expect(
    setup.getByRole('combobox', { name: 'Defenders trained model', exact: true }),
  ).toHaveValue(model.id);
  await expect(setup.locator('.model-recommendation')).toContainText(modelName);
  await setup.getByRole('button', { name: 'To the table', exact: true }).click();
  const current = await currentGame(page, request);
  expect(current.players.defenders.config.model_id).toBe(model.id);

  const hiddenResponse = await request.post(`${api}/games`, {
    data: {
      ruleset: 'brandubh',
      mode: 'human-ai',
      human_side: 'attackers',
      players: {
        defenders: {
          name: 'Hidden model game',
          profile_id: 'neural',
          config: { model_id: model.id, iterations: 1, time_limit: 0.1 },
        },
      },
    },
  });
  expect(hiddenResponse.ok()).toBe(true);
  const hidden = await hiddenResponse.json();
  expect((await request.delete(`${api}/models/${model.id}`)).status()).toBe(409);
  const before = await statistics(request);
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await openCollection(page);
  await page.getByRole('button', { name: `Delete model ${modelName}`, exact: true }).click();
  const confirmation = page.getByRole('dialog', {
    name: `Delete model ${modelName}?`,
    exact: true,
  });
  const discard = confirmation.getByRole('checkbox', {
    name: 'Discard unfinished games and delete model',
    exact: true,
  });
  await expect(confirmation).toContainText('2 unfinished games');
  await expect(discard).not.toBeChecked();
  await expect(confirmation.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled();
  await confirmation.getByRole('button', { name: 'Keep it', exact: true }).click();
  expect((await request.get(`${api}/games/${hidden.id}`)).ok()).toBe(true);
  await page.getByRole('button', { name: `Delete model ${modelName}`, exact: true }).click();
  await discard.check();
  await confirmation.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(card).toHaveCount(0);
  for (const id of [current.id, hidden.id]) {
    expect((await request.get(`${api}/games/${id}`)).status()).toBe(404);
  }
  const after = await statistics(request);
  expect(after.total_games).toBe(before.total_games - 2);
  expect(after.completed_games).toBe(before.completed_games);
  const retained = await request.get(`${api}/games/${finished.id}`);
  expect(retained.ok()).toBe(true);
  expect((await retained.json()).recorded_winner).toBe(finished.recorded_winner);
  expect((await (await request.get(`${api}/games/${finished.id}/replay`)).json()).validated).toBe(
    true,
  );
  const savedJob = await (await request.get(`${api}/training/jobs/${job.id}`)).json();
  expect(savedJob.checkpoint).toBeTruthy();
  expect(savedJob.status).toBe('completed');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('hnefatafl-game'))).toBeNull();
  await page.getByRole('button', { name: 'Dashboard', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await expect(page.getByRole('grid')).toHaveCount(0);
  expect(errors).toEqual([]);
});
