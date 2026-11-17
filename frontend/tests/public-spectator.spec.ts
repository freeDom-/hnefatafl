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
/** Valid square board of a given size: a king, four defenders and edge attackers. */
function squareBoard(size: number) {
  const board = Array.from({ length: size * size }, () => '.');
  const center = Math.floor((size * size) / 2);
  board[center] = 'K';
  board[center - 1] = 'D';
  board[center + 1] = 'D';
  board[center - size] = 'D';
  board[center + size] = 'D';
  for (const index of [0, size - 1, size * size - size, size * size - 1]) board[index] = 'A';
  return board;
}
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

test('match cards name both sides, mark the winner and separate statuses', async ({ page }) => {
  await page.route('**/api/public/online/games**', (route) =>
    route.fulfill({
      json: {
        items: [
          live,
          { ...live, id: 'waiting-game', status: 'waiting' },
          { ...record.game, id: 'done-attackers' },
          {
            ...record.game,
            id: 'done-defenders',
            result: { ...record.game.result, winner: 'defenders' },
          },
        ],
        next_cursor: null,
        limit: 12,
      },
    }),
  );
  await page.goto('/watch');
  await expect(page.getByText('Attackers', { exact: true })).toHaveCount(4);
  await expect(page.getByText('Defenders', { exact: true })).toHaveCount(4);
  await expect(page.locator('.public-match.match-active')).toHaveCount(1);
  await expect(page.locator('.public-match.match-waiting')).toHaveCount(1);
  await expect(page.locator('.public-match.match-completed')).toHaveCount(2);
  await expect(page.locator('.public-side.is-winner')).toHaveCount(2);
  await expect(page.getByText('Winner', { exact: true })).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Watch replay', exact: true })).toHaveCount(2);
  await expect(
    page.locator('.public-match.match-completed .public-match-action.primary'),
  ).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Watch game', exact: true })).toHaveCount(2);
});

test('match cards adapt columns and widths to the viewport without overflow', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'Explicit geometry matrix runs once.');
  let items = [live];
  await page.route('**/api/public/online/games**', (route) =>
    route.fulfill({ json: { items, next_cursor: null, limit: 12 } }),
  );
  for (const width of [320, 768, 1366, 3840]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/watch');
    await expect(page.getByText('Archer', { exact: true }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    const card = await page.locator('.public-match').first().boundingBox();
    expect(card!.width).toBeLessThanOrEqual(545);
    if (width >= 768) {
      // A lone card grows past a third of the row instead of leaving dead space.
      const grid = await page.locator('.public-games').boundingBox();
      expect(card!.width).toBeGreaterThan(grid!.width / 3 + 20);
    }
  }
  // Several cards use more than one column once there is room.
  items = Array.from({ length: 5 }, (_, index) => ({ ...live, id: `many-${index}` }));
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('/watch');
  await expect(page.locator('.public-match')).toHaveCount(5);
  const columns = await page
    .locator('.public-match')
    .evaluateAll(
      (nodes) => new Set(nodes.map((node) => Math.round(node.getBoundingClientRect().x))).size,
    );
  expect(columns).toBeGreaterThan(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('spectator views keep a square, fully visible board and accessible replay controls', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'Explicit geometry matrix runs once.');
  const active = { ...live, position: { ...position, board: squareBoard(9) } };
  const waiting = {
    ...live,
    id: 'waiting-game',
    status: 'waiting',
    deadline: null,
    position: { ...position, board: squareBoard(7) },
  };
  const cancelled = { ...live, id: 'cancelled-game', status: 'cancelled', deadline: null };
  const replay = { ...record, states: [position, { ...position, ply: 1, turn: 'defenders' }] };
  await page.route('**/api/public/online/games**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/replay')) return route.fulfill({ json: replay });
    if (path.includes('waiting-game')) return route.fulfill({ json: waiting });
    if (path.includes('cancelled-game')) return route.fulfill({ json: cancelled });
    return route.fulfill({ json: active });
  });
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await page.goto('/watch/games/public-game');
    await expect(page.getByRole('grid')).toBeVisible();
    const board = (await page.locator('.board').boundingBox())!;
    expect(Math.abs(board.width - board.height)).toBeLessThanOrEqual(1.5);
    expect(board.x).toBeGreaterThanOrEqual(-1);
    expect(board.x + board.width).toBeLessThanOrEqual(width + 1);
    expect(board.width).toBeGreaterThanOrEqual(180);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await expect(page.getByText('Read-only spectator', { exact: true })).toBeVisible();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/watch/games/public-game/replay');
  await expect(page.getByText('Move 0 of 1', { exact: true })).toBeVisible();
  await page.getByRole('group', { name: 'Replay controls' }).focus();
  await page.keyboard.press('End');
  await expect(page.getByText('Move 1 of 1', { exact: true })).toBeVisible();
  await page.keyboard.press('Home');
  await expect(page.getByText('Move 0 of 1', { exact: true })).toBeVisible();
  await page.goto('/watch/games/cancelled-game');
  await expect(page.getByText('Waiting game cancelled.', { exact: true })).toBeVisible();
  expect(await page.locator('.public-replay-controls').count()).toBe(0);
});

test('spectator primary actions meet the mobile touch target without inflating desktop', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'Explicit geometry matrix runs once.');
  await page.route('**/api/public/online/games**', (route) =>
    route.fulfill({
      json: { items: [live, record.game], next_cursor: null, limit: 12 },
    }),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/watch');
  for (const name of ['Watch game', 'Watch replay', 'Play']) {
    const box = (await page.getByRole('link', { name, exact: true }).boundingBox())!;
    expect(box.height, `${name} mobile height`).toBeGreaterThanOrEqual(44);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/watch');
  const desktop = (await page
    .getByRole('link', { name: 'Watch game', exact: true })
    .boundingBox())!;
  expect(desktop.height).toBeLessThanOrEqual(44);
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
