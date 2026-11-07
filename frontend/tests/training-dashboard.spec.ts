import { expect, test, type Page } from './auth-fixture';
import { workshopFixture, workshopFixtures } from './workshop-fixtures';
import { openCreation, selectWorkshopJob } from './workshop-helpers';
async function open(
  page: Page,
  appearance: Record<string, string> = { mode: 'wood', tokens: 'wood', board: 'walnut' },
) {
  await page.addInitScript((theme) => {
    if (!localStorage.getItem('hnefatafl-language'))
      localStorage.setItem('hnefatafl-language', 'en');
    localStorage.setItem('hnefatafl-appearance', JSON.stringify(theme));
  }, appearance);
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
test('replaces run-specific graphs through the existing history loading state', async ({
  page,
}) => {
  await page.clock.install();
  let run = 'old';
  let reads = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: [
        {
          id: 'identity',
          name: 'Identity-safe job',
          kind: 'train',
          status: 'completed',
          summary: true,
          progress: { run, iteration: run === 'old' ? 10 : 2, model: { id: `model-${run}` } },
        },
      ],
    }),
  );
  await page.route('**/api/training/jobs/identity', async (route) => {
    const requestedRun = run;
    reads++;
    if (requestedRun === 'new') await gate;
    const detail = workshopFixture('identity', requestedRun === 'old' ? 10 : 2, 'completed');
    await route.fulfill({
      json: {
        ...detail,
        name: 'Identity-safe job',
        summary: false,
        progress: { ...detail.progress, run: requestedRun, model: { id: `model-${requestedRun}` } },
      },
    });
  });
  await open(page);
  await expect(page.getByText('Loading history…', { exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Learning', exact: true }).click();
  await page.getByText('View graph data', { exact: true }).click();
  const table = page.getByRole('table', { name: 'Training history data', exact: true });
  await expect(table.getByRole('row')).toHaveCount(11);
  run = 'new';
  await page.getByRole('button', { name: 'Refresh job', exact: true }).click();
  try {
    await expect(page.locator('.training-analysis-panel')).toHaveAttribute('aria-busy', 'true');
    await expect(page.locator('.training-analysis-panel').getByRole('status')).toHaveText(
      'Loading history…',
    );
    await expect(table).toHaveCount(0);
    expect(reads).toBe(2);
  } finally {
    release();
  }
  await expect(page.getByText('Loading history…', { exact: true })).toHaveCount(0);
  await page.getByText('View graph data', { exact: true }).click();
  await expect(table.getByRole('row')).toHaveCount(3);
  for (let i = 0; i < 3; i++) {
    await page.getByRole('button', { name: 'Refresh job', exact: true }).click();
    await expect(table.getByRole('row')).toHaveCount(3);
  }
  expect(reads).toBe(2);
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
  // The server reports how long ago the worker last published; a snapshot that
  // stopped arriving ten minutes ago is stale the moment the page mounts.
  let response: Record<string, unknown> = { ...monitored, publication_age_seconds: 900 };
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: [response] }),
  );
  await open(page);
  await expect(page.getByRole('status').filter({ hasText: 'No recent updates' })).toBeVisible();

  // A republished snapshot is classified fresh without a client-side grace period.
  response = {
    ...monitored,
    publication_age_seconds: 0.5,
    progress: { ...monitored.progress, updated_at: '2026-01-01T00:10:00.000Z' },
  };
  // An explicit refresh reconnects without advancing the paused clock, so the
  // elapsed timer stays exactly on the snapshot's reported duration.
  await page.getByRole('button', { name: 'Refresh job', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Worker active' })).toBeVisible();
  await expect(page.getByText('Elapsed 1m 40s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 0m 30s')).toBeVisible();
  const live = page.locator('.live-phase');
  await expect(live.getByText('Games', { exact: true }).locator('..')).toContainText('18 / 32');
  await expect(live.getByText('Active games', { exact: true }).locator('..')).toContainText('14');
  await expect(live.getByText('Positions', { exact: true }).locator('..')).toContainText('4,820');
  await page.screenshot({ path: info.outputPath('live-monitoring.png'), fullPage: true });

  // Timers keep advancing although no new progress snapshot arrived.
  await page.clock.runFor(3000);
  await expect(page.getByText('Elapsed 1m 43s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 0m 33s')).toBeVisible();

  // A worker that stops publishing is reported instead of looking frozen. The
  // server keeps answering and measures the child's own silence, so the age it
  // reports is what decides, not how long the browser has been open.
  response = {
    ...monitored,
    publication_age_seconds: 640,
    progress: { ...monitored.progress, updated_at: '2026-01-01T00:10:40.000Z' },
  };
  await page.clock.runFor(3000);
  await expect(page.getByRole('status').filter({ hasText: 'No recent updates' })).toBeVisible();
});

