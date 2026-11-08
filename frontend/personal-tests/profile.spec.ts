import { test, expect, type APIRequestContext } from '@playwright/test';
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

test('shared names retain canonical attribution in standings, games and history at narrow widths', async ({
  page,
  browser,
  request,
}, info) => {
  test.setTimeout(180000);
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
  const keeper = await browser.newContext({ baseURL: 'http://127.0.0.1:8009' });
  const mobile = info.project.name === 'mobile';
  const admin = await browser.newContext({
    baseURL: 'http://127.0.0.1:8009',
    viewport: { width: mobile ? 375 : 1440, height: 900 },
    isMobile: mobile,
    hasTouch: mobile,
  });
  expect(
    (
      await admin.request.post('/api/auth/login', {
        headers: { Origin: 'http://127.0.0.1:8009' },
        data: { identity: 'PersonalOdin', password: 'personal-browser-fixture-password' },
      })
    ).ok(),
  ).toBe(true);
  await admin.grantPermissions(['clipboard-read', 'clipboard-write']);
  const adminPage = await admin.newPage();
  const actors = [
    { username: 'PersonalArcher', api: page.request },
    { username: 'PersonalKeeper', api: keeper.request },
  ];
  const sessions: {
    username: string;
    api: APIRequestContext;
    csrf: string;
    original: { display_name: string | null; show_display_name: boolean };
  }[] = [];
  for (const actor of actors) {
    const response = await actor.api.post('/api/auth/login', {
      headers: { Origin: 'http://127.0.0.1:8009' },
      data: { identity: actor.username, password: 'personal-browser-fixture-password' },
    });
    expect(response.ok()).toBe(true);
    const session = await response.json();
    const original = await (await actor.api.get('/api/profile')).json();
    sessions.push({ ...actor, csrf: session.csrf_token, original });
  }
  async function share(
    actor: (typeof sessions)[number],
    display_name: string | null,
    enabled = true,
  ) {
    expect(
      (
        await actor.api.patch('/api/profile', {
          headers: { Origin: 'http://127.0.0.1:8009', 'X-CSRF-Token': actor.csrf },
          data: { display_name, show_display_name: enabled },
        })
      ).ok(),
    ).toBe(true);
  }
  try {
    for (const display of ['PersonalOdin', '😀'.repeat(80)]) {
      for (const actor of sessions) await share(actor, display);
      const standings = await (await page.request.get('/api/leaderboard?ruleset=tablut')).json();
      for (const actor of sessions) {
        const entry = standings.entries.find(
          (entry: { username: string }) => entry.username === actor.username,
        );
        expect(entry.name).toBe(display);
        for (const key of ['email', 'password_hash', 'birth_year', 'role'])
          expect(entry).not.toHaveProperty(key);
      }
      await page.goto('/leaderboard');
      await page.getByRole('combobox', { name: /^(Ruleset|Regelwerk)$/ }).selectOption('tablut');
      for (const width of [320, 375, 760, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        for (const language of ['en', 'de']) {
          await page.locator('.language-select select').selectOption(language);
          for (const actor of sessions) {
            const cell = page
              .locator('tbody tr')
              .filter({ hasText: `(@${actor.username})` })
              .locator('td')
              .nth(1);
            await expect(cell).toContainText(`${display} (@${actor.username})`);
            await expect(cell).toBeVisible();
            const bounds = await cell.boundingBox();
            expect(bounds!.x).toBeGreaterThanOrEqual(0);
            expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1);
          }
          expect(
            await page.evaluate(() => document.documentElement.scrollWidth),
          ).toBeLessThanOrEqual(width);
        }
      }
      await page.locator('.language-select select').selectOption('en');
      const listing = await (
        await page.request.get('/api/online/games?section=mine&limit=50')
      ).json();
      const game = listing.games.find((game: { status: string }) => game.status === 'active');
      expect(game).toBeTruthy();
      await page.setViewportSize({
        width: info.project.name === 'mobile' ? 375 : 1440,
        height: 900,
      });
      await page.goto(`/online/games/${game.id}`);
      await expect(page.getByRole('grid')).toBeVisible();
      for (const actor of sessions) {
        const detail = page.locator('.army p').filter({ hasText: `(@${actor.username})` });
        await expect(detail).toBeVisible();
        // Canonical attribution is first in the existing compact detail line,
        // so a long display name cannot push it behind the name's ellipsis.
        const visible = await detail.evaluate((node, token) => {
          const text = node.firstChild!;
          const start = text.textContent!.indexOf(token);
          const range = document.createRange();
          range.setStart(text, start);
          range.setEnd(text, start + token.length);
          const content = range.getBoundingClientRect();
          const bounds = node.getBoundingClientRect();
          return content.left >= bounds.left - 1 && content.right <= bounds.right + 1;
        }, `(@${actor.username})`);
        expect(visible).toBe(true);
      }
      await page.goto('/archive');
      await expect(page.getByRole('textbox', { name: 'Player name' })).toHaveCount(0);
      for (const actor of sessions)
        await expect(
          page
            .locator('.recorded-games td')
            .filter({ hasText: `${display} (@${actor.username})` })
            .first(),
        ).toBeVisible();
      await adminPage.goto('/archive');
      for (const language of ['en', 'de']) {
        await adminPage.locator('.language-select select').selectOption(language);
        const search = adminPage.getByRole('textbox', {
          name: language === 'de' ? 'Spielername' : 'Player name',
        });
        const label = `${display} (@PersonalArcher)`;
        for (const query of [display, 'PersonalArcher', '@PersonalArcher', label]) {
          const response = adminPage.waitForResponse((response) => {
            const url = new URL(response.url());
            return (
              url.pathname === '/api/history/games' && url.searchParams.get('player') === query
            );
          });
          if (query === label) {
            // Exercise actual paste beyond the former 80-UTF-16-unit limit.
            await search.fill('');
            await adminPage.bringToFront();
            await search.focus();
            await adminPage.evaluate((text) => navigator.clipboard.writeText(text), label);
            await adminPage.keyboard.press('Control+V');
          } else await search.fill(query);
          const result = await response;
          expect(result.ok()).toBe(true);
          const records = await result.json();
          expect(records.matching).toBeGreaterThan(0);
          await expect(search).toHaveValue(query);
          await expect(adminPage.locator('.recorded-games tbody tr')).toHaveCount(
            records.games.length,
          );
          await expect(adminPage.locator('.recorded-games')).toContainText(label);
        }
      }
    }
    await share(sessions[0], 'PersonalOdin', false);
    const board = await (await page.request.get('/api/leaderboard?ruleset=tablut')).json();
    expect(
      board.entries.find((entry: { username: string }) => entry.username === 'PersonalArcher').name,
    ).toBe('PersonalArcher');
    await page.goto('/leaderboard');
    await expect(page.locator('tbody')).toContainText('PersonalArcher');
    await expect(page.locator('tbody')).not.toContainText('(@PersonalArcher)');
    await expect(page.locator('tbody')).toContainText('(@PersonalKeeper)');
  } finally {
    for (const actor of sessions)
      await share(actor, actor.original.display_name, actor.original.show_display_name);
    await keeper.close();
    await admin.close();
  }
});
