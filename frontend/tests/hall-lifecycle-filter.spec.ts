import { expect, test, type Page } from '@playwright/test';

const API = 'http://127.0.0.1:8001/api';
const origin = 'http://127.0.0.1:8001';

async function loginOrdinary(page: Page) {
  await page.goto('/');
  await page.getByLabel('Username or email').fill('BrowserUser');
  await page.getByLabel('Password', { exact: true }).fill('browser-fixture-only-password');
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
}

/**
 * Lifecycle filtering is an ordinary-account capability, not an administrator
 * one: private single-player and local 1v1 records report in_progress, while
 * online records report only the online lifecycle.
 */
test('ordinary accounts filter in-progress private records and drop hidden lifecycles', async ({
  page,
}) => {
  await loginOrdinary(page);

  const session = await (await page.request.get(`${API}/auth/me`)).json();
  const headers = { Origin: origin, 'X-CSRF-Token': session.csrf_token as string };
  const single = await page.request.post(`${API}/games`, {
    headers,
    data: {
      ruleset: 'brandubh',
      mode: 'human-ai',
      human_side: 'attackers',
      players: { defenders: { profile_id: 'baseline' } },
    },
  });
  expect(single.status()).toBe(201);
  const singleGame = await single.json();
  const local = await page.request.post(`${API}/games/local`, {
    headers,
    data: { ruleset: 'brandubh', attackers_label: 'Hall Asta', defenders_label: 'Hall Brynja' },
  });
  expect(local.status()).toBe(201);
  const localGame = await local.json();

  await page.goto('/archive');
  const lifecycle = page.getByRole('combobox', { name: 'Lifecycle' });
  const inProgress = lifecycle.locator('option[value="in_progress"]');
  // All records spans the online and private lifecycles, so in_progress is offered.
  await expect(inProgress).toHaveCount(1);

  await page.getByRole('tab', { name: 'Singleplayer' }).click();
  await expect(page).toHaveURL(/category=account-single-player/);
  await expect(inProgress).toHaveCount(1);
  await expect(lifecycle.locator('option[value="active"]')).toHaveCount(0);
  const singleRequest = page.waitForRequest((entry) => {
    const url = new URL(entry.url());
    return (
      url.pathname.endsWith('/api/history/games') &&
      url.searchParams.get('category') === 'account-single-player' &&
      url.searchParams.get('status') === 'in_progress' &&
      !url.searchParams.has('cursor')
    );
  });
  await lifecycle.selectOption('in_progress');
  await singleRequest;
  await expect(page).toHaveURL(/status=in_progress/);
  await expect(page.locator(`a[href*="selected=${singleGame.id}"]`)).toBeVisible();

  // Local 1v1 also reports in_progress, so the private filter survives the switch.
  const localRequest = page.waitForRequest((entry) => {
    const url = new URL(entry.url());
    return (
      url.pathname.endsWith('/api/history/games') &&
      url.searchParams.get('category') === 'local' &&
      url.searchParams.get('status') === 'in_progress'
    );
  });
  await page.getByRole('tab', { name: 'Local 1v1' }).click();
  await localRequest;
  await expect(page).toHaveURL(/category=local/);
  await expect(page).toHaveURL(/status=in_progress/);
  await expect(page.locator(`a[href*="selected=${localGame.id}"]`)).toBeVisible();

  // Online records never report in_progress, so the now-hidden filter is dropped
  // instead of silently constraining the online request.
  const onlineRequest = page.waitForRequest((entry) => {
    const url = new URL(entry.url());
    return (
      url.pathname.endsWith('/api/history/games') && url.searchParams.get('category') === 'online'
    );
  });
  await page.getByRole('tab', { name: 'Online multiplayer' }).click();
  const requested = await onlineRequest;
  const onlineUrl = new URL(requested.url());
  expect(onlineUrl.searchParams.get('status')).toBeNull();
  expect(onlineUrl.searchParams.get('cursor')).toBeNull();
  await expect(page).toHaveURL(/category=online/);
  expect(new URL(page.url()).searchParams.get('status')).toBeNull();
  await expect(inProgress).toHaveCount(0);
  // The private records stay out of the online projection.
  await expect(page.locator(`a[href*="selected=${singleGame.id}"]`)).toHaveCount(0);
  await expect(page.locator(`a[href*="selected=${localGame.id}"]`)).toHaveCount(0);

  for (const game of [singleGame, localGame]) {
    const saved = await (await page.request.get(`${API}/games/${game.id}`)).json();
    const removed = await page.request.delete(`${API}/games/${game.id}?version=${saved.version}`, {
      headers,
    });
    expect(removed.ok()).toBe(true);
  }
});

/**
 * A direct or bookmarked URL can hide an incompatible lifecycle while the
 * backend still applies it; the page must canonicalize it in place, never send
 * it, and never leave it reachable through Browser Back.
 */
test('normalizes a bookmarked lifecycle the scope cannot return', async ({ page }) => {
  await loginOrdinary(page);
  const firstRead = page.waitForRequest((entry) =>
    new URL(entry.url()).pathname.endsWith('/api/history/games'),
  );
  await page.goto('/archive?category=online&status=in_progress');
  const requested = new URL((await firstRead).url());
  expect(requested.searchParams.get('category')).toBe('online');
  expect(requested.searchParams.get('status')).toBeNull();
  expect(requested.searchParams.get('cursor')).toBeNull();

  await expect(page).toHaveURL('/archive?category=online');
  const lifecycle = page.getByRole('combobox', { name: 'Lifecycle' });
  await expect(lifecycle.locator('option[value="in_progress"]')).toHaveCount(0);
  await expect(lifecycle).toHaveValue('');

  // Reload keeps the canonical URL and never re-sends the hidden lifecycle.
  const reloadRead = page.waitForRequest((entry) =>
    new URL(entry.url()).pathname.endsWith('/api/history/games'),
  );
  await page.reload();
  expect(new URL((await reloadRead).url()).searchParams.get('status')).toBeNull();
  await expect(page).toHaveURL('/archive?category=online');

  // The invalid entry was replaced, so Back lands on the canonical URL.
  await page.getByRole('tab', { name: 'Local 1v1' }).click();
  await expect(page).toHaveURL(/category=local/);
  await page.goBack();
  await expect(page).toHaveURL('/archive?category=online');
  await expect(page).not.toHaveURL(/status=/);
  await page.goForward();
  await expect(page).toHaveURL(/category=local/);
});

test('preserves a compatible bookmarked lifecycle', async ({ page }) => {
  await loginOrdinary(page);
  const firstRead = page.waitForRequest((entry) =>
    new URL(entry.url()).pathname.endsWith('/api/history/games'),
  );
  await page.goto('/archive?category=local&status=in_progress');
  const requested = new URL((await firstRead).url());
  expect(requested.searchParams.get('category')).toBe('local');
  expect(requested.searchParams.get('status')).toBe('in_progress');
  await expect(page).toHaveURL('/archive?category=local&status=in_progress');
  await expect(page.getByRole('combobox', { name: 'Lifecycle' })).toHaveValue('in_progress');
});