test('shows a slow initialization stage as a live, responsive job', async ({ page }, info) => {
  // A job that is still loading a checkpoint must not look like a dead worker.
  const frozen = new Date('2026-01-01T00:00:00.000Z');
  await page.clock.install({ time: frozen });
  await page.clock.pauseAt(frozen);
  const job = workshopFixture('running', 1);
  const initializing = {
    ...job,
    publication_age_seconds: 0.5,
    progress: {
      ...job.progress,
      stage: 'loading_checkpoint',
      duration: 12,
      updated_at: '2026-01-01T00:00:00.000Z',
      phase_started_at: '2026-01-01T00:00:00.000Z',
      phase_elapsed: 5,
      last_progress_seconds: 0,
      revision: 1,
      phase_progress: {},
    },
  };
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: [initializing] }),
  );
  await open(page);
  await expect(page.getByRole('status').filter({ hasText: 'Worker active' })).toBeVisible();
  await expect(page.getByText('loading checkpoint')).toBeVisible();
  await expect(page.getByText('Elapsed 0m 12s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 0m 5s')).toBeVisible();
  // No iteration has started, so none is timed while the checkpoint loads.
  await expect(page.getByText(/Iteration elapsed/)).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('initialization-monitoring.png'), fullPage: true });
  // The elapsed time keeps advancing while the checkpoint is still loading.
  await page.clock.runFor(4000);
  await expect(page.getByText('Elapsed 0m 16s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 0m 9s')).toBeVisible();
});

async function refuseTrainingFields(page: Page) {
  // The server refuses fields the form edits, one inside Advanced, and one it
  // does not own at all.
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({
          status: 400,
          json: {
            detail: 'Unsupported training configuration fields: channels, seed, output_dir',
            code: 'unsupported_training_field',
            fields: ['channels', 'seed', 'output_dir'],
          },
        })
      : route.fulfill({ json: [workshopFixture('running', 1)] }),
  );
}

async function rejectTrainingFields(page: Page) {
  // With a job present the workshop starts collapsed, so open it first.
  const disclosure = page.getByRole('button', { name: /Forge a new strategist/ });
  if ((await disclosure.getAttribute('aria-expanded')) !== 'true') await disclosure.click();
  // The button carries an aria-hidden arrow, so match its name loosely.
  await page.getByRole('button', { name: /Start training/ }).click();
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Network channels');
  await expect(alert).toContainText('channels');
  await expect(alert).toContainText('Random seed');
  await expect(alert).toContainText('output_dir');
  await expect(alert).toContainText('assigns each training job its own output directory');
  // A rejected Advanced field expands the section instead of hiding it.
  const channels = page.getByRole('spinbutton', { name: 'Network channels' });
  const seed = page.getByRole('spinbutton', { name: 'Random seed' });
  await expect(channels).toHaveAttribute('aria-invalid', 'true');
  await expect(seed).toHaveAttribute('aria-invalid', 'true');
  await expect(channels).toHaveAttribute('aria-describedby', (await alert.getAttribute('id'))!);
  await expect(channels).toHaveClass(/field-invalid/);
  return channels;
}

