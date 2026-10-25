import { expect, test, type APIRequestContext } from './auth-fixture';
import { selectWorkshopJob } from './workshop-helpers';

const endpoint = 'http://127.0.0.1:8001/api/training/jobs';

async function finished(request: APIRequestContext, id: string) {
  await expect
    .poll(async () => (await (await request.get(`${endpoint}/${id}`)).json()).status, {
      timeout: 90000,
    })
    .toBe('completed');
  return (await request.get(`${endpoint}/${id}`)).json();
}

test('real random_d4 training, workshop control, reuse and reviewed resume', async ({
  page,
  request,
}, info) => {
  const name = `Symmetry witness ${info.project.name}`;
  const response = await request.post(endpoint, {
    data: {
      preset: 'smoke',
      ruleset: 'brandubh',
      model_name: name,
      device: 'cpu',
      cpu_threads: 1,
      max_game_plies: 4,
      evaluation_max_plies: 2,
      mcts_time_limit: 5,
      seed: 0,
      mixed_precision: false,
      epochs: 2,
      training_symmetry_mode: 'random_d4',
    },
  });
  expect(response.status()).toBe(201);
  const job = await finished(request, (await response.json()).id);
  // The server enforced and recorded the setting, and real optimization reported exposures.
  expect(job.config.training_symmetry_mode).toBe('random_d4');
  const augmentation = job.progress.metrics.augmentation;
  expect(augmentation.mode).toBe('random_d4');
  expect(augmentation.exposures).toBeGreaterThan(0);
  expect(augmentation.exposures).toBe(augmentation.training_exposures);
  expect(augmentation.transform_counts).toHaveLength(8);
  expect(job.progress.performance.augmentation_exposures).toBe(augmentation.exposures);
  // Games and replay keep their original meaning: augmentation adds neither.
  expect(job.progress.self_play_games.cumulative).toBe(1);
  expect(augmentation.exposures).toBeGreaterThan(job.progress.metrics.replay_positions);

  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await selectWorkshopJob(page, job.id, name);
  const control = page.getByRole('combobox', {
    name: 'Training symmetry augmentation',
    exact: true,
  });
  await page.getByRole('button', { name: 'Use parameters', exact: true }).click();
  await expect(control).toHaveValue('random_d4');
  await expect(
    page.getByText(
      'Randomly rotate or reflect training positions and their corresponding move targets',
    ),
  ).toBeVisible();
  // The explanation separates training samples from rules and search budgets.
  await expect(page.getByText('not the game rules or MCTS search budget')).toBeVisible();
  await page.getByLabel('Language', { exact: true }).selectOption('de');
  await expect(
    page
      .getByRole('combobox', { name: 'Symmetrie-Datenaugmentation', exact: true })
      .locator('option:checked'),
  ).toHaveText('Zufällige D4-Drehungen und -Spiegelungen');
  await page.getByLabel('Sprache', { exact: true }).selectOption('en');
  await page
    .getByRole('combobox', { name: 'Training symmetry augmentation', exact: true })
    .selectOption('off');

  // A reviewed resume can deliberately switch off -> random_d4 and records it.
  const resumedResponse = await request.post(`${endpoint}/${job.id}/resume`, {
    data: { iterations: 2, device: 'cpu', training_symmetry_mode: 'random_d4' },
  });
  expect(resumedResponse.status()).toBe(201);
  const resumed = await finished(request, (await resumedResponse.json()).id);
  expect(resumed.config.training_symmetry_mode).toBe('random_d4');
  expect(resumed.progress.metrics.augmentation.exposures).toBeGreaterThan(0);
  // One additional iteration on top of the source job's one: augmentation adds no games.
  expect(resumed.progress.self_play_games).toEqual({
    version: 1,
    job: 1,
    baseline: 1,
    cumulative: 2,
  });
  // The source job and its configuration are untouched.
  expect((await (await request.get(`${endpoint}/${job.id}`)).json()).config).toMatchObject({
    training_symmetry_mode: 'random_d4',
    iterations: 1,
  });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
});
