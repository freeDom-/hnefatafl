import { expect, test, type APIRequestContext } from './auth-fixture';
import { openCollection, selectWorkshopJob } from './workshop-helpers';

const endpoint = 'http://127.0.0.1:8001/api/training/jobs';
async function finished(request: APIRequestContext, id: string) {
  await expect
    .poll(async () => (await (await request.get(`${endpoint}/${id}`)).json()).status, {
      timeout: 60000,
    })
    .toBe('completed');
  return (await request.get(`${endpoint}/${id}`)).json();
}

test('real CPU counters, editable parameter reuse, independent jobs and checkpoint resume', async ({
  page,
  request,
}, info) => {
  const name = `Counted Freydis ${info.project.name}`;
  const response = await request.post(endpoint, {
    data: {
      preset: 'smoke',
      ruleset: 'brandubh',
      model_name: name,
      device: 'cpu',
      cpu_threads: 1,
      max_game_plies: 2,
      evaluation_max_plies: 2,
      mcts_time_limit: 5,
      seed: 0,
      mixed_precision: false,
    },
  });
  expect(response.status()).toBe(201);
  const original = await finished(request, (await response.json()).id);
  expect(original.progress.self_play_games).toEqual({
    version: 1,
    job: 1,
    baseline: 0,
    cumulative: 1,
  });
  const sourceBefore = JSON.stringify(original.config);
  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await selectWorkshopJob(page, original.id, name);
  const counts = page.getByLabel('Self-play games generated', { exact: true });
  await expect(counts.locator('dd')).toHaveText(['1', '1']);
  await page.getByRole('button', { name: 'Use parameters', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Forge a new strategist', exact: true }),
  ).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('button', { name: /Advanced configuration/ })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
  await expect(page.getByLabel('Random seed', { exact: true })).toHaveValue('0');
  await expect(page.getByLabel('Mixed precision when supported')).not.toBeChecked();
  await page.getByLabel('Model name', { exact: true }).fill(`${name} copy`);
  await page.getByLabel('Training iterations', { exact: true }).fill('2');
  await page.getByLabel('Random seed', { exact: true }).fill('23');
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/api/training/jobs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: /Start training/ }).click();
  const nextResponse = await created;
  const posted = nextResponse.request().postDataJSON();
  expect(posted).toMatchObject({ model_name: `${name} copy`, seed: 23, iterations: 2 });
  expect(posted).not.toHaveProperty('resume');
  const copy = await finished(request, (await nextResponse.json()).id);
  expect(copy.progress.self_play_games).toEqual({ version: 1, job: 2, baseline: 0, cumulative: 2 });
  expect(
    JSON.stringify((await (await request.get(`${endpoint}/${original.id}`)).json()).config),
  ).toBe(sourceBefore);
  const resumedResponse = await request.post(`${endpoint}/${original.id}/resume`, {
    data: { iterations: 3, device: 'cpu' },
  });
  expect(resumedResponse.status()).toBe(201);
  const resumed = await finished(request, (await resumedResponse.json()).id);
  expect(resumed.progress.self_play_games).toEqual({
    version: 1,
    job: 2,
    baseline: 1,
    cumulative: 3,
  });
  // Scores tie at this deliberately truncated budget: the earlier selected model stays at one game.
  expect(resumed.result.model.self_play_games.cumulative).toBe(1);
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await selectWorkshopJob(page, resumed.id, name);
  await expect(counts.locator('dd')).toHaveText(['2', '3']);
  await openCollection(page);
  const model = page.locator('.model-card').filter({
    has: page.getByRole('button', {
      name: `Load training parameters for ${name} copy`,
      exact: true,
    }),
  });
  await expect(model.getByText('Total self-play games').locator('..')).toContainText('1');
  await page
    .getByRole('button', { name: `Load training parameters for ${name} copy`, exact: true })
    .click();
  await expect(page.getByLabel('Model name', { exact: true })).toHaveValue(`${name} copy`);
  await expect(page.getByLabel('Random seed', { exact: true })).toHaveValue('23');
  await page.getByLabel('Language', { exact: true }).selectOption('de');
  await expect(
    page.getByRole('button', { name: 'Parameter übernehmen', exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('Erzeugte Selbstspielpartien').locator('dt')).toHaveText([
    'Selbstspielpartien dieses Auftrags',
    'Kumulative Trainingspartien',
  ]);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
});