test('names and marks a rejected training field in every appearance', async ({ page }, info) => {
  const marker = (element: Element) => getComputedStyle(element).boxShadow;
  await refuseTrainingFields(page);
  await open(page);
  const channels = await rejectTrainingFields(page);
  const woodMarker = await channels.evaluate(marker);
  expect(woodMarker).not.toBe('none');
  // Keyboard focus keeps the shared focus outline on a marked control.
  await channels.focus();
  await expect(channels).toBeFocused();
  const focus = await channels.evaluate((element) => {
    const style = getComputedStyle(element);
    return { width: style.outlineWidth, style: style.outlineStyle, color: style.outlineColor };
  });
  // The workshop's own focus outline survives the error marker, and the two are
  // different colours so a focused rejected field is still recognizable.
  expect(focus.style).toBe('solid');
  expect(focus.width).toBe('2px');
  const markerColor = /rgb\([^)]+\)/.exec(woodMarker)?.[0];
  expect(markerColor).toBeTruthy();
  expect(focus.color).not.toBe(markerColor);
  await page.screenshot({ path: info.outputPath('rejected-field-wood.png'), fullPage: true });

  // Every appearance defines the shared error token the marker uses. The latest
  // init script wins, so it overrides the wood appearance registered by `open`.
  await page.addInitScript(() =>
    localStorage.setItem(
      'hnefatafl-appearance',
      JSON.stringify({ mode: 'dark', tokens: 'wood', board: 'walnut' }),
    ),
  );
  await page.goto('/dashboard');
  await expect(page.locator('html')).toHaveAttribute('data-color-mode', 'dark');
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
  const darkChannels = await rejectTrainingFields(page);
  const darkMarker = await darkChannels.evaluate(marker);
  expect(darkMarker).not.toBe('none');
  expect(darkMarker).not.toBe(woodMarker);
  await page.screenshot({ path: info.outputPath('rejected-field-dark.png'), fullPage: true });
});

test('keeps the iteration timer live and reports a monitoring outage', async ({ page }, info) => {
  const frozen = new Date('2026-01-01T00:00:00.000Z');
  await page.clock.install({ time: frozen });
  await page.clock.pauseAt(frozen);
  const job = workshopFixture('running', 1);
  const monitored = {
    ...job,
    publication_age_seconds: 0.5,
    supervisor_age_seconds: 0.5,
    progress: {
      ...job.progress,
      stage: 'preparing',
      active_iteration: 42,
      iteration_started_at: '2026-01-01T00:00:00.000Z',
      iteration_elapsed: 512,
      phase_started_at: '2026-01-01T00:00:00.000Z',
      phase_elapsed: 378,
      duration: 5000,
      updated_at: '2026-01-01T00:00:00.000Z',
      last_progress_seconds: 1,
      revision: 5,
    },
  };
  let reachable = true;
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    reachable ? route.fulfill({ json: [monitored] }) : route.abort(),
  );
  await open(page);
  await expect(page.getByText('Iteration elapsed 8m 32s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 6m 18s')).toBeVisible();
  await page.clock.runFor(3000);
  await expect(page.getByText('Iteration elapsed 8m 35s')).toBeVisible();
  await expect(page.getByText('Phase elapsed 6m 21s')).toBeVisible();
  await page.screenshot({ path: info.outputPath('iteration-timing.png'), fullPage: true });

  // The server stops answering: the browser reports its own blind spot instead
  // of blaming the remote worker for a connection it cannot observe.
  reachable = false;
  await page.clock.runFor(31000);
  await expect(
    page.getByRole('status').filter({ hasText: 'Monitoring connection unavailable' }),
  ).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Worker connection lost' })).toHaveCount(
    0,
  );
  await page.screenshot({ path: info.outputPath('monitoring-outage.png'), fullPage: true });

  // Reconnecting adopts the authoritative state again, and the phase name is
  // localized for German readers.
  reachable = true;
  await page.clock.runFor(3000);
  await expect(page.getByRole('status').filter({ hasText: 'Worker active' })).toBeVisible();
  await page.getByLabel('Language', { exact: true }).selectOption('de');
  await expect(page.getByText('Vorbereitung')).toBeVisible();
  await expect(page.getByText(/Iterationsdauer \d+m \d+s/)).toBeVisible();
});

