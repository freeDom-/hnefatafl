import { expect, test } from './auth-fixture';
import { openCreation, selectWorkshopJob } from './workshop-helpers';

const endpoint = 'http://127.0.0.1:8001/api/training/jobs';

test('Schedule-Free settings, actual training diagnostics and immutable resume work on desktop and mobile', async ({
  page,
  request,
}, info) => {
  await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await openCreation(page);
  await page.getByLabel('Battlefield', { exact: true }).selectOption('brandubh');
  await page.getByLabel('Training preset', { exact: true }).selectOption('smoke');
  const optimizer = page.getByLabel('Optimizer', { exact: true });
  await expect(optimizer).toHaveValue('adamw');
  await optimizer.selectOption('adamw_schedulefree');
  const warmup = page.getByLabel('Schedule-Free warm-up steps', { exact: true });
  await expect(warmup).toHaveValue('0');
  await warmup.fill('12');
  await page
    .getByRole('button', { name: 'Explain Schedule-Free warm-up steps', exact: true })
    .click();
  await expect(page.getByRole('tooltip')).toContainText(
    'One training iteration can contain many optimizer updates',
  );
  await page.keyboard.press('Escape');
  await page.getByLabel('Base learning rate', { exact: true }).fill('0.003');
  await page.getByLabel('Training iterations', { exact: true }).fill('2');
  const name = `Schedule-Free ${info.project.name}`;
  await page.getByLabel('Model name', { exact: true }).fill(name);
  await page
    .getByLabel('Automatic hyperparameter updates', { exact: true })
    .selectOption('hypercontroller');
  await page.getByRole('button', { name: 'Start training', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('with HyperController is not supported yet');
  await page.getByLabel('Automatic hyperparameter updates', { exact: true }).selectOption('off');
  await page.getByRole('button', { name: /Advanced configuration/ }).click();
  await page.getByLabel('Self-play move limit', { exact: true }).fill('4');
  await page
    .getByLabel('Random seed', { exact: true })
    .fill(info.project.name === 'mobile' ? '71' : '70');
  const creation = page.waitForResponse(
    (r) => r.url().endsWith('/api/training/jobs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Start training', exact: true }).click();
  const response = await creation;
  expect(response.status()).toBe(201);
  const id = (await response.json()).id;
  await expect
    .poll(async () => (await (await request.get(`${endpoint}/${id}`)).json()).status, {
      timeout: 60000,
    })
    .toBe('completed');
  const job = await (await request.get(`${endpoint}/${id}`)).json();
  expect(job.progress.metrics.optimizer).toMatchObject({
    name: 'adamw_schedulefree',
    warmup_steps: 12,
    base_learning_rate: 0.003,
  });
  expect(job.progress.metrics.optimizer.completed_steps).toBeGreaterThan(0);
  await selectWorkshopJob(page, id, name);
  await expect(page.getByLabel('Schedule-Free AdamW', { exact: true })).toContainText(
    'Completed optimizer steps',
  );
  await page.getByRole('button', { name: 'Resume from checkpoint', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Schedule-Free AdamW', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Schedule-Free warm-up steps', { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole('spinbutton', { name: 'Schedule-Free warm-up steps', exact: true }),
  ).toHaveCount(0);
  await expect(dialog.getByLabel('Base learning rate', { exact: true })).toHaveValue('0.003');
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
});
