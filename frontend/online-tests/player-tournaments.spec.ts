import { test, expect, type APIRequestContext } from '@playwright/test';

async function login(context: APIRequestContext, identity: string) {
  const response = await context.post('/api/auth/login', {
    data: { identity, password: 'online-browser-fixture-password' },
  });
  expect(response.ok()).toBe(true);
  return { ...(await response.json()), context };
}

function post(session: Awaited<ReturnType<typeof login>>, path: string, data?: unknown) {
  return session.context.post(path, { headers: { 'X-CSRF-Token': session.csrf_token }, data });
}

for (const mode of ['sequential', 'simultaneous']) {
  test(`verified creator and browser-free scheduled ${mode} tournament share authoritative games`, async ({
    browser,
    playwright,
    request,
  }, info) => {
    await request.post('http://127.0.0.1:8006/reset');
    const creator = await playwright.request.newContext({
      baseURL: 'http://127.0.0.1:4177',
      extraHTTPHeaders: { Origin: 'http://127.0.0.1:4177' },
    });
    const opponent = await playwright.request.newContext({
      baseURL: 'http://127.0.0.1:4177',
      extraHTTPHeaders: { Origin: 'http://127.0.0.1:4177' },
    });
    const a = await login(creator, `SwitcherArcher${info.project.name}`);
    const b = await login(opponent, `SwitcherKeeper${info.project.name}`);
    const context = await browser.newContext({ storageState: await creator.storageState() });
    const page = await context.newPage();
    try {
      await page.goto('/player-tournaments');
      await page.getByRole('button', { name: 'Create player tournament' }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByLabel('Match mode')).toHaveValue('sequential');
      await expect(dialog.getByRole('option', { name: 'Single game', exact: true })).toHaveCount(0);
      await dialog.getByLabel('Tournament name').fill(`Scheduled ${mode}`);
      await dialog.getByLabel('Maximum entrants').fill('2');
      await dialog.getByLabel('Match mode').selectOption(mode);
      await dialog.getByLabel('Time control').selectOption('total');
      await dialog.getByLabel('Days', { exact: true }).fill('0');
      await dialog.getByLabel('Minutes', { exact: true }).fill('2');
      await dialog.getByLabel('Increment per move (seconds)').fill('5');
      const planned = new Date(Date.now() + 10 * 60000);
      const wall = await page.evaluate((instant) => {
        const date = new Date(instant);
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
      }, planned.toISOString());
      const picker = dialog.getByRole('group', {
        name: 'Planned start (your local time, optional)',
      });
      await picker.getByLabel('Date (YYYY-MM-DD)').fill(wall.slice(0, 10));
      await picker.getByLabel('Time (HH:MM)').fill(wall.slice(11));
      await dialog.getByRole('button', { name: 'Create player tournament', exact: true }).click();
      await expect(page).toHaveURL(/\/player-tournaments\/ptournament-[a-f0-9]{32}$/);
      const id = page.url().split('/').at(-1)!;
      const base = `/api/player-tournaments/${id}`;
      expect((await post(a, `${base}/register`)).ok()).toBe(true);
      const registered = await post(b, `${base}/register`);
      expect(registered.ok()).toBe(true);
      const cup = await registered.json();
      expect(cup.status).toBe('registration');
      expect(cup.start_state).toBe('waiting_for_start');
      expect(cup.match_mode).toBe(mode);
      expect(cup.match_legs).toBe(2);
      expect((await post(a, `${base}/start`, { version: cup.version })).status()).toBe(409);
      expect((await (await creator.get(`${base}/matches`)).json()).items).toEqual([]);
      expect((await post(b, `${base}/start`, { version: cup.version })).status()).toBe(403);
      expect((await post(a, `${base}/cancel`, { version: cup.version })).status()).toBe(403);
      expect((await creator.get(`${base}/audit`)).status()).toBe(403);
      // No open browser, client polling, or manual start request drives progression.
      await context.close();
      await request.post('http://127.0.0.1:8006/advance', { data: { seconds: 660 } });
      await expect
        .poll(async () => (await (await creator.get(base)).json()).status)
        .toBe('running');
      expect((await (await creator.get(base)).json()).progression_policy).toBe('synchronized');
      const sessions = new Map([
        [a.account.id, a],
        [b.account.id, b],
      ]);
      const games = new Set<string>();
      for (let round = 0; round < 8; round++) {
        const latest = await (await creator.get(base)).json();
        if (latest.status === 'complete') break;
        const matches = (await (await creator.get(`${base}/matches`)).json()).items;
        const match = matches.find((m: { status: string }) => m.status === 'ready');
        expect(match).toBeTruthy();
        if (!games.size) expect(match.legs.length).toBe(mode === 'simultaneous' ? 2 : 1);
        for (const leg of match.legs.filter(
          (leg: { game_status: string }) => leg.game_status === 'active',
        )) {
          const game = await (await creator.get(`/api/online/games/${leg.game_id}`)).json();
          expect(game.time_control).toBe('total');
          expect(game.increment_seconds).toBe(5);
          expect(game.rated).toBe(false);
          const mover = sessions.get(game.participants[game.position.turn].account_id)!;
          const conceded = await post(mover, `/api/online/games/${game.id}/concede`, {
            version: game.version,
          });
          expect(conceded.ok()).toBe(true);
          games.add(game.id);
        }
        const currentGames = [...games];
        await expect
          .poll(async () => {
            const cup = await (await creator.get(base)).json();
            const next = (await (await creator.get(`${base}/matches`)).json()).items;
            return (
              cup.status === 'complete' ||
              next.some((m: { legs: { game_id: string; game_status: string }[] }) =>
                m.legs.some((l) => l.game_status === 'active' && !currentGames.includes(l.game_id)),
              )
            );
          })
          .toBe(true);
      }
      const complete = await (await creator.get(base)).json();
      expect(complete.status).toBe('complete');
      expect(complete.official_winner).toBeTruthy();
      expect(games.size).toBeGreaterThanOrEqual(4);
    } finally {
      await context.close();
      await creator.dispose();
      await opponent.dispose();
    }
  });
}

