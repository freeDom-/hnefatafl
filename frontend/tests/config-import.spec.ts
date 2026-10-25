import { expect, test } from './auth-fixture';
import { openCreation, selectWorkshopJob } from './workshop-helpers';

const api = 'http://127.0.0.1:8001/api';

test('imports an exporter-compatible config.json into new and resume forms without starting work', async ({
  page,
  request,
}, info) => {
  const name = `Config raven ${info.project.name}`;
  const started = await request.post(`${api}/training/jobs`, {
    data: {
      preset: 'smoke',
      ruleset: 'brandubh',
      device: 'cpu',
      cpu_threads: 1,
      model_name: name,
      max_game_plies: 2,
      evaluation_max_plies: 2,
    },
  });
  expect(started.ok()).toBe(true);
  const source = await started.json();
  await expect
    .poll(
      async () => (await (await request.get(`${api}/training/jobs/${source.id}`)).json()).status,
      { timeout: 60000 },
    )
    .toBe('completed');
  const exported = await (await request.get(`${api}/training/jobs/${source.id}`)).json();
  const importedConfig = {
    ...exported.config,
    model_name: 'Imported raven',
    channels: 12,
    blocks: 3,
    seed: 5,
    batch_size: 17,
    iterations: 1,
  };
  const before = (await (await request.get(`${api}/training/jobs`)).json()).length;

  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await openCreation(page);
  await page.getByLabel('Import config.json', { exact: true }).setInputFiles({
    name: 'config.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(importedConfig)),
  });
  await expect(
    page.getByText('Configuration imported. Review the values before starting.'),
  ).toBeVisible();
  await expect(page.getByLabel('Model name', { exact: true })).toHaveValue('Imported raven');
  await expect(page.getByLabel('Batch size', { exact: true })).toHaveValue('17');
  await expect(page.getByLabel('Network channels', { exact: true })).toHaveValue('12');
  await expect(page.getByLabel('Residual blocks', { exact: true })).toHaveValue('3');
  await expect(page.getByLabel('Random seed', { exact: true })).toHaveValue('5');
  expect((await (await request.get(`${api}/training/jobs`)).json()).length).toBe(before);

  await selectWorkshopJob(page, source.id, name);
  await page.getByRole('button', { name: 'Resume from checkpoint', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Resume training', exact: true });
  await expect(dialog.getByRole('spinbutton', { name: 'Target total iterations' })).toHaveValue(
    '2',
  );
  await dialog.getByLabel('Import config.json', { exact: true }).setInputFiles({
    name: 'config.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(importedConfig)),
  });
  await expect(
    dialog.getByText('Configuration imported. Review the values before continuing.'),
  ).toBeVisible();
  await expect(dialog.getByText(/Checkpoint settings kept:/)).toContainText('Network channels');
  await expect(dialog.getByLabel('Batch size', { exact: true })).toHaveValue('17');
  await expect(dialog.getByRole('spinbutton', { name: 'Target total iterations' })).toHaveValue(
    '2',
  );
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect((await (await request.get(`${api}/training/jobs`)).json()).length).toBe(before);
});
