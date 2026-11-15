import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from './auth-fixture';
import { historicalGame } from './historical-fixture';
import {
  activeOnlineGame,
  activeOnlineHistory,
  emptyOnlineHistory,
  seedEmptyGameLists,
  seedOnlineHistory,
  seedRecentRecords,
  seedReferenceStandings,
} from './arena-fixture';
import { openDestination, openProfile } from './play-helpers';

const output = 'test-results/arena-final';
const themes = ['kings-table', 'iron-ash', 'saga-hall', 'classic-table'];
const targets = [
  { width: 1920, height: 1080, density: 'default' },
  { width: 3840, height: 2160, density: 'large' },
];
async function settled(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())));
  });
}
async function geometry(page: Page, dashboard = false) {
  await settled(page);
  const m = await page.evaluate(() => {
    const box = (s: string) => document.querySelector(s)?.getBoundingClientRect().toJSON();
    return {
      width: innerWidth,
      height: innerHeight,
      scroll: document.documentElement.scrollWidth,
      rail: box('.arena-sidebar'),
      header: box('.arena-topbar'),
      main: box('main'),
      board: box('.board'),
      hero: box('.arena-hero'),
      aside: box('.dashboard-aside'),
      queue: box('.dashboard-needs-you'),
      footer: box('.site-footer'),
    };
  });
  expect(m.scroll).toBeLessThanOrEqual(m.width);
  expect(m.rail?.left).toBe(0);
  expect(m.header?.left).toBeGreaterThanOrEqual(m.rail!.right - 1);
  expect(m.main?.left).toBeGreaterThanOrEqual(m.rail!.right - 1);
  await expect(page.locator('.site-header')).toHaveCount(0);
  if (m.board) {
    expect(Math.abs(m.board.width - m.board.height)).toBeLessThan(1);
    expect(m.board.width).toBeGreaterThan(300);
    expect(m.board.right).toBeLessThanOrEqual(m.width);
  }
  if (dashboard) {
    expect(m.hero!.right).toBeLessThanOrEqual(m.aside!.left);
    expect(m.hero!.bottom).toBeLessThanOrEqual(m.queue!.top);
    expect(m.footer!.bottom).toBeLessThanOrEqual(m.height + 1);
  }
}
async function capture(page: Page, name: string) {
  await settled(page);
  await page.screenshot({ path: `${output}/${name}.jpg`, type: 'jpeg', quality: 85 });
}
test.beforeAll(() => mkdirSync(output, { recursive: true }));
for (const theme of themes)
  for (const target of targets) {
    test(`${theme} ${target.width}: complete visual acceptance and shared gameplay shell`, async ({
      page,
      request,
      adminSession,
    }, info) => {
      test.skip(
        info.project.name !== 'chromium',
        'The matrix explicitly sets desktop viewports; responsive mobile has separate coverage.',
      );
      test.setTimeout(180000);
      const name = `${theme}-${target.width}`;
      await page.setViewportSize({ width: target.width, height: target.height });
      await page.addInitScript(
        ({ theme, density }) =>
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ uiTheme: theme, density, board: 'oak', tokens: 'classic' }),
          ),
        { theme, density: target.density },
      );
      await seedEmptyGameLists(page);
      await seedOnlineHistory(page, emptyOnlineHistory());
      await seedRecentRecords(page);
      await seedReferenceStandings(page);
      await page.goto('/dashboard');
      await expect(page.locator('.arena-hero-empty')).toBeVisible();
      await geometry(page, true);
      await capture(page, `${name}-dashboard-empty`);
      await page.locator('.arena-sidebar [data-destination="play"]').click();
      await expect(page).toHaveURL(/\/play$/);
      await expect(page.getByRole('heading', { name: 'Play', exact: true })).toBeVisible();
      await geometry(page);
      await capture(page, `${name}-play`);
      await seedOnlineHistory(page, activeOnlineHistory());
      await page.goto('/dashboard');
      await expect(page.locator('.arena-hero-board .board')).toBeVisible();
      await expect(page.locator('.arena-kicker')).toContainText('YOUR TURN');
      await expect(page.locator('.dashboard-game-list > li')).toHaveCount(3);
      await geometry(page, true);
      await capture(page, `${name}-dashboard`);
      const single = await historicalGame(request, {
        data: { ruleset: 'brandubh', mode: 'human-ai', human_side: 'attackers' },
      });
      expect(single.ok()).toBe(true);
      const game = await single.json();
      await page.goto(`/games/${game.id}`);
      await expect(page.getByRole('grid')).toBeVisible();
      await geometry(page);
      await capture(page, `${name}-single-player`);
      // Browser Back/Forward retains the same navigation chrome and account game.
      await openDestination(page, 'dashboard');
      await page.goBack();
      await expect(page).toHaveURL(new RegExp(`/games/${game.id}$`));
      await geometry(page);
      const local = await request.post('http://127.0.0.1:8001/api/games/local', {
        data: { ruleset: 'brandubh', attackers_label: 'Archer', defenders_label: 'Keeper' },
      });
      expect(local.ok()).toBe(true);
      const localGame = await local.json();
      try {
        await page.goto(`/games/${localGame.id}`);
        await expect(page.getByRole('grid')).toBeVisible();
        await geometry(page);
        await capture(page, `${name}-local`);
      } finally {
        await page.goto('/dashboard');
        // Visual fixtures must not leave private local records in the shared
        // test account: later real-empty-state checks require an empty category.
        const saved = await (
          await request.get(`http://127.0.0.1:8001/api/games/${localGame.id}`)
        ).json();
        const removed = await request.delete(
          `http://127.0.0.1:8001/api/games/${localGame.id}?version=${saved.version}`,
        );
        expect(removed.ok()).toBe(true);
      }
      const online = activeOnlineGame();
      online.participants.attackers!.account_id = adminSession.id;
      await page.route(`**/api/online/games/${online.id}`, (route) =>
        route.fulfill({ json: online }),
      );
      await page.route('**/api/online/availability', (route) =>
        route.fulfill({ json: { enabled: true, eligible: true } }),
      );
      await page.goto(`/online/games/${online.id}`);
      await expect(page.getByRole('grid')).toBeVisible();
      await geometry(page);
      await capture(page, `${name}-online`);
      await page.goto(`/archive?review=account-single-player&selected=${game.id}`);
      await expect(page.getByRole('grid')).toBeVisible();
      await geometry(page);
      await capture(page, `${name}-replay`);
      await page.goto('/statistics');
      await expect(
        page.getByRole('heading', { name: 'Your statistics', exact: true }),
      ).toBeVisible();
      await expect(page.getByText('No games recorded yet.', { exact: true })).toBeVisible();
      await geometry(page);
      await capture(page, `${name}-statistics`);
      await page.goto('/training');
      await expect(page.locator('.training-workshop')).toBeVisible();
      await geometry(page);
      await capture(page, `${name}-workshop`);
      await openProfile(page);
      await expect(page.getByRole('radio', { name: 'Classic Table', exact: true })).toBeVisible();
      await geometry(page);
      await page.getByRole('region', { name: 'Appearance', exact: true }).scrollIntoViewIfNeeded();
      await capture(page, `${name}-appearance`);
    });
  }
