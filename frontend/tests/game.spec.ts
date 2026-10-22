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

test('training dashboard resumes, renames, reuses parameters and deletes training data while preserving playable models', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(180000);
  test.skip(
    testInfo.project.name === 'mobile',
    'CPU training is verified once; mobile gameplay is checked separately.',
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await page.getByLabel('Model name', { exact: true }).fill('Browser smoke strategist');
  await page.getByRole('combobox', { name: 'Training preset', exact: true }).selectOption('smoke');
  await page.getByRole('button', { name: /Advanced configuration/ }).click();
  await page.getByRole('combobox', { name: 'Optimizer', exact: true }).selectOption('sgd');
  await page.getByRole('spinbutton', { name: 'Learning rate', exact: true }).fill('0.002');
  await page.getByRole('spinbutton', { name: 'Weight regularization', exact: true }).fill('0.003');
  await page.getByRole('spinbutton', { name: 'Momentum', exact: true }).fill('0.8');
  const firstTraining = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/training/jobs') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Start training', exact: false }).click();
  const firstJob = await (await firstTraining).json();
  await expect(page.getByRole('status')).toContainText('training job has started');
  await expect(page.locator('.job-detail .job-status')).toHaveText('completed', { timeout: 90000 });
  await expect(page.getByRole('img', { name: 'Loss by iteration', exact: true })).toBeVisible();
  await expect(
    page.getByRole('img', { name: 'Evaluation by iteration', exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('.model-card').getByRole('heading', { name: 'Browser smoke strategist' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resume from checkpoint' })).toBeEnabled();
  await page.getByRole('button', { name: 'Resume from checkpoint' }).click();
  const resume = page.getByRole('dialog', { name: 'Resume training', exact: true });
  await expect(resume.getByRole('spinbutton', { name: 'Target total iterations' })).toHaveValue(
    '2',
  );
  await expect(resume.getByLabel('Learning rate', { exact: true })).toHaveValue('0.002');
  await expect(resume.getByLabel('Weight decay', { exact: true })).toHaveValue('0.003');
  await expect(resume.getByLabel('Momentum', { exact: true })).toHaveValue('0.8');
  await resume.getByLabel('Learning rate', { exact: true }).fill('0.0007');
  await resume.getByLabel('Weight decay', { exact: true }).fill('0.004');
  await resume.getByLabel('Momentum', { exact: true }).fill('0.65');
  const continued = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/training/jobs/${firstJob.id}/resume`) &&
      response.request().method() === 'POST',
  );
  await resume.getByRole('button', { name: 'Continue training', exact: true }).click();
  const continuedJob = await (await continued).json();
  expect(continuedJob.config).toMatchObject({
    learning_rate: 0.0007,
    weight_decay: 0.004,
    momentum: 0.65,
    optimizer: 'sgd',
  });
  await expect(resume).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText(
    'Training resumed from iteration 1 toward 2.',
  );
  await expect(page.locator('.job-detail .job-status')).toHaveText('completed', { timeout: 90000 });
  await expect(
    page.getByRole('img', { name: 'Loss by iteration', exact: true }).locator('circle'),
  ).toHaveCount(4);
  await expect(
    page.getByRole('img', { name: 'Evaluation by iteration', exact: true }).locator('circle'),
  ).toHaveCount(2);
  const api = 'http://127.0.0.1:8001/api';
  const savedJob = await (await request.get(`${api}/training/jobs/${continuedJob.id}`)).json();
  const retainedSettings = await (
    await request.get(`${api}/training/jobs/${continuedJob.id}/resume`)
  ).json();
  expect(retainedSettings).toMatchObject({
    learning_rate: 0.0007,
    weight_decay: 0.004,
    momentum: 0.65,
    completed_iteration: 2,
  });
  const models = await (await request.get(`${api}/models`)).json();
  const model = models.find((entry: { id: string }) => entry.id === savedJob.model_id);
  expect(model).toBeTruthy();
  const card = page.locator(`.model-card[data-model-id="${model.id}"]`);
  await expect(
    card.getByRole('definition').filter({ hasText: '4 channels · 0 blocks' }),
  ).toBeVisible();
  await expect(card.locator('[data-rune]')).toHaveAttribute('data-rune', model.rune);

  const historyResponse = await request.post(`${api}/games`, {
    data: {
      ruleset: model.ruleset,
      mode: 'human-ai',
      human_side: 'attackers',
      players: {
        defenders: {
          kind: 'ai',
          name: model.name,
          profile_id: 'neural',
          config: { algorithm: 'neural', model_id: model.id, iterations: 2, time_limit: 0.05 },
        },
      },
    },
  });
  expect(historyResponse.ok()).toBe(true);
  const history = await historyResponse.json();

  await card.getByRole('button', { name: `Rename model ${model.name}`, exact: true }).click();
  const rename = page.getByRole('dialog', { name: 'Rename model', exact: true });
  const renamedName = 'Browser Fjord strategist';
  await rename.getByRole('textbox', { name: 'Model name', exact: true }).fill(renamedName);
  await rename.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(rename).toHaveCount(0);
  await expect(card.getByRole('heading', { name: renamedName, exact: true })).toBeVisible();
  const renamed = (await (await request.get(`${api}/models`)).json()).find(
    (entry: { id: string }) => entry.id === model.id,
  );
  expect(renamed).toMatchObject({
    id: model.id,
    name: renamedName,
    rune: model.rune,
    training_config: model.training_config,
    architecture: model.architecture,
  });
  expect(await (await request.get(`${api}/games/${history.id}`)).json()).toEqual(history);

  await page
    .getByRole('combobox', { name: 'Training preset', exact: true })
    .selectOption('recommended');
  await page.getByRole('combobox', { name: 'Battlefield', exact: true }).selectOption('brandubh');
  await card
    .getByRole('button', { name: `Load training parameters for ${renamedName}`, exact: true })
    .click();
  await expect(page.getByRole('status')).toContainText(
    `Training parameters loaded from ${renamedName}.`,
  );
  await expect(page.getByRole('combobox', { name: 'Battlefield', exact: true })).toHaveValue(
    model.ruleset,
  );
  await expect(page.getByRole('textbox', { name: 'Model name', exact: true })).toHaveValue(
    renamedName,
  );
  await expect(
    page.getByRole('spinbutton', { name: 'Training iterations', exact: true }),
  ).toHaveValue(String(model.training_config.iterations));
  await expect(
    page.getByRole('spinbutton', { name: 'Self-play games per iteration', exact: true }),
  ).toHaveValue(String(model.training_config.games));
  await expect(page.getByRole('spinbutton', { name: 'Network channels', exact: true })).toHaveValue(
    String(model.architecture.channels),
  );
  await expect(page.getByRole('spinbutton', { name: 'Residual blocks', exact: true })).toHaveValue(
    String(model.architecture.blocks),
  );
  await expect(page.getByRole('spinbutton', { name: 'Random seed', exact: true })).toHaveValue(
    String(model.training_config.seed),
  );

  expect(
    await page
      .locator('.training-config form')
      .evaluate((form: HTMLFormElement) => form.checkValidity()),
  ).toBe(true);

  await page.getByRole('button', { name: 'Delete training data', exact: true }).click();
  const removeTraining = page.getByRole('dialog', { name: 'Delete training data?', exact: true });
  await expect(removeTraining).toContainText('checkpoints');
  await removeTraining.getByRole('button', { name: 'Keep it', exact: true }).click();
  expect((await request.get(`${api}/training/jobs/${continuedJob.id}`)).ok()).toBe(true);
  await page.getByRole('button', { name: 'Delete training data', exact: true }).click();
  await removeTraining.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(removeTraining).toHaveCount(0);
  await expect(page.locator('.job-detail')).toHaveCount(0);
  expect((await request.get(`${api}/training/jobs/${continuedJob.id}`)).status()).toBe(404);
  expect((await request.get(`${api}/training/jobs/${continuedJob.id}/resume`)).status()).toBe(404);
  expect((await request.get(`${api}/training/jobs/${firstJob.id}`)).ok()).toBe(true);
  await expect(card.getByRole('heading', { name: renamedName, exact: true })).toBeVisible();
  expect(await (await request.get(`${api}/games/${history.id}`)).json()).toEqual(history);

  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page.getByRole('button', { name: 'New tournament', exact: true }).click();
  const tournamentSetup = page.getByRole('dialog', { name: 'Set up a tournament', exact: true });
  await expect(tournamentSetup.getByRole('textbox')).toHaveCount(1);
  await tournamentSetup
    .getByRole('textbox', { name: 'Tournament name', exact: true })
    .fill('Fjord model provenance');
  await tournamentSetup.getByRole('checkbox', { name: 'Brandubh 7 × 7', exact: true }).uncheck();
  await tournamentSetup
    .getByRole('checkbox', { name: 'Tablut (market rules) 9 × 9', exact: true })
    .check();
  await tournamentSetup.getByLabel('Opponent 1 profile', { exact: true }).selectOption('neural');
  await tournamentSetup
    .getByLabel('Opponent 1 trained model', { exact: true })
    .selectOption(model.id);
  await tournamentSetup.getByLabel('Opponent 2 profile', { exact: true }).selectOption('baseline');
  await expect(tournamentSetup.locator('.model-training-summary')).toContainText(
    '4 channels · 0 blocks',
  );
  await tournamentSetup.getByLabel('Opponent 1 time limit (seconds)', { exact: true }).fill('0.01');
  await tournamentSetup.getByLabel('Move limit per game', { exact: true }).fill('1');
  const tournamentCreated = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/tournaments') && response.request().method() === 'POST',
  );
  await tournamentSetup.getByRole('button', { name: 'Start tournament', exact: true }).click();
  const tournament = await (await tournamentCreated).json();
  await expect(page.locator('.tournament-detail .tournament-status')).toHaveText('Completed', {
    timeout: 30000,
  });
  const snapshotConfig = Object.fromEntries(
    Object.entries(model.training_config).filter(
      ([key]) => !['output_dir', 'registry_dir', 'resume'].includes(key),
    ),
  );
  expect(tournament.config.players[0]).toMatchObject({
    name: renamedName,
    model_name: renamedName,
    rune: model.rune,
    model: {
      id: model.id,
      architecture: model.architecture,
      training_config: snapshotConfig,
    },
  });
  expect(tournament.config.players[0].model).not.toHaveProperty('path');
  for (const pathField of ['output_dir', 'registry_dir', 'resume'])
    expect(tournament.config.players[0].model.training_config).not.toHaveProperty(pathField);
  await expect(
    page
      .locator('.tournament-standing')
      .first()
      .getByRole('heading', { name: renamedName, exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('.tournament-standing').first().locator('.model-training-summary'),
  ).toContainText('4 channels · 0 blocks');
  await page.reload();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Tournaments', exact: true }).click();
  await page
    .getByRole('button', { name: 'View tournament Fjord model provenance', exact: true })
    .click();
  await expect(
    page.locator('.tournament-standing').first().locator('.model-training-summary'),
  ).toContainText('4 channels · 0 blocks');

  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: /New game/ }).click();
  await page.getByRole('button', { name: 'Human vs AI', exact: true }).click();
  await page.getByRole('radio', { name: /Tablut/ }).check();
  await page.getByLabel('Your side').selectOption('attackers');
  await page.getByLabel('Defenders algorithm').selectOption('neural');
  await page
    .getByRole('combobox', { name: 'Defenders trained model', exact: true })
    .selectOption(model.id);
  const played = page.waitForResponse(
    (response) => response.url().endsWith('/api/games') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'To the table' }).click();
  expect((await (await played).json()).players.defenders).toMatchObject({
    name: renamedName,
    model_name: renamedName,
    config: { model_id: model.id },
  });
  await expect(page.getByText('Attackers to move', { exact: true })).toBeVisible({
    timeout: 20000,
  });
  await expect(page.locator('.army h3').nth(1)).toContainText(renamedName);
});