test.use({ timezoneId: 'Europe/Berlin' });

test('a missed start can be rescheduled by its creator after reviewing the latest registration', async ({
  page,
  browser,
  playwright,
  request,
}, info) => {
  await request.post('http://127.0.0.1:8006/reset');
  const settings = {
    baseURL: 'http://127.0.0.1:4177',
    extraHTTPHeaders: { Origin: 'http://127.0.0.1:4177' },
  };
  const creator = await login(
    await playwright.request.newContext(settings),
    `SwitcherArcher${info.project.name}`,
  );
  const entrant = await login(
    await playwright.request.newContext(settings),
    `SwitcherKeeper${info.project.name}`,
  );
  const otherBrowser = await browser.newContext({
    baseURL: settings.baseURL,
    storageState: await entrant.context.storageState(),
  });
  await page.context().addCookies((await creator.context.storageState()).cookies);
  try {
    const created = await post(creator, '/api/player-tournaments', {
      name: 'Missed cup',
      capacity: 8,
      match_mode: 'sequential',
      scheduled_start_at: new Date(Date.now() + 120_000).toISOString(),
    });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    const base = `/api/player-tournaments/${id}`;
    expect((await post(creator, `${base}/register`)).status()).toBe(200);
    await page.goto(`/player-tournaments/${id}`);
    await expect(page.getByText('Waiting for more registrations.')).toBeVisible();
    await expect(page.getByText(/Provisional bracket based on maximum entrants/)).toBeVisible();
    await expect(page.locator('.player-bracket-scroll .player-match')).toHaveCount(14);
    await expect(page.getByRole('link', { name: 'Game 1' })).toHaveCount(0);
    expect((await (await creator.context.get(`${base}/matches`)).json()).items).toEqual([]);

    await request.post('http://127.0.0.1:8006/advance', { data: { seconds: 121 } });
    await expect(page.getByText(/Planned start missed/)).toBeVisible();
    const reviewed = await (await creator.context.get(base)).json();
    const otherPage = await otherBrowser.newPage();
    await otherPage.goto(`/player-tournaments/${id}`);
    await expect(otherPage.getByText(/Planned start missed/)).toBeVisible();
    await expect(otherPage.getByRole('button', { name: 'Reschedule tournament' })).toHaveCount(0);
    await expect(otherPage.getByRole('button', { name: 'Start tournament' })).toHaveCount(0);
    expect(
      (
        await post(entrant, `${base}/reschedule`, {
          version: reviewed.version,
          scheduled_start_at: '2035-10-28T01:30:00.000Z',
        })
      ).status(),
    ).toBe(403);

    const opener = page.getByRole('button', { name: 'Reschedule tournament' });
    await opener.click();
    const dialog = page.getByRole('dialog', { name: 'Reschedule tournament' });
    const submit = dialog.getByRole('button', { name: 'Reschedule tournament' });
    await expect(dialog.getByText(/^Current planned start is /)).toBeVisible();
    await expect(dialog.getByText(/Your timezone: Europe\/Berlin/).last()).toBeVisible();
    await submit.click();
    await expect(dialog.getByRole('alert')).toHaveText('Enter a valid date and time.');
    const date = dialog.getByLabel('Date (YYYY-MM-DD)');
    const time = dialog.getByLabel('Time (HH:MM)');
    await page.clock.setFixedTime(new Date('1999-01-01T00:00:00Z'));
    await date.fill('2000-02-01');
    const past = page.waitForResponse((response) => response.url().endsWith('/reschedule'));
    await submit.click();
    expect((await past).status()).toBe(422);
    await expect(dialog.getByRole('alert')).toHaveText('The planned start must be in the future.');
    await page.clock.setFixedTime(new Date('2036-01-01T00:00:00Z'));

    const registered = await post(entrant, `${base}/register`);
    expect(registered.status()).toBe(200);
    expect((await registered.json()).start_state).toBe('missed_start');
    const enterReplacement = async () => {
      await date.fill('2035-10-28');
      await time.fill('02:30');
      await dialog.getByLabel('This time occurs twice. Choose an occurrence.').selectOption('1');
    };
    await enterReplacement();
    const stale = page.waitForResponse((response) => response.url().endsWith('/reschedule'));
    await submit.click();
    expect((await stale).status()).toBe(409);
    await expect(dialog.getByRole('alert')).toHaveText(
      'This tournament changed. Reload before retrying.',
    );
    await dialog.getByRole('button', { name: 'Back' }).click();
    await expect(opener).toBeFocused();
    await opener.click();
    await enterReplacement();
    const success = page.waitForResponse((response) => response.url().endsWith('/reschedule'));
    await submit.click();
    const rescheduled = await success;
    expect(rescheduled.status()).toBe(200);
    expect((await rescheduled.json()).scheduled_start_at).toBe('2035-10-28T01:30:00.000000Z');
    await page.clock.setFixedTime(new Date());
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText('Waiting for more registrations.')).toBeVisible();

    await page.getByRole('button', { name: 'Start tournament', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Start tournament', exact: true })
      .click();
    await expect(page.getByText(/Provisional bracket/)).toHaveCount(0);
    await expect(page.locator('.player-bracket-scroll .player-match')).toHaveCount(2);
    await expect(page.getByRole('link', { name: 'Game 1' })).toBeVisible();
    await expect(page.getByText('Tournament round 1 · 0 finished · 1 outstanding')).toBeVisible();
  } finally {
    await otherBrowser.close();
    await creator.context.dispose();
    await entrant.context.dispose();
  }
});
