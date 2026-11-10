import { expect, test, type Page } from './auth-fixture';
import { workshopFixture } from './workshop-fixtures';
import { learningHistory } from './learning-fixtures';
import { selectWorkshopJob } from './workshop-helpers';

async function open(page: Page, mode = 'light') {
  await page.addInitScript((mode) => {
    localStorage.setItem('hnefatafl-language', 'en');
    localStorage.setItem(
      'hnefatafl-appearance',
      JSON.stringify({ mode, tokens: 'wood', board: 'walnut' }),
    );
  }, mode);
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
}

for (const mode of ['light', 'dark']) {
  test(`${mode} job rows scroll independently, retain position on polls, and support keyboard and pointer selection`, async ({
    page,
  }, info) => {
    await page.clock.install();
    const jobs = Array.from({ length: 60 }, (_, index) => ({
      ...workshopFixture(`scroll-${index}`, 2, index === 0 ? 'running' : 'completed'),
      name: `Training job ${index} with a long name that wraps inside the navigator`,
      created_at: new Date(Date.UTC(2026, 0, 1, 0, index)).toISOString(),
    }));
    let polls = 0;
    await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => {
      polls++;
      return route.fulfill({ json: jobs });
    });
    await open(page, mode);
    const nav = page.getByRole('navigation', { name: 'Training jobs', exact: true });
    const list = nav.locator('.jobs-list');
    const selector = page.getByLabel('Selected job', { exact: true });
    if (info.project.name === 'mobile') {
      await expect(list).toBeHidden();
      await expect(selector).toBeVisible();
      await selector.selectOption('scroll-59');
      await expect(page.getByRole('heading', { name: jobs[59].name, exact: true })).toBeVisible();
      return;
    }
    await expect(selector).toBeHidden();
    await nav.scrollIntoViewIfNeeded();
    await expect(list).toBeVisible();
    expect(await list.evaluate((list) => list.scrollHeight > list.clientHeight)).toBe(true);
    expect(await list.evaluate((list) => list.scrollWidth <= list.clientWidth)).toBe(true);
    const controls = nav.locator('.job-filters');
    const controlsY = (await controls.boundingBox())!.y;
    const pageY = await page.evaluate(() => window.scrollY);
    await list.evaluate((list) => {
      list.scrollTop = 500;
    });
    const scroll = await list.evaluate((list) => list.scrollTop);
    expect(scroll).toBeGreaterThan(0);
    const previousPolls = polls;
    await page.clock.runFor(6000);
    await expect.poll(() => polls).toBeGreaterThan(previousPolls);
    expect(await list.evaluate((list) => list.scrollTop)).toBe(scroll);
    expect((await controls.boundingBox())!.y).toBe(controlsY);
    expect(await page.evaluate(() => window.scrollY)).toBe(pageY);
    for (const label of ['Search jobs', 'Status', 'Sort jobs'])
      await expect(nav.getByLabel(label, { exact: true })).toBeVisible();
    await expect(nav.locator('.navigator-count')).toBeVisible();
    const last = list.locator('.job-row').last();
    await last.focus();
    await expect(last).toBeFocused();
    await expect(last).toBeInViewport();
    await last.press('Enter');
    await expect(last).toHaveAttribute('aria-current', 'true');
    await expect(page.getByRole('heading', { name: jobs[1].name, exact: true })).toBeVisible();
    const first = list.locator('.job-row').first();
    await first.click();
    await expect(first).toHaveAttribute('aria-current', 'true');
    await expect(page.getByRole('heading', { name: jobs[0].name, exact: true })).toBeVisible();
  });
}

test('a short job list keeps its natural height and mobile keeps the selector', async ({
  page,
}, info) => {
  const jobs = [workshopFixture('short', 2, 'completed')];
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: jobs }));
  await open(page);
  const list = page.locator('.training-job-navigator .jobs-list');
  if (info.project.name === 'mobile') {
    await expect(list).toBeHidden();
    await expect(page.getByLabel('Selected job', { exact: true })).toBeVisible();
  } else {
    expect(await list.evaluate((list) => list.scrollHeight)).toBe(
      await list.evaluate((list) => list.clientHeight),
    );
    expect(await list.evaluate((list) => list.clientHeight)).toBeLessThan(300);
  }
});

test('Learning controls, tiny rates, exact values and history survive polling on desktop and mobile', async ({
  page,
}) => {
  await page.clock.install();
  const lineage = learningHistory();
  let jobs = [
    {
      ...workshopFixture('learning', 1),
      name: 'Complete learning lineage',
      progress: { iteration: 1500, history: lineage },
    },
    {
      ...workshopFixture('legacy', 1, 'completed'),
      name: 'Legacy learning lineage',
      progress: { iteration: 1500, history: learningHistory(true) },
    },
  ];
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: jobs }));
  await open(page);
  await page.getByRole('tab', { name: 'Learning', exact: true }).click();
  const metric = page.getByLabel('Learning metric', { exact: true });
  await expect(metric).toHaveValue('loss');
  await expect(page.locator('.training-analysis-panel .training-chart')).toHaveCount(1);
  await expect(page.getByRole('img', { name: 'Loss by iteration', exact: true })).toBeVisible();
  await metric.selectOption('batch_size');
  await page.getByLabel('Iteration range', { exact: true }).selectOption('100');
  const chart = page.getByRole('img', { name: 'Training batch size', exact: true });
  await chart.focus();
  await chart.press('Home');
  await expect(page.locator('.chart-tooltip')).toHaveText(
    'Iteration 1401 · Training batch size: 128',
  );
  jobs = jobs.map((job) => ({ ...job, progress: { ...job.progress, duration: 100 } }));
  await page.clock.runFor(6000);
  await expect(metric).toHaveValue('batch_size');
  await page.getByText('View graph data', { exact: true }).click();
  const table = page.getByRole('table', { name: 'Training history data' });
  await expect(table.getByRole('row')).toHaveCount(101);
  await expect(table.getByRole('row').last().getByRole('cell').last()).toHaveText('128');
  await metric.selectOption('learning_rate');
  const rate = page.getByRole('img', { name: 'Learning rate (used)', exact: true });
  await rate.focus();
  await rate.press('End');
  await expect(page.locator('.chart-tooltip')).toHaveText(
    'Iteration 1500 · Learning rate (used): 1E-7',
  );
  await expect(table.getByRole('row').last().getByRole('cell').last()).toHaveText('1E-7');
  await selectWorkshopJob(page, 'legacy', 'Legacy learning lineage');
  await expect(metric).toHaveValue('loss');
  await metric.selectOption('mcts_iterations');
  const mcts = page.getByRole('img', { name: 'Self-play MCTS iterations', exact: true });
  await mcts.focus();
  await mcts.press('Home');
  await expect(page.locator('.chart-tooltip')).toHaveText(
    'Iteration 1 · Self-play MCTS iterations: —',
  );
  await mcts.press('End');
  await expect(page.locator('.chart-tooltip')).toHaveText(
    'Iteration 1500 · Self-play MCTS iterations: 256',
  );
});
