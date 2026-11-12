import { expect, test, type APIRequestContext } from './auth-fixture';
import { openWorkshop } from './play-helpers';
import { selectWorkshopJob } from './workshop-helpers';

const endpoint = 'http://127.0.0.1:8001/api/training/jobs';
async function completed(request: APIRequestContext, id: string) {
  await expect
    .poll(
      async () => (await (await request.get(`${endpoint}/${id}`, { maxRetries: 2 })).json()).status,
      {
        timeout: 60000,
      },
    )
    .toBe('completed');
  return (await request.get(`${endpoint}/${id}`, { maxRetries: 2 })).json();
}

test('applies a real Optuna winner through Resume Training to a real CPU checkpoint', async ({
  page,
  request,
}, info) => {
  const sourceName = `Source checkpoint ${info.project.name}`;
  const started = await request.post(endpoint, {
    data: {
      preset: 'smoke',
      device: 'cpu',
      cpu_threads: 1,
      seed: 17,
      model_name: sourceName,
      max_game_plies: 2,
      evaluation_max_plies: 2,
      mcts_time_limit: 5,
    },
  });
  expect(started.status()).toBe(201);
  const source = await completed(request, (await started.json()).id);
  const studyName = `Applied winner ${info.project.name}`;
  const tuningResponse = await request.post(endpoint, {
    data: {
      kind: 'tune',
      study_name: studyName,
      trials: 1,
      source_checkpoint: `${source.id}:last.pt`,
      training: { ...source.config, iterations: 2 },
      search_space: {
        learning_rate: { type: 'categorical', choices: [0.004] },
        weight_decay: { type: 'categorical', choices: [0.02] },
        mcts_iterations: { type: 'categorical', choices: [7] },
      },
    },
  });
  expect(tuningResponse.status()).toBe(201);
  const tuned = await completed(request, (await tuningResponse.json()).id);
  expect(tuned.result.best_parameters).toEqual({
    learning_rate: 0.004,
    weight_decay: 0.02,
    mcts_iterations: 7,
  });
  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await openWorkshop(page);
  await selectWorkshopJob(page, tuned.id, studyName);
  await page.getByRole('tab', { name: 'Tuning results', exact: true }).click();
  await page.getByLabel('Additional iterations', { exact: true }).fill('1');
  await page.getByLabel('Apply to existing training').selectOption(source.id);
  await page.getByRole('button', { name: 'Apply best parameters', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Resume training', exact: true });
  await expect(dialog.getByLabel('Learning rate', { exact: true })).toHaveValue('0.004');
  await expect(dialog.getByLabel('Weight decay', { exact: true })).toHaveValue('0.02');
  await expect(dialog.getByLabel('MCTS iterations per move', { exact: true })).toHaveValue('7');
  await expect(dialog.getByRole('table', { name: 'Parameter changes' })).toContainText('0.001');
  expect((await (await request.get(endpoint)).json()).length).toBeGreaterThanOrEqual(2);
  const submitting = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/training/jobs/${source.id}/resume`) &&
      response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Continue training', exact: true }).click();
  const submitted = await submitting;
  expect(submitted.status()).toBe(201);
  expect(submitted.request().postDataJSON()).toEqual({
    iterations: 2,
    learning_rate: 0.004,
    weight_decay: 0.02,
    mcts_iterations: 7,
  });
  const continued = await completed(request, (await submitted.json()).id);
  const effective = await (await request.get(`${endpoint}/${continued.id}/resume`)).json();
  expect(effective.learning_rate).toBe(0.004);
  expect(effective.weight_decay).toBe(0.02);
  expect(continued.progress.history.map((row: { iteration: number }) => row.iteration)).toEqual([
    1, 2,
  ]);
  expect(continued.progress.self_play_games.cumulative).toBe(2);
  const unchanged = await (await request.get(`${endpoint}/${source.id}/resume`)).json();
  expect(unchanged.learning_rate).toBe(0.001);
  expect(unchanged.weight_decay).toBe(0.0001);
});