test('theme label remains left aligned in English and German, keyboard operable', async ({
  page,
}) => {
  for (const width of [1024, 1366, 1920, 3840]) {
    await page.setViewportSize({ width, height: width === 3840 ? 2160 : 1080 });
    await page.goto('/dashboard');
    for (const language of ['en', 'de']) {
      await page.getByRole('combobox', { name: /^(Language|Sprache)$/ }).selectOption(language);
      await settled(page);
      const label = await page.locator('.theme-select-label').boundingBox();
      const select = page.locator('.theme-select select');
      const box = await select.boundingBox();
      expect(label!.x + label!.width).toBeLessThan(box!.x);
      expect(Math.abs(label!.y + label!.height / 2 - box!.y - box!.height / 2)).toBeLessThan(2);
      await expect(select.locator('option')).toHaveCount(4);
      await select.focus();
      await expect(select).toBeFocused();
      await select.selectOption('saga-hall');
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', 'saga-hall');
    }
  }
});
test('automatic CSS viewport scale and explicit overrides survive resize, reload and zoom-sized viewports', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/dashboard');
  await expect(page.locator('html')).toHaveAttribute('data-density', 'default');
  await page.setViewportSize({ width: 2560, height: 1440 });
  await expect(page.locator('html')).toHaveAttribute('data-density', 'default');
  await page.setViewportSize({ width: 3840, height: 2160 });
  await expect(page.locator('html')).toHaveAttribute('data-density', 'large');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-density', 'large');
  // At 200% browser zoom a 4K browser exposes approximately a 1920x1080 CSS viewport.
  await page.setViewportSize({ width: 1920, height: 1080 });
  await expect(page.locator('html')).toHaveAttribute('data-density', 'default');
  await openProfile(page);
  for (const [name, id] of [
    ['Compact', 'compact'],
    ['Default', 'default'],
    ['Large', 'large'],
  ]) {
    await page.getByRole('radio', { name, exact: true }).check();
    for (const viewport of [
      { width: 3840, height: 2160 },
      { width: 2560, height: 1440 },
      { width: 1920, height: 1080 },
      { width: 1366, height: 768 },
      { width: 1024, height: 768 },
      { width: 768, height: 1024 },
      { width: 390, height: 844 },
      { width: 320, height: 640 },
    ]) {
      await page.setViewportSize(viewport);
      await expect(page.locator('html')).toHaveAttribute('data-density', id);
      await settled(page);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-density', id);
  }
});

