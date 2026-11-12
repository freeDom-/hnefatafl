import { expect, test, type Page } from './auth-fixture';
import { openWorkshop } from './play-helpers';
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
  await openWorkshop(page);
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

test('legacy and resumed optimizer-update counts share the KPI, selectable chart and localized table', async ({
  page,
}) => {
  const job = {
    ...workshopFixture('legacy-optimizer-updates', 6, 'completed'),
    progress: {
      history: [
        { iteration: 1, optimizer_updates: 12345 },
        { iteration: 2, optimizer_updates: 0 },
        { iteration: 3, updates: 4, optimizer_updates: 6000 },
        { iteration: 4, updates: null, optimizer_updates: 7 },
        { iteration: 5, optimizer: { completed_steps: 6000 } },
        { iteration: 6, updates: 0, optimizer_updates: 6000 },
      ],
    },
  };
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: [job] }));
  await open(page);
  await page.getByRole('tab', { name: 'Learning', exact: true }).click();
  await expect(page.locator('.training-history table')).toHaveCount(0);
  await expect(page.getByRole('option', { name: 'Optimizer updates', exact: true })).toBeEnabled();
  await page.getByLabel('Learning metric', { exact: true }).selectOption('updates');
  await page.getByText('View graph data', { exact: true }).click();
  for (const language of ['en', 'de']) {
    await page.locator('.language-select select').selectOption(language);
    const label = language === 'en' ? 'Optimizer updates' : 'Optimierungsschritte';
    const chart = page.getByRole('img', { name: label, exact: true });
    await expect(
      page
        .locator('.learning-kpis .training-kpi')
        .filter({ has: page.getByText(label, { exact: true }) })
        .locator('dd'),
    ).toHaveText('0');
    const values = [language === 'en' ? '12,345' : '12.345', '0', '4', '7', '—', '0'];
    const rows = page.locator('.training-history table tbody tr');
    await chart.focus();
    await chart.press('Home');
    for (let index = 0; index < values.length; index++) {
      if (index) await chart.press('ArrowRight');
      await expect(page.locator('.chart-tooltip')).toHaveText(
        `Iteration ${index + 1} · ${label}: ${values[index]}`,
      );
      await expect(rows.nth(index).getByRole('cell').last()).toHaveText(values[index]);
    }
  }
});

test('tiny weight decay uses its own scale and exact localized values', async ({ page }) => {
  const job = {
    ...workshopFixture('tiny-weight-decay', 2, 'completed'),
    progress: {
      history: [
        {
          iteration: 1,
          learning_rate: 1e-7,
          training_parameters: { version: 1, weight_decay: 0.00000125 },
        },
        {
          iteration: 2,
          learning_rate: 1e-7,
          training_parameters: { version: 1, weight_decay: 0.0000025 },
        },
      ],
    },
  };
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: [job] }));
  await open(page);
  await page.getByRole('tab', { name: 'Learning', exact: true }).click();
  await page.getByLabel('Learning metric', { exact: true }).selectOption('weight_decay');
  await page.getByText('View graph data', { exact: true }).click();
  for (const language of ['en', 'de']) {
    await page.locator('.language-select select').selectOption(language);
    const chart = page.locator('.training-history svg');
    await expect(chart).toHaveAccessibleName(
      language === 'en' ? 'Weight decay' : 'Gewichtsverfall',
    );
    const positions = await chart
      .locator('circle')
      .evaluateAll((circles) => circles.map((circle) => Number(circle.getAttribute('cy'))));
    expect(positions).toHaveLength(2);
    expect(Math.max(...positions) - Math.min(...positions)).toBeGreaterThan(70);
    const mantissas = language === 'en' ? ['1.25E-6', '2.5E-6'] : ['1,25E-6', '2,5E-6'];
    const rows = page.locator('.training-history table tbody tr');
    await chart.focus();
    await chart.press('Home');
    await expect(page.locator('.chart-tooltip')).toContainText(mantissas[0]);
    await expect(rows.nth(0).getByRole('cell').last()).toHaveText(mantissas[0]);
    await chart.press('End');
    await expect(page.locator('.chart-tooltip')).toContainText(mantissas[1]);
    await expect(rows.nth(1).getByRole('cell').last()).toHaveText(mantissas[1]);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

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
