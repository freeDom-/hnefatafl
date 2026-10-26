import { test, expect, type Page } from '@playwright/test';
async function saved(page: Page) {
  return page.evaluate(
    () =>
      new Promise<{ raw: string | null; revision: string | null }>((resolve, reject) => {
        const req = indexedDB.open('hnefatafl-guest-v1', 1);
        req.onsuccess = () => {
          const db = req.result;
          const tx = db.transaction('device', 'readonly');
          const s = tx.objectStore('device');
          const value = s.get('game'),
            revision = s.get('revision');
          tx.oncomplete = () => {
            db.close();
            resolve({ raw: value.result ?? null, revision: revision.result ?? null });
          };
          tx.onerror = () => reject(tx.error);
        };
        req.onerror = () => reject(req.error);
      }),
  );
}
async function ready(page: Page) {
  await expect(page.getByText('Local engine ready.')).toBeVisible();
}
async function start(page: Page) {
  await ready(page);
  await page.getByRole('button', { name: 'Start guest game', exact: true }).click();
  await expect(page.getByRole('grid')).toBeVisible();
}
async function humanMove(page: Page) {
  await page.getByRole('gridcell', { name: 'd7, attacker', exact: true }).click();
  await page.getByRole('gridcell', { name: /^b7, empty/ }).click();
  await expect(page.getByRole('gridcell', { name: 'b7, attacker', exact: true })).toBeVisible();
  await ready(page);
}

test('anonymous guest uses real Python engine, persists moves and reloads through production CSP without API calls', async ({
  page,
  request,
}) => {
  const before = await (await request.get('http://127.0.0.1:8008/counts')).json();
  const apis: string[] = [];
  const errors: string[] = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname.startsWith('/api/')) apis.push(r.url());
  });
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  const began = Date.now();
  await page.goto('/guest');
  const accountLink = page.getByRole('link', { name: 'Account single-player', exact: true });
  await expect(accountLink).toHaveClass(/header-link.*button/);
  await expect(accountLink).toHaveAttribute('href', '/dashboard?setup=single-player');
  await accountLink.focus();
  await expect(accountLink).toBeFocused();
  expect(
    await accountLink.evaluate((a) => a.getBoundingClientRect().height),
  ).toBeGreaterThanOrEqual(42);

  await ready(page);
  console.log('Guest startup ms:', Date.now() - began);
  await start(page);
  await humanMove(page);
  const slot = await saved(page);
  expect(JSON.parse(slot.raw!).moves).toEqual([{ source: 3, target: 1 }]);
  await page.getByRole('button', { name: 'Play computer move' }).click();
  await ready(page);
  await expect.poll(async () => JSON.parse((await saved(page)).raw!).moves.length).toBe(2);
  const checkpoint = await saved(page);
  await page.screenshot({ path: test.info().outputPath('guest-ready.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await ready(page);
  expect((await saved(page)).raw).toBe(checkpoint.raw);
  await page.getByRole('button', { name: 'Previous move', exact: true }).click();
  await expect(page.getByText('Guest replay · read only')).toBeVisible();
  expect(
    await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length),
  ).toBe(0);
  expect(apis).toEqual([]);
  expect(errors).toEqual([]);
  expect(await (await request.get('http://127.0.0.1:8008/counts')).json()).toEqual(before);
  const headers = await request.get('/assets/guest-manifest.json');
  const m = await headers.json();
  const worker = await request.get(m.base + 'worker.js');
  expect(worker.headers()['content-security-policy']).toContain('wasm-unsafe-eval');
  expect((await request.get('/login')).headers()['content-security-policy']).not.toContain(
    'wasm-unsafe-eval',
  );
});

