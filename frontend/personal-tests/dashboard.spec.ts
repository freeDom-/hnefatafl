import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
const password = 'personal-browser-fixture-password';
async function login(page: Page, name = 'PersonalArcher', path = '/dashboard') {
  await page.goto(path);
  await credentials(page, name);
}
async function credentials(page: Page, name: string) {
  await page.getByLabel('Username or email').fill(name);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your games' })).toBeVisible();
}
const accountPanel = (page: Page) =>
  page.locator('section[aria-labelledby="personal-account-heading"]');
const onlinePanel = (page: Page) =>
  page.locator('section[aria-labelledby="personal-online-heading"]');
async function snapshot(request: APIRequestContext) {
  return (await request.get('http://127.0.0.1:8010/snapshot')).json();
}

test('real records load with retry, bounded filters/pages and a correct turn link on desktop/mobile', async ({
  page,
  request,
}) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let first = true;
  await page.route('**/api/personal/online?**', async (route) => {
    if (!first) return route.continue();
    first = false;
    const response = await route.fetch();
    await held;
    await route.fulfill({
      response,
      status: 503,
      json: { detail: 'Isolated simulated read outage' },
    });
  });
  await login(page);
  await expect(onlinePanel(page).getByText('Loading your games…')).toBeVisible();
  release();
  await expect(onlinePanel(page).getByRole('button', { name: 'Retry' })).toBeVisible();
  await onlinePanel(page).getByRole('button', { name: 'Retry' }).click();
  await expect(onlinePanel(page).getByRole('link', { name: 'Open online game' })).toHaveCount(10);
  const firstPage = await onlinePanel(page)
    .getByRole('link', { name: 'Open online game' })
    .evaluateAll((links) => links.map((a) => a.getAttribute('href')));
  await onlinePanel(page).getByRole('button', { name: 'Next page' }).click();
  await expect(onlinePanel(page).getByRole('link', { name: 'Open online game' })).toHaveCount(3);
  const secondPage = await onlinePanel(page)
    .getByRole('link', { name: 'Open online game' })
    .evaluateAll((links) => links.map((a) => a.getAttribute('href')));
  expect(secondPage.every((href) => !firstPage.includes(href))).toBe(true);
  // Tablut opens with defenders, so Archer's first-move card belongs in Other active.
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Online section', exact: true })
    .selectOption('other_active');
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Ruleset', exact: true })
    .selectOption('tablut');
  await expect(onlinePanel(page).getByRole('link', { name: 'Open online game' })).toHaveCount(1);
  expect(new URL(page.url()).searchParams.has('oc')).toBe(false);
  await expect(accountPanel(page).getByRole('link', { name: 'Review account game' })).toHaveCount(
    10,
  );
  await accountPanel(page).getByRole('button', { name: 'Next page' }).click();
  await expect(accountPanel(page).getByRole('link', { name: 'Review account game' })).toHaveCount(
    3,
  );
  await accountPanel(page)
    .getByRole('combobox', { name: 'Ruleset', exact: true })
    .selectOption('tablut');
  await expect(accountPanel(page).getByRole('link', { name: 'Review account game' })).toHaveCount(
    1,
  );
  await page.screenshot({ path: test.info().outputPath('personal-dashboard.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const state = await snapshot(request);
  await onlinePanel(page).getByRole('link', { name: 'Open online game' }).click();
  await expect(page).toHaveURL(new RegExp(`/online/games/${state.ids.tablut}$`));
  await expect(page.getByRole('heading', { name: 'Online game', exact: true })).toBeVisible();
});

test('account and online review stay read-only and show effective cancelled/void results', async ({
  page,
  request,
}) => {
  const before = await snapshot(request);
  const paths: string[] = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname.startsWith('/api/'))
      paths.push(r.method() + ' ' + new URL(r.url()).pathname);
  });
  await login(page);
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Online section', exact: true })
    .selectOption('history');
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Lifecycle', exact: true })
    .selectOption('completed');
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Your online outcome', exact: true })
    .selectOption('win');
  await onlinePanel(page).getByRole('link', { name: 'Review online game' }).click();
  await expect(page).toHaveURL(new RegExp(`/online/games/${before.ids.completed}/review$`));
  await expect(page.getByText('Historical replay · read only')).toBeVisible();
  await expect(page.getByText('Attackers win · concession')).toBeVisible();
  await page.getByRole('button', { name: 'Next move', exact: true }).click();
  await expect(page.getByText('Move 1 of 1')).toBeVisible();
  await page.getByRole('button', { name: 'Previous move', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Concede', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Back to dashboard' }).click();
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Online section', exact: true })
    .selectOption('history');
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Lifecycle', exact: true })
    .selectOption('voided');
  await expect(onlinePanel(page).getByRole('link', { name: 'Review online game' })).toHaveCount(1);
  await onlinePanel(page).getByRole('link', { name: 'Review online game' }).click();
  await expect(page.getByText('Lifecycle: Voided')).toBeVisible();
  await expect(page.getByText('No winner')).toBeVisible();
  await expect(page.getByText(/win ·/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Back to dashboard' }).click();
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Online section', exact: true })
    .selectOption('history');
  await onlinePanel(page)
    .getByRole('combobox', { name: 'Lifecycle', exact: true })
    .selectOption('cancelled');
  await expect(onlinePanel(page).getByRole('link', { name: 'Review online game' })).toHaveCount(1);
  await onlinePanel(page).getByRole('link', { name: 'Review online game' }).click();
  await expect(page.getByText('Lifecycle: Cancelled')).toBeVisible();
  await page.getByRole('button', { name: 'Back to dashboard' }).click();
  await accountPanel(page)
    .getByRole('combobox', { name: 'Recorded result', exact: true })
    .selectOption('draw');
  await expect(accountPanel(page).getByRole('link', { name: 'Review account game' })).toHaveCount(
    1,
  );
  await accountPanel(page).getByRole('link', { name: 'Review account game' }).click();
  await expect(page).toHaveURL(new RegExp(`/dashboard/single-player/${before.ids.account}$`));
  await expect(page.getByText('Recorded result: Draw')).toBeVisible();
  await page.getByRole('button', { name: 'Next move', exact: true }).click();
  await page.getByRole('button', { name: 'Previous move', exact: true }).click();
  expect((await snapshot(request)).fingerprint).toBe(before.fingerprint);
  expect(
    paths.filter((path) => path.startsWith('POST ') && !path.startsWith('POST /api/auth/')),
  ).toEqual([]);
  expect(
    paths.filter((path) =>
      /\/api\/games\/online-|\/api\/.*(?:analysis|analytics|seek|undo|export|hint)/.test(path),
    ),
  ).toEqual([]);
});

test('two accounts and administrator retain strict personal scope; existing spectator reads and active analysis denial remain', async ({
  page,
  context,
  request,
}) => {
  const state = await snapshot(request);
  for (const name of ['PersonalObserver', 'PersonalOdin', 'PersonalKeeper']) {
    await login(page, name);
    await expect(
      accountPanel(page).getByText('No account games match these filters.'),
    ).toBeVisible();
    if (name !== 'PersonalKeeper')
      await expect(
        onlinePanel(page).getByText('No online games match these filters.'),
      ).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    const me = await (await context.request.get('/api/auth/me')).json();
    for (const path of [
      `/api/personal/single-player/${state.ids.account}/replay`,
      ...(name === 'PersonalKeeper'
        ? []
        : [
            `/api/personal/online/${state.ids.active}`,
            `/api/personal/online/${state.ids.active}/replay`,
          ]),
    ]) {
      expect((await context.request.get(path)).status()).toBe(404);
    }
    expect((await context.request.get(`/api/online/games/${state.ids.active}`)).status()).toBe(200);
    expect(
      (
        await context.request.post(`/api/games/${state.ids.active}/analysis`, {
          headers: { Origin: 'http://127.0.0.1:8009', 'X-CSRF-Token': me.csrf_token },
          data: { version: 1, cursor: 0 },
        })
      ).status(),
    ).toBe(409);
    await page.getByRole('button', { name: 'Log out', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Your games' })).toHaveCount(0);
  }
  expect((await snapshot(request)).fingerprint).toBe(state.fingerprint);
});

test('late real response after logout and account switch never populates the next dashboard', async ({
  page,
}) => {
  await login(page);
  await expect(accountPanel(page).getByRole('link', { name: 'Review account game' })).toHaveCount(
    10,
  );
  let release!: () => void, received!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const arrived = new Promise<void>((resolve) => {
    received = resolve;
  });
  let armed = true;
  await page.route('**/api/personal/single-player?**', async (route) => {
    if (!armed) return route.continue();
    armed = false;
    const response = await route.fetch();
    expect((await response.json()).games.length).toBe(10);
    received();
    await held;
    await route.fulfill({ response }).catch(() => {});
  });
  await accountPanel(page).getByRole('button', { name: 'Refresh', exact: true }).click();
  await arrived;
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await credentials(page, 'PersonalKeeper');
  await expect(accountPanel(page).getByText('No account games match these filters.')).toBeVisible();
  release();
  await page.unrouteAll({ behavior: 'wait' });
  await expect(accountPanel(page).getByRole('link', { name: 'Review account game' })).toHaveCount(
    0,
  );
  await expect(accountPanel(page).getByText('No account games match these filters.')).toBeVisible();
});

async function saved(page: Page) {
  return page.evaluate(
    () =>
      new Promise<{ raw: unknown; revision: unknown }>((resolve, reject) => {
        const req = indexedDB.open('hnefatafl-guest-v1', 1);
        req.onsuccess = () => {
          const db = req.result;
          const tx = db.transaction('device', 'readonly');
          const store = tx.objectStore('device');
          const game = store.get('game'),
            revision = store.get('revision');
          tx.oncomplete = () => {
            db.close();
            resolve({ raw: game.result, revision: revision.result });
          };
          tx.onerror = () => reject(tx.error);
        };
        req.onerror = () => reject(req.error);
      }),
  );
}
test('real guest slot survives dashboard, reviews and account changes without reading or uploading it', async ({
  page,
  request,
}) => {
  const before = await snapshot(request);
  await page.goto('/guest');
  await expect(page.getByText('Local engine ready.')).toBeVisible();
  await page.getByRole('button', { name: 'Start guest game', exact: true }).click();
  await page.getByRole('gridcell', { name: 'd7, attacker', exact: true }).click();
  await page.getByRole('gridcell', { name: /^b7, empty/ }).click();
  await expect(page.getByText('Local engine ready.')).toBeVisible();
  const slot = await saved(page);
  expect(JSON.parse(String(slot.raw)).moves).toEqual([{ source: 3, target: 1 }]);
  await page.evaluate(() => {
    const w = window as typeof window & { guestReads: number };
    w.guestReads = 0;
    const get = IDBObjectStore.prototype.get;
    IDBObjectStore.prototype.get = function (key) {
      if (this.name === 'device') w.guestReads++;
      return get.call(this, key);
    };
    history.pushState(null, '', '/dashboard');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  const requests: { path: string; body: string | null }[] = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname.startsWith('/api/'))
      requests.push({ path: new URL(r.url()).pathname, body: r.postData() });
  });
  await credentials(page, 'PersonalArcher');
  await expect(accountPanel(page).getByRole('link', { name: 'Review account game' })).toHaveCount(
    10,
  );
  await accountPanel(page).getByRole('link', { name: 'Review account game' }).first().click();
  await expect(page.getByRole('heading', { name: 'Account game review' })).toBeVisible();
  await page.getByRole('link', { name: 'Back to dashboard' }).click();
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await credentials(page, 'PersonalKeeper');
  await expect(accountPanel(page).getByText('No account games match these filters.')).toBeVisible();
  expect(
    await page.evaluate(() => (window as typeof window & { guestReads: number }).guestReads),
  ).toBe(0);
  expect(await saved(page)).toEqual(slot);
  expect(requests.filter((r) => r.body?.includes('"moves"') || r.body?.includes('guest'))).toEqual(
    [],
  );
  expect((await snapshot(request)).fingerprint).toBe(before.fingerprint);
  await page.getByRole('link', { name: 'Guest play on this device' }).click();
  await expect(page.getByText('Local engine ready.')).toBeVisible();
  await expect(page.getByRole('gridcell', { name: 'b7, attacker', exact: true })).toBeVisible();
});
