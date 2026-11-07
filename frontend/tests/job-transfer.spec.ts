import { selectWorkshopJob } from './workshop-helpers';
import { readFile } from 'node:fs/promises';
import { expect, test } from './auth-fixture';

const api = 'http://127.0.0.1:8001/api';

test('exports, imports and resumes a real CPU job through the workshop', async ({
  page,
  request,
}, info) => {
  const name = `Portable raven ${info.project.name}`;
  const started = await request.post(`${api}/training/jobs`, {
    data: {
      preset: 'smoke',
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
      async () =>
        (await (await request.get(`${api}/training/jobs/${source.id}`, { maxRetries: 2 })).json())
          .status,
      { timeout: 30000 },
    )
    .toBe('completed');
  const saved = await (await request.get(`${api}/training/jobs/${source.id}`)).json();
  const modelName = `Searchable model association ${info.project.name}`;
  expect(
    (await request.patch(`${api}/models/${saved.model_id}`, { data: { name: modelName } })).ok(),
  ).toBe(true);
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await selectWorkshopJob(page, source.id, name);
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export job', exact: true }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe(`hnefatafl-job-${source.id}.tar.gz`);
  const archive = await readFile((await download.path())!);
  expect(archive.subarray(0, 2)).toEqual(Buffer.from([0x1f, 0x8b]));
  const importing = page.waitForResponse((response) =>
    response.url().endsWith('/api/training/jobs/import?summary=true'),
  );
  let releaseHistory!: () => void;
  const historyGate = new Promise<void>((resolve) => {
    releaseHistory = resolve;
  });
  let historyRequests = 0;
  await page.route(/\/api\/training\/jobs\/[0-9a-f]{32}$/, async (route) => {
    if (route.request().method() === 'GET' && !route.request().url().endsWith(source.id)) {
      historyRequests++;
      await historyGate;
    }
    await route.continue();
  });
  await page.getByLabel('Import job', { exact: true }).setInputFiles({
    name: download.suggestedFilename(),
    mimeType: 'application/gzip',
    buffer: archive,
  });
  const importedResponse = await importing;
  expect(importedResponse.ok()).toBe(true);
  const imported = await importedResponse.json();
  expect(imported.id).not.toBe(source.id);
  expect(imported.summary).toBe(true);
  expect(imported.progress.history).toBeUndefined();
  expect(imported.resumable).toBe(true);
  try {
    await expect(page.locator('.training-analysis-panel').getByRole('status')).toHaveText(
      'Loading history…',
    );
  } finally {
    releaseHistory();
  }
  await expect(page.getByText('Loading history…', { exact: true })).toHaveCount(0);
  expect(historyRequests).toBe(1);
  await expect(page.locator('.job-row.selected')).toHaveCount(1);
  await expect(page.locator('.job-detail .job-status')).toHaveText('completed');
  await expect(
    page.getByText('Job imported. Review its history or resume from its full checkpoint.'),
  ).toBeVisible();
  await expect(page.locator(`.model-card[data-model-id="${imported.model_id}"]`)).toHaveCount(1);
  const navigation = page.locator('.job-navigation-options');
  if ((await navigation.getAttribute('open')) === null) await navigation.locator('summary').click();
  await page.getByLabel('Search jobs', { exact: true }).fill(modelName);
  // Mobile renders the filtered jobs through the selector and hides the cards.
  await expect(page.locator(`.job-row[data-job-id="${imported.id}"]`)).toHaveCount(1);
  await page
    .getByLabel('Search jobs', { exact: true })
    .fill('Unrelated model without associations');
  await expect(page.locator('.job-row')).toHaveCount(0);
  await page.getByLabel('Search jobs', { exact: true }).fill('');
  expect(historyRequests).toBe(1);
  await page.getByRole('button', { name: 'Resume from checkpoint', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Resume training', exact: true });
  await expect(dialog.getByRole('spinbutton', { name: 'Target total iterations' })).toHaveValue(
    '2',
  );
  await expect(dialog.getByRole('combobox', { name: 'Training preset', exact: true })).toHaveCount(
    0,
  );
  await expect(
    dialog.getByRole('spinbutton', { name: 'Self-play games per iteration' }),
  ).toHaveValue('1');
  await dialog
    .getByRole('combobox', { name: 'Evaluation opponent', exact: true })
    .selectOption('champion');
  await expect(dialog.getByRole('spinbutton', { name: 'Champion confidence level' })).toHaveValue(
    '0.95',
  );
  await expect(dialog.getByRole('spinbutton', { name: 'Jarl evaluation depth' })).toHaveCount(0);
  const resuming = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/training/jobs/${imported.id}/resume`) &&
      response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Continue training', exact: true }).click();
  const resumedResponse = await resuming;
  expect(resumedResponse.ok()).toBe(true);
  const resumed = await resumedResponse.json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`${api}/training/jobs/${resumed.id}`, { maxRetries: 2 })).json())
          .status,
      { timeout: 30000 },
    )
    .toBe('completed');
  const continued = await (
    await request.get(`${api}/training/jobs/${resumed.id}`, { maxRetries: 2 })
  ).json();
  expect(continued.progress.metrics.evaluation.opponent).toBe('champion');
  expect(continued.progress.evaluation_selection.champion_iteration).toBe(1);
  const retained = await (await request.get(`${api}/training/jobs/${imported.id}`)).json();
  expect(retained.progress.iteration).toBe(1);
});
