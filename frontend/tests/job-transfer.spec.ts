import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';

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
      async () => (await (await request.get(`${api}/training/jobs/${source.id}`)).json()).status,
      { timeout: 30000 },
    )
    .toBe('completed');
  await page.goto('/');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await page.locator('.job-row').filter({ hasText: name }).click();
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export job', exact: true }).click();
  const download = await downloading;
  const zip = await readFile((await download.path())!);
  expect(zip.length).toBeGreaterThan(0);
  const importing = page.waitForResponse((response) =>
    response.url().endsWith('/api/training/jobs/import'),
  );
  await page.getByLabel('Import job', { exact: true }).setInputFiles({
    name: download.suggestedFilename(),
    mimeType: 'application/zip',
    buffer: zip,
  });
  const importedResponse = await importing;
  expect(importedResponse.ok()).toBe(true);
  const imported = await importedResponse.json();
  expect(imported.id).not.toBe(source.id);
  expect(imported.resumable).toBe(true);
  await expect(page.locator('.job-row.selected')).toHaveCount(1);
  await expect(page.locator('.job-detail .job-status')).toHaveText('completed');
  await expect(
    page.getByText('Job imported. Review its history or resume from its full checkpoint.'),
  ).toBeVisible();
  await expect(page.locator(`.model-card[data-model-id="${imported.model_id}"]`)).toHaveCount(1);
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
      async () => (await (await request.get(`${api}/training/jobs/${resumed.id}`)).json()).status,
      { timeout: 30000 },
    )
    .toBe('completed');
  const retained = await (await request.get(`${api}/training/jobs/${imported.id}`)).json();
  expect(retained.progress.iteration).toBe(1);
});
