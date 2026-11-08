import { expect, test } from '@playwright/test';
import { openGameNavigation, openProfile } from '../tests/play-helpers';

test('profile edits update existing games, reviews and mounted identities without rewriting game history', async ({
  page,
  request,
}) => {
  test.setTimeout(180000);
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
  const login = await page.request.post('/api/auth/login', {
    headers: { Origin: 'http://127.0.0.1:8009' },
    data: { identity: 'PersonalArcher', password: 'personal-browser-fixture-password' },
  });
  expect(login.ok()).toBe(true);
  const session = await login.json();
  const headers = { Origin: 'http://127.0.0.1:8009', 'X-CSRF-Token': session.csrf_token };
  const original = await (await page.request.get('/api/profile')).json();
  let identifier: string | undefined;
  const share = async (display_name: string, populated = true) => {
    expect(
      (
        await page.request.patch('/api/profile', {
          headers,
          data: { display_name: populated ? display_name : null },
        })
      ).ok(),
    ).toBe(true);
  };
  const snapshot = async () => (await request.get('http://127.0.0.1:8010/snapshot')).json();
  try {
    await share('Thor');
    const created = await page.request.post('/api/games', {
      headers,
      data: { ruleset: 'brandubh', human_side: 'attackers' },
    });
    expect(created.status()).toBe(201);
    const game = await created.json();
    identifier = game.id;
    const before = await snapshot();
    await page.goto(`/games/${identifier}`);
    await expect(page.getByRole('heading', { name: 'Thor', exact: true })).toBeVisible();
    await expect(page.locator('.army p').filter({ hasText: '(@PersonalArcher)' })).toBeVisible();
    await openGameNavigation(page);
    await openProfile(page);
    const currentName = 'Freya Þór 東京 😀';
    await page.getByRole('textbox', { name: 'Display name', exact: true }).fill(currentName);
    await page.getByRole('button', { name: 'Save Profile', exact: true }).click();
    await expect(page.getByText('Profile saved.')).toBeVisible();
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/games/${identifier}$`));
    await expect(page.getByRole('heading', { name: currentName, exact: true })).toBeVisible();
    for (const width of [320, 375, 760, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const longName = '😀'.repeat(80);
      await share(longName);
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await expect(page.getByRole('heading', { name: longName, exact: true })).toBeVisible();
      const attribution = page.locator('.army p').filter({ hasText: '(@PersonalArcher)' });
      await expect(attribution).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        width,
      );
      await share(currentName);
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await expect(page.getByRole('heading', { name: currentName, exact: true })).toBeVisible();
    }
    await page.goto(`/archive?review=account-single-player&selected=${identifier}`);
    await expect(page.getByRole('grid')).toBeVisible();
    await expect(page.getByRole('heading', { name: currentName, exact: true })).toBeVisible();
    await expect(page.locator('.army p').filter({ hasText: '(@PersonalArcher)' })).toBeVisible();
    await share('Private withdrawn name', false);
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page.getByRole('heading', { name: 'PersonalArcher', exact: true })).toBeVisible();
    await expect(page.locator('.armies')).not.toContainText('Private withdrawn name');
    await expect(page.locator('.armies')).not.toContainText('(@PersonalArcher)');
    const fresh = await (await page.request.get(`/api/games/${identifier}`)).json();
    for (const key of [
      'version',
      'board',
      'moves',
      'cursor',
      'seed',
      'created_at',
      'recorded_winner',
    ])
      expect(fresh[key]).toEqual(game[key]);
    expect(fresh.players.attackers.account_id).toBe(session.account.id);
    expect(fresh.players.attackers.name).toBe('PersonalArcher');
    expect(await snapshot()).toEqual(before);
    await page.screenshot({
      path: test.info().outputPath('dynamic-account-identity.png'),
      fullPage: true,
    });
  } finally {
    if (identifier)
      expect(
        (await page.request.delete(`/api/games/${identifier}?version=0`, { headers })).ok(),
      ).toBe(true);
    expect(
      (
        await page.request.patch('/api/profile', {
          headers,
          data: {
            display_name: original.display_name,
          },
        })
      ).ok(),
    ).toBe(true);
  }
});