async function confirmClear(page: Page) {
  await page.getByRole('button', { name: 'Clear device game', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Clear device game?' })).toBeVisible();
  await page.getByRole('button', { name: 'Confirm clear', exact: true }).click();
  await expect(page.getByRole('grid')).toHaveCount(0);
}
async function inject(page: Page, raw: unknown, revision?: unknown) {
  await page.evaluate(
    ({ raw, revision }) =>
      new Promise<void>((resolve, reject) => {
        const req = indexedDB.open('hnefatafl-guest-v1', 1);
        req.onsuccess = () => {
          const db = req.result;
          const tx = db.transaction('device', 'readwrite');
          tx.objectStore('device').put(raw, 'game');
          if (revision !== undefined) tx.objectStore('device').put(revision, 'revision');
          tx.oncomplete = () => {
            db.close();
            resolve();
          };
          tx.onabort = () => {
            db.close();
            reject(tx.error);
          };
        };
        req.onerror = () => reject(req.error);
      }),
    { raw, revision },
  );
}

test('confirmed replacement is atomic on an interrupted write; clear keeps independent preferences', async ({
  page,
}) => {
  await page.goto('/guest');
  await start(page);
  await humanMove(page);
  const before = await saved(page);
  await page.evaluate(() => {
    localStorage.setItem('hnefatafl-test-preference', 'keep');
    localStorage.setItem('hnefatafl-game', 'existing-account-selection');
  });
  await page.getByRole('button', { name: 'Start guest game', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  expect(await saved(page)).toEqual(before);
  // Simulate quota/interruption after the revision put but before the game put.
  await page.evaluate(() => {
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (value, key) {
      if (this.name === 'device' && key === 'game')
        throw new DOMException('fixture quota', 'QuotaExceededError');
      return put.call(this, value, key);
    };
  });
  await page.getByRole('button', { name: 'Start guest game', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm replacement', exact: true }).click();
  await expect(page.getByText(/This change could not be saved/)).toBeVisible();
  expect(await saved(page)).toEqual(before);
  await page.reload();
  await ready(page);
  await expect(page.getByRole('gridcell', { name: 'b7, attacker', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Start guest game', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm replacement', exact: true }).click();
  await ready(page);
  await expect.poll(async () => JSON.parse((await saved(page)).raw!).moves.length).toBe(0);
  await confirmClear(page);
  expect((await saved(page)).raw).toBeNull();
  expect(
    await page.evaluate(() => [
      localStorage.getItem('hnefatafl-test-preference'),
      localStorage.getItem('hnefatafl-game'),
    ]),
  ).toEqual(['keep', 'existing-account-selection']);
  await page.reload();
  await ready(page);
  await expect(page.getByRole('grid')).toHaveCount(0);
});

test('invalid and unsupported histories stay untouched until an explicit recovery decision', async ({
  page,
}) => {
  await page.goto('/guest');
  await start(page);
  const valid = JSON.parse((await saved(page)).raw!);
  for (const change of [
    '{bad',
    { ...valid, schema: 2 },
    { ...valid, engine_version: 'b'.repeat(64) },
    { ...valid, moves: [{ source: 0, target: 1 }], cursor: 1 },
    { ...valid, board: ['K'], winner: 'attackers' },
    { ...valid, id: 'online-' + 'a'.repeat(32) },
  ]) {
    const raw = typeof change === 'string' ? change : JSON.stringify(change);
    await inject(page, raw);
    await page.reload();
    await ready(page);
    await expect(page.getByRole('alert')).toContainText('saved game is invalid');
    await expect(page.getByRole('grid')).toHaveCount(0);
    expect((await saved(page)).raw).toBe(raw);
  }
  await confirmClear(page);
  await start(page);
  // Metadata corruption also has an explicit clear/recovery path.
  await inject(page, JSON.stringify(valid), { broken: true });
  await page.reload();
  await ready(page);
  await expect(page.getByText(/Device storage is unavailable/)).toBeVisible();
  await confirmClear(page);
  await start(page);
  expect(JSON.parse((await saved(page)).raw!).schema).toBe(1);
});

test('terminal results come from replay; an appended post-terminal move is rejected', async ({
  page,
}) => {
  await page.goto('/guest');
  await start(page);
  const record = JSON.parse((await saved(page)).raw!);
  const cycle = [
    { source: 3, target: 2 },
    { source: 17, target: 16 },
    { source: 2, target: 3 },
    { source: 16, target: 17 },
  ];
  record.moves = [...cycle, ...cycle];
  record.cursor = 8;
  await inject(page, JSON.stringify(record));
  await page.reload();
  await ready(page);
  await expect(page.getByText('Draw', { exact: true })).toBeVisible();
  await expect(page.getByText('threefold repetition')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play computer move' })).toHaveCount(0);
  record.moves.push(cycle[0]);
  record.cursor++;
  await inject(page, JSON.stringify(record));
  await page.reload();
  await ready(page);
  await expect(page.getByRole('alert')).toContainText('saved game is invalid');
});

test('unavailable IndexedDB permits memory-only play with a visible warning', async ({ page }) => {
  await page.addInitScript(() =>
    Object.defineProperty(window, 'indexedDB', { get: () => undefined }),
  );
  await page.goto('/guest');
  await ready(page);
  await expect(page.getByText(/Device storage is unavailable/)).toBeVisible();
  await page.getByRole('button', { name: 'Start guest game', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm replacement', exact: true }).click();
  await expect(page.getByRole('grid')).toBeVisible();
  await humanMove(page);
  await expect(page.getByText(/Device storage is unavailable/)).toBeVisible();
});

test('a stale tab cannot resurrect a cleared slot', async ({ page, context }) => {
  await page.goto('/guest');
  await start(page);
  const other = await context.newPage();
  await other.goto('/guest');
  await ready(other);
  await confirmClear(page);
  await humanMove(other);
  await expect(
    other.getByText('Another tab changed the device game. Reload it before continuing.'),
  ).toBeVisible();
  expect((await saved(page)).raw).toBeNull();
  await other.getByRole('button', { name: 'Reload saved device game', exact: true }).click();
  await other.getByRole('button', { name: 'Confirm reload', exact: true }).click();
  await ready(other);
  await expect(other.getByRole('grid')).toHaveCount(0);
  await other.close();
});

async function login(page: Page, name: string) {
  await page.getByLabel('Username or email').fill(name);
  await page.getByLabel('Password', { exact: true }).fill('guest-browser-fixture-password');
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  const cancel = page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true });
  if (await cancel.isVisible()) await cancel.click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
}
test('guest survives login, logout, account switching, back navigation and online routes without upload', async ({
  page,
  request,
}) => {
  const before = await (await request.get('http://127.0.0.1:8008/counts')).json();
  await page.goto('/guest');
  await start(page);
  await humanMove(page);
  const checkpoint = await saved(page);
  await page.getByRole('link', { name: 'Account single-player', exact: true }).click();
  await login(page, 'GuestArcher');
  const owned = await page.evaluate(async () => {
    const session = await (await fetch('/api/auth/me')).json();
    const created = await (
      await fetch('/api/games', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token },
        body: JSON.stringify({ ruleset: 'brandubh', mode: 'human-human' }),
      })
    ).json();
    localStorage.setItem('hnefatafl-game', created.id);
    return created;
  });
  await page.getByRole('link', { name: 'Guest play', exact: true }).click();
  await ready(page);
  expect(await saved(page)).toEqual(checkpoint);
  expect(await page.evaluate(() => localStorage.getItem('hnefatafl-game'))).toBe(owned.id);
  await page.goBack();
  await page.goto('/games/' + owned.id);
  await expect(page.getByRole('grid')).toBeVisible();
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Log in', exact: true })).toBeVisible();
  await login(page, 'GuestKeeper');
  expect(await page.evaluate(async () => (await (await fetch('/api/games')).json()).length)).toBe(
    0,
  );
  expect(
    await page.evaluate(async (id) => (await fetch('/api/games/' + id)).status, owned.id),
  ).toBe(404);
  expect(await saved(page)).toEqual(checkpoint);
  const guestRequests: string[] = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname.includes('/assets/guest-')) guestRequests.push(r.url());
  });
  await page.goto('/online');
  await expect(page.getByRole('heading', { name: 'Online lobby' })).toBeVisible();
  await page.goto('/online/games/online-' + 'a'.repeat(32));
  await expect(page.getByText(/Online game not found/)).toBeVisible();
  expect(guestRequests).toEqual([]);
  expect(await saved(page)).toEqual(checkpoint);
  await page.getByRole('button', { name: 'Log out', exact: true }).click();
  await login(page, 'GuestArcher');
  expect(
    await page.evaluate(async (id) => (await fetch('/api/games/' + id)).status, owned.id),
  ).toBe(200);
  expect(await (await request.get('http://127.0.0.1:8008/counts')).json()).toEqual({
    ...before,
    games: before.games + 1,
  });
});
