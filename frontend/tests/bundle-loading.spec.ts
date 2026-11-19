import { expect, test } from './auth-fixture';
import { openArena, openDestination, openWorkshop } from './play-helpers';

const isFeature = (url: string, name: string) => {
  const path = new URL(url).pathname;
  return path === `/src/${name}.tsx` || new RegExp(`/assets/${name}-[^/]+\\.js$`).test(path);
};

test('Dashboard loads no Workshop, replay, analysis or profile feature graph before navigation', async ({
  page,
}) => {
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));
  await page.goto('/dashboard');
  await expect(page.locator('.personal-dashboard')).toBeVisible();
  for (const name of [
    'Training',
    'Tournaments',
    'RecordedReview',
    'AgentAnalysis',
    'AdvisorSelect',
    'NewGameForm',
    'StatisticsPage',
    'ProfilePage',
    'Online',
  ]) {
    expect(
      requests.some((url) => isFeature(url, name)),
      `${name} must remain deferred`,
    ).toBe(false);
  }
  await openWorkshop(page);
  await expect(page.locator('.training-workshop')).toBeVisible();
  expect(requests.some((url) => isFeature(url, 'Training'))).toBe(true);
  expect(requests.some((url) => isFeature(url, 'Tournaments'))).toBe(false);
  await page.goBack();
  await expect(page.locator('.personal-dashboard')).toBeVisible();
  await expect(page).toHaveURL(/\/dashboard$/);
});

test('first-use creation and route loading resolve while the advisor clock remains paused', async ({
  page,
}) => {
  const frozen = new Date();
  await page.clock.install({ time: frozen });
  await page.clock.pauseAt(new Date(frozen.getTime() + 1000));
  try {
    await page.goto('/dashboard?setup=single-player');
    await expect(page.getByRole('dialog', { name: 'Begin a new saga' })).toBeVisible();
    await page.getByRole('button', { name: 'Close dialog' }).click();
    await openDestination(page, 'statistics');
    await expect(page.locator('.statistics-page')).toBeVisible();
    expect(await page.evaluate(() => Date.now())).toBe(frozen.getTime() + 1000);
  } finally {
    await page.clock.resume();
  }
});

for (const language of ['en', 'de']) {
  test(`failed ${language} feature imports leave navigation usable and recover through explicit reload`, async ({
    page,
  }) => {
    const german = language === 'de';
    await page.addInitScript(
      (language) => localStorage.setItem('hnefatafl-language', language),
      language,
    );
    let blocked = true;
    let blockedRequests = 0;
    await page.route(
      (url) => isFeature(url.href, 'Training'),
      (route) => {
        if (blocked) {
          blockedRequests++;
          return route.abort();
        }
        return route.continue();
      },
    );
    await page.goto('/training');
    await expect(page.getByRole('alert')).toContainText(
      german ? 'Diese Seite konnte nicht geladen werden.' : 'This page could not be loaded.',
    );
    await expect(
      page.getByRole('button', { name: german ? 'Erneut versuchen' : 'Retry', exact: true }),
    ).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    expect(blockedRequests).toBeGreaterThan(0);
    await openArena(page);
    await expect(page.locator('.personal-dashboard')).toBeVisible();
    await page.goBack();
    await expect(page.getByRole('alert')).toContainText(
      german ? 'Diese Seite konnte nicht geladen werden.' : 'This page could not be loaded.',
    );
    await page
      .getByRole('button', { name: german ? 'Erneut versuchen' : 'Retry', exact: true })
      .click();
    await expect(page.getByRole('alert')).toContainText(
      german ? 'Diese Seite konnte nicht geladen werden.' : 'This page could not be loaded.',
    );
    blocked = false;
    await page
      .getByRole('button', { name: german ? 'Anwendung neu laden' : 'Reload application' })
      .click();
    await expect(page.locator('.training-workshop')).toBeVisible();
    await expect(page).toHaveURL(/\/training$/);
  });
}

test('an ordinary account cannot mount or request the permission-gated Workshop code', async ({
  page,
  playwright,
}) => {
  await page.context().clearCookies();
  const client = await playwright.request.newContext();
  try {
    const login = await client.post('http://127.0.0.1:8001/api/auth/login', {
      headers: { Origin: 'http://127.0.0.1:8001' },
      data: { identity: 'BrowserUser', password: 'browser-fixture-only-password' },
    });
    expect(login.ok()).toBe(true);
    await page.context().addCookies((await client.storageState()).cookies);
    const forbidden: string[] = [];
    page.on('request', (request) => {
      if (
        isFeature(request.url(), 'Training') ||
        new URL(request.url()).pathname === '/api/training/jobs'
      )
        forbidden.push(request.url());
    });
    await page.goto('/training');
    await expect(page.getByRole('alert')).toHaveText('Access denied');
    expect(forbidden).toEqual([]);
  } finally {
    await client.dispose();
  }
});