test('pins the throughput history headings while only its rows scroll', async ({ page }) => {
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: workshopFixtures() }),
  );
  await open(page);
  await selectWorkshopJob(page, 'completed', 'Bjorn · Northern campaign');
  await page.getByRole('tab', { name: 'Performance', exact: true }).click();
  await page.getByText('Throughput history', { exact: true }).click();
  const scroll = page.locator('.training-performance .table-scroll');
  await expect(
    scroll.getByRole('table', { name: 'Throughput history', exact: true }),
  ).toBeVisible();
  // Geometry is read directly so a row scrolled out of view still reports a box.
  const geometry = () =>
    scroll.evaluate((element) => {
      const header = element.querySelector('thead th')!;
      const row = element.querySelector('tbody tr')!;
      return {
        scrollTop: element.scrollTop,
        containerTop: element.getBoundingClientRect().top,
        headerTop: header.getBoundingClientRect().top,
        rowTop: row.getBoundingClientRect().top,
        headerPosition: getComputedStyle(header).position,
        headerBackground: getComputedStyle(header).backgroundColor,
      };
    });
  const before = await geometry();
  // The heading starts at the top of the scroll area and is the pinned element.
  expect(before.headerPosition).toBe('sticky');
  expect(Math.abs(before.headerTop - before.containerTop)).toBeLessThan(2);
  await scroll.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  const after = await geometry();
  expect(after.scrollTop).toBeGreaterThan(0);
  // The heading stays pinned at the top of the container...
  expect(Math.abs(after.headerTop - after.containerTop)).toBeLessThan(2);
  // ...while the data rows move up normally.
  expect(after.rowTop).toBeLessThan(before.rowTop - 100);
  // The pinned background is opaque so rows never show through it.
  expect(after.headerBackground).not.toBe('rgba(0, 0, 0, 0)');
  expect(after.headerBackground).not.toBe('transparent');
});

test('keeps throughput history columns aligned and the sticky rule scoped', async ({ page }) => {
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: workshopFixtures() }),
  );
  await open(page);
  await selectWorkshopJob(page, 'completed', 'Bjorn · Northern campaign');
  await page.getByRole('tab', { name: 'Performance', exact: true }).click();
  await page.getByText('Throughput history', { exact: true }).click();
  const scroll = page.locator('.training-performance .table-scroll');
  await expect(
    scroll.getByRole('table', { name: 'Throughput history', exact: true }),
  ).toBeVisible();
  // The table is wider than its container, so horizontal scrolling applies.
  expect(await scroll.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await scroll.evaluate((element) => {
    element.scrollLeft = element.scrollWidth;
  });
  const alignment = await scroll.evaluate((element) => {
    const header = element.querySelectorAll('thead th')[2]!;
    const cell = element.querySelectorAll('tbody tr td')[1]!;
    return {
      headerLeft: header.getBoundingClientRect().left,
      cellLeft: cell.getBoundingClientRect().left,
    };
  });
  // Header and data columns share the same table, so they stay aligned.
  expect(Math.abs(alignment.headerLeft - alignment.cellLeft)).toBeLessThan(2);
  // The narrow-screen layout stays usable without page-level overflow.
  await page.setViewportSize({ width: 390, height: 780 });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
  await expect(
    scroll.getByRole('table', { name: 'Throughput history', exact: true }),
  ).toBeVisible();
  // The sticky rule is limited to the throughput table; learning history is not pinned.
  await page.getByRole('tab', { name: 'Learning', exact: true }).click();
  await page.getByText('View graph data', { exact: true }).click();
  const learningHeader = page.locator('.training-history .table-scroll thead th').first();
  await expect(learningHeader).toBeVisible();
  expect(await learningHeader.evaluate((element) => getComputedStyle(element).position)).not.toBe(
    'sticky',
  );
});
