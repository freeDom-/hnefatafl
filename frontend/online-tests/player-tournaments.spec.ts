import { test, expect, type APIRequestContext } from '@playwright/test';

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
    async function login(context: APIRequestContext, identity: string) {
      const response = await context.post('/api/auth/login', {
        data: { identity, password: 'online-browser-fixture-password' },
      });
      expect(response.ok()).toBe(true);
      const session = await response.json();
      return { ...session, context };
    }
    const a = await login(creator, `SwitcherArcher${info.project.name}`);
    const b = await login(opponent, `SwitcherKeeper${info.project.name}`);
    async function post(session: typeof a, path: string, data?: unknown) {
      return session.context.post(path, { headers: { 'X-CSRF-Token': session.csrf_token }, data });
    }
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
