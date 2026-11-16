import { mkdirSync } from 'node:fs';
import { expect, test } from '@playwright/test';

function contrastRatio(foreground: string, background: string) {
  const luminance = (color: string) => {
    const [r, g, b] = color
      .match(/[\d.]+/g)!
      .slice(0, 3)
      .map(Number)
      .map((value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
    return r * 0.2126 + g * 0.7152 + b * 0.0722;
  };
  const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

const position = {
  ruleset: 'brandubh',
  board: Array.from({ length: 49 }, (_, i) =>
    i === 24 ? 'K' : [3, 21, 27, 45].includes(i) ? 'A' : [17, 23, 25, 31].includes(i) ? 'D' : '.',
  ),
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
};
const participant = {
  display_name: 'Archer',
  username: 'Archer',
  portrait_id: 'shieldbearer',
  start_rating: 1500,
  current_rating: 1500,
};
const live = {
  id: 'public-game',
  category: 'online',
  ruleset: 'brandubh',
  ruleset_version: '1',
  status: 'active',
  mode: 'single',
  leg: 1,
  rated: true,
  created_at: '2026-11-15T00:00:00Z',
  participants: {
    attackers: participant,
    defenders: {
      ...participant,
      display_name: 'Keeper',
      username: 'Keeper',
      portrait_id: 'shieldmaiden',
    },
  },
  result: null,
  position,
  deadline: null,
  time_per_move: 60,
  time_control: 'per_move',
  increment_seconds: 0,
  terminal_king_square: null,
  moves: null,
};
const record = {
  game: {
    ...live,
    status: 'completed',
    result: {
      winner: 'attackers',
      termination_reason: 'resignation',
      rated: true,
      winner_delta: 16,
    },
  },
  validated: true,
  states: [position, { ...position, ply: 1, turn: 'defenders' }],
  moves: [{ source: 3, target: 2 }],
  analysis: { hint: { move: { source: 24, target: 0 } } },
};

test('signed-out discovery, bounded filters and pagination initialize without any protected request', async ({
  page,
}) => {
  const protectedRequests: string[] = [];
  const protectedModules: string[] = [];
  page.on('request', (request) => {
    if (/\/src\/(?:App|Auth|Training|Tournaments)\.tsx(?:\?|$)/.test(request.url()))
      protectedModules.push(request.url());
  });
  const methods: string[] = [];
  const urls: string[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith('/api/public/online/games')) {
      protectedRequests.push(url.pathname);
      await route.fulfill({ status: 401, json: { detail: 'Session unavailable' } });
      return;
    }
    methods.push(route.request().method());
    urls.push(url.toString());
    await route.fulfill({
      json: {
        items: [live],
        next_cursor: url.searchParams.has('cursor') ? null : 'opaque-page',
        limit: 12,
      },
    });
  });
  await page.goto('/watch');
  await expect(page.getByRole('heading', { name: 'Every table tells a saga.' })).toBeVisible();
  const skip = page.getByRole('link', { name: 'Skip to content', exact: true });
  await page.keyboard.press('Tab');
  await expect(skip).toBeFocused();
  expect((await skip.boundingBox())!.x).toBeGreaterThanOrEqual(0);
  await expect(page.getByText('Archer', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Next page' }).click();
  await expect.poll(() => urls.at(-1)).toContain('cursor=opaque-page');
  await page.getByRole('combobox', { name: 'Ruleset', exact: true }).selectOption('tablut');
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('completed');
  await page.getByLabel('Public player', { exact: true }).fill('Müller');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect.poll(() => urls.at(-1)).toContain('ruleset=tablut');
  expect(urls.at(-1)).not.toContain('cursor=');
  expect(urls.at(-1)).toContain('player=M%C3%BCller');
  expect(protectedRequests).toEqual([]);
  expect(protectedModules).toEqual([]);
  expect(methods.every((method) => method === 'GET')).toBe(true);
  await expect(page.getByRole('link', { name: 'Log in', exact: true })).toHaveAttribute(
    'href',
    '/login',
  );
});

test('public live and replay boards never request analysis or mutate games; replay is keyboard navigable', async ({
  page,
}) => {
  const requests: { path: string; method: string }[] = [];
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    requests.push({ path, method: route.request().method() });
    if (path === '/api/public/online/games/public-game/replay')
      await route.fulfill({ json: record });
    else if (path === '/api/public/online/games/public-game') await route.fulfill({ json: live });
    else await route.fulfill({ status: 403, json: { detail: 'Forbidden' } });
  });
  await page.goto('/watch/games/public-game');
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByText('Read-only spectator', { exact: true })).toBeVisible();
  const square = page.getByRole('gridcell').first();
  await expect(square).toHaveAttribute('aria-disabled', 'true');
  await square.focus();
  await page.keyboard.press('Enter');
  await square.dispatchEvent('click');
  expect(await page.locator('.legal, .hint-source, .hint-target').count()).toBe(0);
  await page.goto('/watch/games/public-game/replay');
  await expect(page.getByText('Move 0 of 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Next move', exact: true }).focus();
  await page.keyboard.press('End');
  await expect(page.getByText('Move 1 of 1', { exact: true })).toBeVisible();
  await page.keyboard.press('Home');
  await expect(page.getByText('Move 0 of 1', { exact: true })).toBeVisible();
  expect(
    requests.every(
      ({ path, method }) => path.startsWith('/api/public/online/games/') && method === 'GET',
    ),
  ).toBe(true);
  expect(requests.some(({ path }) => path.includes('analysis') || path.includes('analytics'))).toBe(
    false,
  );
  expect(await page.locator('.legal, .hint-source, .hint-target').count()).toBe(0);
});

