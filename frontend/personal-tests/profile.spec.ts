import { test, expect } from '@playwright/test';
import { openProfile } from '../tests/play-helpers';

test('profile sharing controls filter real leaderboard responses for another player', async ({
  page,
  browser,
  request,
}) => {
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
  await page.goto('/dashboard');
  await page.getByLabel('Username or email').fill('PersonalArcher');
  await page.getByLabel('Password', { exact: true }).fill('personal-browser-fixture-password');
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await openProfile(page);
  const original = await (await page.request.get('/api/profile')).json();
  const session = await (await page.request.get('/api/auth/me')).json();
  const viewer = await browser.newContext({ baseURL: 'http://127.0.0.1:8009' });
  const viewerPage = await viewer.newPage();
  expect(
    (
      await viewer.request.post('/api/auth/login', {
        headers: { Origin: 'http://127.0.0.1:8009' },
        data: { identity: 'PersonalKeeper', password: 'personal-browser-fixture-password' },
      })
    ).ok(),
  ).toBe(true);
  async function publicEntry() {
    const response = await viewer.request.get('/api/leaderboard?ruleset=tablut');
    expect(response.ok()).toBe(true);
    expect(response.headers()['cache-control']).toBe('no-store');
    const board = await response.json();
    return board.entries.find(
      (entry: { account_id: string }) => entry.account_id === session.account.id,
    );
  }
  try {
    await page.getByRole('textbox', { name: 'Display name' }).fill('Þór 東京');
    await page.getByRole('textbox', { name: 'City', exact: true }).fill('Łódź');
    await page.getByRole('combobox', { name: 'Country', exact: true }).selectOption('DE');
    await page.getByRole('spinbutton', { name: 'Year of birth' }).fill('1990');
    await page.getByRole('button', { name: 'Save Profile' }).click();
    await expect(page.getByText('Profile saved.')).toBeVisible();
    let entry = await publicEntry();
    expect(entry.name).toBe('PersonalArcher');
    for (const key of ['country_code', 'city', 'age_range', 'birth_year', 'email', 'password_hash'])
      expect(entry).not.toHaveProperty(key);
    await page.getByRole('checkbox', { name: 'Share country', exact: true }).check();
    await page.getByRole('button', { name: 'Save Profile' }).click();
    await expect(page.getByText('Profile saved.')).toBeVisible();
    entry = await publicEntry();
    expect(entry.country_code).toBe('DE');
    expect(entry).not.toHaveProperty('city');
    expect(entry).not.toHaveProperty('age_range');
    expect(entry.name).toBe('PersonalArcher');
    for (const name of ['Share display name', 'Share city', 'Share age range'])
      await page.getByRole('checkbox', { name, exact: true }).check();
    await page.getByRole('button', { name: 'Save Profile' }).click();
    await expect(page.getByText('Profile saved.')).toBeVisible();
    entry = await publicEntry();
    expect(entry.name).toBe('Þór 東京');
    expect(entry.city).toBe('Łódź');
    expect(entry.age_range.max - entry.age_range.min).toBeGreaterThanOrEqual(9);
    const calendarAge = new Date().getUTCFullYear() - 1990;
    expect(entry.age_range.min).toBeLessThanOrEqual(calendarAge - 1);
    expect(entry.age_range.max).toBeGreaterThanOrEqual(calendarAge);
    expect(entry).not.toHaveProperty('birth_year');
    await viewerPage.goto('/leaderboard');
    await viewerPage.getByRole('combobox', { name: 'Ruleset' }).selectOption('tablut');
    const row = viewerPage.getByRole('row').filter({ hasText: 'Þór 東京' });
    await expect(row).toContainText('Germany');
    await expect(row).toContainText('Łódź');
    await expect(row).toContainText(`${entry.age_range.min}–${entry.age_range.max}`);
    for (const checkbox of await page.getByRole('checkbox').all()) await checkbox.uncheck();
    await page.getByRole('button', { name: 'Save Profile' }).click();
    await expect(page.getByText('Profile saved.')).toBeVisible();
    entry = await publicEntry();
    expect(entry.name).toBe('PersonalArcher');
    for (const key of ['country_code', 'city', 'age_range', 'birth_year'])
      expect(entry).not.toHaveProperty(key);
    await viewerPage.reload();
    await expect(
      viewerPage.getByRole('row').filter({ hasText: 'PersonalArcher' }),
    ).not.toContainText('Germany');
  } finally {
    const fields = [
      'display_name',
      'country_code',
      'city',
      'birth_year',
      'show_display_name',
      'show_country',
      'show_city',
      'show_age',
    ];
    expect(
      (
        await page.request.patch('/api/profile', {
          headers: { Origin: 'http://127.0.0.1:8009', 'X-CSRF-Token': session.csrf_token },
          data: Object.fromEntries(fields.map((key) => [key, original[key]])),
        })
      ).ok(),
    ).toBe(true);
    await viewer.close();
  }
});