test.describe('native browser zoom', () => {
  test.use({ viewport: null, deviceScaleFactor: undefined, isMobile: false, hasTouch: false });
  test('genuine 200% browser zoom uses CSS viewport defaults and preserves explicit scale in all themes', async ({
    playwright,
    adminSession,
  }, info) => {
    test.skip(
      info.project.name !== 'chromium',
      'Native browser zoom is checked once using Chromium with the test-only extension.',
    );
    const { mkdtempSync, rmSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { resolve, join } = await import('node:path');
    const directory = mkdtempSync(join(tmpdir(), 'hnefatafl-zoom-'));
    const extension = resolve('tests/zoom-extension');
    const context = await playwright.chromium.launchPersistentContext(directory, {
      channel: 'chromium',
      headless: true,
      viewport: null,
      deviceScaleFactor: undefined,
      isMobile: false,
      hasTouch: false,
      args: [
        '--window-size=3840,2160',
        `--disable-extensions-except=${extension}`,
        `--load-extension=${extension}`,
      ],
    });
    try {
      await context.addCookies(adminSession.state.cookies);
      await context.addInitScript(
        (id) => localStorage.setItem('hnefatafl-account-cache', id),
        adminSession.id,
      );
      const page = await context.newPage();
      await page.goto('http://127.0.0.1:4173/dashboard');
      const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
      const zoom = async (factor: number) =>
        worker.evaluate(async (value) => {
          const api = (
            globalThis as unknown as {
              chrome: {
                tabs: {
                  query: (q: object) => Promise<{ id: number; url?: string }[]>;
                  setZoom: (id: number, value: number) => Promise<void>;
                  getZoom: (id: number) => Promise<number>;
                };
              };
            }
          ).chrome.tabs;
          const tab = (await api.query({})).find((t) =>
            t.url?.startsWith('http://127.0.0.1:4173'),
          )!;
          await api.setZoom(tab.id, value);
          return await api.getZoom(tab.id);
        }, factor);
      await expect(page.locator('html')).toHaveAttribute('data-density', 'large');
      for (const theme of themes) {
        await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
        expect(await zoom(2)).toBe(2);
        await expect(page.locator('html')).toHaveAttribute('data-density', 'default');
        expect(await page.evaluate(() => innerWidth)).toBe(1920);
        await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await page.screenshot({
          path: `${output}/${theme}-zoom-200.jpg`,
          type: 'jpeg',
          quality: 85,
        });
        expect(await zoom(1)).toBe(1);
        await expect(page.locator('html')).toHaveAttribute('data-density', 'large');
      }
      await openProfile(page);
      await page.getByRole('radio', { name: 'Compact', exact: true }).check();
      await zoom(2);
      await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
      await page.reload();
      await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    } finally {
      await context.close();
      rmSync(directory, { recursive: true, force: true });
    }
  });
});

function ratio(a: string, b: string) {
  const l = (value: string) => {
    const c = (value.match(/[\d.]+/g) || [])
      .slice(0, 3)
      .map(Number)
      .map((n) => {
        const x = n / 255;
        return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
      });
    return c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722;
  };
  const x = l(a),
    y = l(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
test('Saga crafted buttons retain contrast across gradient stops, feedback and reduced motion; scenes fail safely', async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: 'saga-hall' })),
  );
  await page.route('**/assets/arena-v2/assets/art/**', (route) => route.abort());
  await seedEmptyGameLists(page);
  await seedOnlineHistory(page, emptyOnlineHistory());
  await page.goto('/dashboard');
  const primary = page.locator('.arena-hero .arena-primary-action').first();
  for (const control of [primary, page.locator('.arena-hero .button.secondary').first()]) {
    await expect(control).toBeVisible();
    const box = await control.boundingBox();
    await expect(async () => {
      const colors = await control.evaluate((node) => {
        if (!node.isConnected) return null;
        const s = getComputedStyle(node);
        return {
          text: s.color,
          base: s.backgroundColor,
          stops: s.backgroundImage.match(/rgba?\([^)]+\)/g) || [],
        };
      });
      expect(colors).not.toBeNull();
      expect(colors!.text).toMatch(/^rgba?\(/);
      expect(ratio(colors!.text, colors!.base)).toBeGreaterThanOrEqual(4.5);
      for (const stop of colors!.stops)
        expect(ratio(colors!.text, stop)).toBeGreaterThanOrEqual(4.5);
    }).toPass({ timeout: 10000 });
    await control.hover();
    expect(await control.boundingBox()).toEqual(box);
    await control.focus();
    await expect(control).toBeFocused();
    expect(
      await control.evaluate((n) => parseFloat(getComputedStyle(n).outlineWidth)),
    ).toBeGreaterThanOrEqual(3);
  }
  const secondary = page.locator('.arena-hero button.secondary').first();
  await secondary.evaluate((n) => ((n as HTMLButtonElement).disabled = true));
  await expect(secondary).toBeDisabled();
  await expect(secondary).toHaveCSS('cursor', 'not-allowed');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(primary).toHaveCSS('transition-duration', '0s');
  const picker = page.getByRole('combobox', { name: 'Theme', exact: true });
  if (await picker.isVisible()) await picker.selectOption('iron-ash');
  else {
    await openProfile(page);
    await page.getByRole('radio', { name: 'Iron & Ash', exact: true }).check();
    await openDestination(page, 'dashboard');
  }
  await expect(page.locator('html')).toHaveAttribute('data-ui-theme', 'iron-ash');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(17, 22, 25)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