test('public-off, empty and private-id states give useful feedback without authentication redirects', async ({
  page,
}) => {
  let status = 404;
  await page.route('**/api/public/online/games**', (route) =>
    route.fulfill({
      status,
      json: status === 200 ? { items: [], next_cursor: null, limit: 12 } : {},
    }),
  );
  await page.goto('/watch');
  await expect(page.getByRole('alert')).toContainText('Public viewing is unavailable.');
  status = 200;
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('heading', { name: 'The tables are quiet.' })).toBeVisible();
  status = 404;
  await page.goto('/watch/games/private-account-game');
  await expect(page.getByRole('alert')).toContainText('Public viewing is unavailable.');
  await expect(page).toHaveURL(/\/watch\/games\/private-account-game$/);
});

test('unsupported public routes render the not-found state without protected initialization', async ({
  page,
}) => {
  const publicRequests: string[] = [];
  const protectedRequests: string[] = [];
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/api/public/online/games')) {
      publicRequests.push(path);
      await route.fulfill({ json: { items: [], next_cursor: null, limit: 12 } });
    } else {
      protectedRequests.push(path);
      await route.fulfill({ status: 401, json: { detail: 'Session unavailable' } });
    }
  });
  for (const path of ['/watch/games/bad!id', '/watch/games/valid-id/extra', '/watch/unknown']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'Public page not found.' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Back to public games' })).toHaveAttribute(
      'href',
      '/watch',
    );
  }
  expect(publicRequests).toEqual([]);
  expect(protectedRequests).toEqual([]);
  await page.goto('/watch');
  await expect(page.getByRole('heading', { name: 'Every table tells a saga.' })).toBeVisible();
  expect(publicRequests.length).toBeGreaterThan(0);
});

test('all four public themes fit phone through 4K viewports in English and German', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'Explicit phone-to-4K geometry matrix runs once.');
  const output = 'test-results/public-arena';
  mkdirSync(output, { recursive: true });
  await page.route('**/api/public/online/games**', (route) =>
    route.fulfill({
      json: {
        items: [live, { ...live, id: 'finished', status: 'completed', result: record.game.result }],
        next_cursor: null,
        limit: 12,
      },
    }),
  );
  for (const theme of ['kings-table', 'iron-ash', 'saga-hall', 'classic-table']) {
    for (const language of ['en', 'de']) {
      await page.addInitScript(
        ({ theme, language }) => {
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ version: 2, uiTheme: theme }),
          );
          localStorage.setItem('hnefatafl-language', language);
        },
        { theme, language },
      );
      for (const width of [320, 390, 768, 1024, 1366, 1920, 3840]) {
        await page.setViewportSize({
          width,
          height: width === 3840 ? 2160 : width === 1920 ? 1080 : 900,
        });
        await page.goto('/watch');
        await expect(page.getByText('Archer', { exact: true }).first()).toBeVisible();
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        ).toBe(true);
        await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
        if (width >= 1366) {
          const form = await page.locator('.public-filters').boundingBox();
          const control = await page.locator('.public-filters select').first().boundingBox();
          expect(form!.height).toBeLessThan(control!.height * 3 + 80);
        }
        const colors = await page.locator('.public-intro').evaluate((node) => ({
          background: getComputedStyle(node).backgroundColor,
          text: getComputedStyle(node.querySelector('p:last-child')!).color,
        }));
        expect(contrastRatio(colors.text, colors.background)).toBeGreaterThanOrEqual(4.5);
        await page.screenshot({
          path: `${output}/${theme}-${language}-${width}.png`,
          fullPage: true,
        });
      }
    }
  }
});

test('a failed public route chunk offers an explicit working reload retry', async ({ page }) => {
  let fail = true;
  await page.route(/(?:\/src\/PublicArena\.tsx|\/assets\/PublicArena-[^/]+\.js)/, async (route) => {
    if (fail) await route.abort('failed');
    else await route.continue();
  });
  await page.route('**/api/public/online/games**', (route) =>
    route.fulfill({ json: { items: [], next_cursor: null, limit: 12 } }),
  );
  await page.goto('/watch');
  await expect(page.getByRole('alert')).toContainText('This page could not be loaded.');
  fail = false;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Every table tells a saga.' })).toBeVisible();
});

test('real signed-out public API renders discovery independently of session initialization', async ({
  page,
  request,
}) => {
  const protectedRequests: string[] = [];
  await page.route('**/api/auth/**', async (route) => {
    protectedRequests.push(route.request().url());
    await route.fulfill({ status: 503, json: { detail: 'Session unavailable' } });
  });
  const response = await request.get('/api/public/online/games?limit=12');
  expect(response.ok()).toBe(true);
  const data = await response.json();
  expect(Array.isArray(data.items)).toBe(true);
  expect(data).toHaveProperty('next_cursor');
  await page.goto('/watch');
  await expect(page.getByRole('region', { name: 'Public games', exact: true })).toBeVisible();
  expect(protectedRequests).toEqual([]);
});
