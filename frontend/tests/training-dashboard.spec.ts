import { expect, test, type Page } from './auth-fixture';
import { workshopFixture, workshopFixtures } from './workshop-fixtures';
import { openCreation, selectWorkshopJob } from './workshop-helpers';
async function open(page: Page) {
  await page.addInitScript(() => {
    if (!localStorage.getItem('hnefatafl-language'))
      localStorage.setItem('hnefatafl-language', 'en');
    localStorage.setItem(
      'hnefatafl-appearance',
      JSON.stringify({ mode: 'wood', tokens: 'wood', board: 'walnut' }),
    );
  });
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Overview', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
}
test('continues live updates after a slow poll without overlapping requests', async ({ page }) => {
  await page.clock.install();
  const initial = workshopFixture('running', 1);
  let slow = false;
  let calls = 0;
  let release: (() => Promise<void>) | undefined;
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, async (route) => {
    calls++;
    if (!slow) {
      await route.fulfill({ json: [initial] });
      return;
    }
    await new Promise<void>((resolve) => {
      release = async () => {
        await route.fulfill({ json: [{ ...initial, name: 'Live progress received' }] });
        resolve();
      };
    });
  });
  await open(page);
  await expect(
    page.getByRole('heading', { name: 'Eir · Tablut strategist', exact: true }),
  ).toBeVisible();
  const before = calls;
  slow = true;
  await page.clock.runFor(7500);
  await expect.poll(() => calls).toBe(before + 1);
  await expect.poll(() => Boolean(release)).toBe(true);
  await release!();
  await expect(
    page.getByRole('heading', { name: 'Live progress received', exact: true }),
  ).toBeVisible();
});
test('dashboard keeps form edits, explicit selection and analysis state through refresh', async ({
  page,
}) => {
  let jobs = workshopFixtures();
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: jobs }));
  await open(page);
  await expect(
    page.getByRole('heading', { name: 'Eir · Tablut strategist', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Forge a new strategist', exact: true }),
  ).toHaveAttribute('aria-expanded', 'false');
  await openCreation(page);
  await page.getByLabel('Model name', { exact: true }).fill('Draft survives polling');
  await page.getByRole('button', { name: 'Forge a new strategist', exact: true }).click();
  await selectWorkshopJob(page, 'completed', 'Bjorn · Northern campaign');
  await page.getByRole('tab', { name: 'Learning', exact: true }).click();
  await page.getByLabel('Iteration range', { exact: true }).selectOption('10');
  jobs.reverse();
  await page.getByRole('button', { name: 'Refresh job', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Learning', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByLabel('Iteration range', { exact: true })).toHaveValue('10');
  await expect(
    page.getByRole('heading', { name: 'Bjorn · Northern campaign', exact: true }),
  ).toBeVisible();
  await page.getByText('View graph data', { exact: true }).click();
  await expect(
    page.getByRole('table', { name: 'Training history data', exact: true }).getByRole('row'),
  ).toHaveCount(11);
  await openCreation(page);
  await expect(page.getByLabel('Model name', { exact: true })).toHaveValue(
    'Draft survives polling',
  );
  await page.getByRole('button', { name: 'Forge a new strategist', exact: true }).click();
  const learning = page.getByRole('tab', { name: 'Learning', exact: true });
  await learning.focus();
  await learning.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Evaluation', exact: true })).toBeFocused();
  await page.getByRole('tab', { name: 'Evaluation', exact: true }).press('End');
  await expect(page.getByRole('tab', { name: 'Details', exact: true })).toBeFocused();
  await page.getByText('Training log', { exact: true }).click();
  await expect(page.locator('.training-details .log-view')).toContainText('Worker initialized.');
  await expect(page.getByRole('button', { name: 'Export job', exact: true })).toBeVisible();
});
test('preserves the selected analysis tab and shows the new job content when switching jobs', async ({
  page,
}) => {
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: workshopFixtures() }),
  );
  await open(page);
  // Initial opening keeps the default Overview tab.
  await expect(page.getByRole('tab', { name: 'Overview', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await selectWorkshopJob(page, 'completed', 'Bjorn · Northern campaign');
  await page.getByRole('tab', { name: 'Evaluation', exact: true }).click();
  await expect(page.locator('.training-overview')).toHaveCount(0);
  await expect(page.locator('.evaluation-series')).toHaveCount(1);
  // Switching to another job keeps Evaluation selected and swaps the content.
  await selectWorkshopJob(page, 'running', 'Eir · Tablut strategist');
  await expect(
    page.getByRole('heading', { name: 'Eir · Tablut strategist', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Evaluation', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.locator('.training-overview')).toHaveCount(0);
  await expect(page.locator('.evaluation-series')).toHaveCount(1);
  // A third switch still preserves the same tab.
  await selectWorkshopJob(page, 'cancelled', 'cancelled strategist');
  await expect(
    page.getByRole('heading', { name: 'cancelled strategist', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Evaluation', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});
test('shows one merged evaluation series in the overview and in the evaluation tab', async ({
  page,
}) => {
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: workshopFixtures() }),
  );
  await open(page);
  await selectWorkshopJob(page, 'completed', 'Bjorn · Northern campaign');
  // Overview: one merged curve, not the latest series only.
  await expect(page.locator('.evaluation-series')).toHaveCount(1);
  await expect(page.locator('.series-context')).toHaveText(
    /^Merged evaluation series · \d+ series$/,
  );
  await page.getByRole('tab', { name: 'Evaluation', exact: true }).click();
  const selector = page.getByLabel('Evaluation series', { exact: true });
  // Latest series stays the default and is drawn on its own.
  await expect(selector).toHaveValue('latest');
  await expect(page.locator('.evaluation-series')).toHaveCount(1);
  await expect(page.locator('.series-context')).toHaveText(/^Champion · Series \d+/);
  await selector.selectOption('all');
  await expect(page.locator('.evaluation-series')).toHaveCount(1);
  await expect(page.locator('.evaluation-series figure')).toHaveCount(1);
  await expect(page.locator('.series-context')).toHaveText(
    /^Merged evaluation series · \d+ series$/,
  );
  // Individual series remain selectable and render separately.
  const first = await selector.locator('option').nth(2).getAttribute('value');
  await selector.selectOption(first!);
  await expect(page.locator('.series-context')).toHaveText(/^Shield bearer · Series 1/);
  await expect(page.locator('.evaluation-series')).toHaveCount(1);
});

test('workshop visual fixtures fit desktop, laptop, tablet and mobile in every analysis view', async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: workshopFixtures() }),
  );
  await open(page);
  const widths = info.project.name === 'mobile' ? [375, 430] : [1700, 1280, 768];
  for (const width of widths) {
    await page.setViewportSize({ width, height: 1000 });
    for (const view of ['Overview', 'Learning', 'Evaluation', 'Performance', 'Details']) {
      await page.getByRole('tab', { name: view, exact: true }).click();
      await expect(page.getByRole('tabpanel')).toBeVisible();
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        .toBe(true);
      if (view === 'Overview') {
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({ path: info.outputPath(`workshop-${width}.png`), fullPage: true });
      }
    }
  }
  // Switching jobs keeps the active tab, so return to Overview for the
  // per-job fixtures.
  await page.getByRole('tab', { name: 'Overview', exact: true }).click();
  for (const id of ['completed', 'legacy', 'failed', 'cancelled']) {
    const job = workshopFixtures().find((job) => job.id === id)!;
    await selectWorkshopJob(page, id, String(job.config!.model_name));
    await expect(
      page.getByRole('heading', { name: String(job.config!.model_name), exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Overview', exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await page.screenshot({
      path: info.outputPath(`${id}-${info.project.name}.png`),
      fullPage: true,
    });
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
  }
  expect(errors).toEqual([]);
});
test('empty workshop opens creation; theme and German dashboard controls remain readable', async ({
  page,
}, info) => {
  let jobs = workshopFixtures();
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: jobs }));
  await open(page);
  for (const mode of ['light', 'dark', 'wood']) {
    await page.evaluate((mode) => {
      document.documentElement.dataset.colorMode = mode;
    }, mode);
    await page.getByRole('tab', { name: 'Overview', exact: true }).click();
    await page.screenshot({ path: info.outputPath(`theme-${mode}.png`), fullPage: true });
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
  }
  await page.getByLabel('Language', { exact: true }).selectOption('de');
  await expect(page.getByRole('tab', { name: 'Übersicht', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Leistung', exact: true }).click();
  await expect(page.getByLabel('Leistungskennzahl', { exact: true })).toBeVisible();
  jobs = [];
  await page.getByRole('button', { name: 'Übersicht', exact: true }).click();
  await page.getByRole('button', { name: 'KI-Werkstatt', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Schmiede einen neuen Strategen', exact: true }),
  ).toHaveAttribute('aria-expanded', 'true');
  await page.screenshot({
    path: info.outputPath(`empty-${info.project.name}.png`),
    fullPage: true,
  });
});

test('advances live phase monitoring and reports stale publication', async ({ page }, info) => {
  // A paused clock makes the displayed seconds deterministic: they can only
  // move when this test advances them.
  const frozen = new Date('2026-01-01T00:00:00.000Z');
  await page.clock.install({ time: frozen });
  await page.clock.pauseAt(frozen);
  const job = workshopFixture('running', 1);
  const monitored = {
    ...job,
    progress: {
      ...job.progress,
      stage: 'self_play',
      duration: 100,
      updated_at: '2026-01-01T00:00:00.000Z',
      phase_started_at: '2026-01-01T00:00:00.000Z',
      phase_elapsed: 30,
      last_progress_seconds: 2,
      revision: 5,
      phase_progress: {
        games_completed: 18,
        games_total: 32,
        games_active: 14,
        positions: 4820,
      },
    },
  };
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: [monitored] }),
  );
  await open(page);
  await expect(page.getByText('Elapsed 1m 40s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 0m 30s')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Worker active' })).toBeVisible();
  const live = page.locator('.live-phase');
  await expect(live.getByText('Games', { exact: true }).locator('..')).toContainText('18 / 32');
  await expect(live.getByText('Active games', { exact: true }).locator('..')).toContainText('14');
  await expect(live.getByText('Positions', { exact: true }).locator('..')).toContainText('4,820');
  await page.screenshot({ path: info.outputPath('live-monitoring.png'), fullPage: true });

  // Timers keep advancing although no new progress snapshot arrived.
  await page.clock.runFor(3000);
  await expect(page.getByText('Elapsed 1m 43s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 0m 33s')).toBeVisible();

  // A worker that stops publishing is reported instead of looking frozen.
  await page.clock.runFor(25000);
  await expect(page.getByRole('status').filter({ hasText: 'No recent updates' })).toBeVisible();
});
