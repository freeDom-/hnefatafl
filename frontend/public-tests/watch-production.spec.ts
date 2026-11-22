import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { readFileSync, readdirSync } from 'node:fs';
import { basename, join } from 'node:path';

const themes = ['kings-table', 'iron-ash', 'saga-hall', 'classic-table'];
const languages = ['en', 'de'];

function contrast(foreground: string, background: string) {
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

function inspectNetwork(page: Page) {
  const buildAssets = new Set(readdirSync('dist/assets'));
  const errors: string[] = [];
  const assets: { path: string; status: number; type: string }[] = [];
  const checks: Promise<void>[] = [];
  const apis: { path: string; method: string; cookie?: string; authorization?: string }[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('requestfailed', (request) =>
    errors.push(`${request.url()}: ${request.failure()?.errorText}`),
  );
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/')) {
      checks.push(
        request.allHeaders().then((headers) => {
          apis.push({
            path,
            method: request.method(),
            cookie: headers.cookie,
            authorization: headers.authorization,
          });
        }),
      );
    }
  });
  page.on('response', (response) => {
    const path = new URL(response.url()).pathname;
    if (!path.startsWith('/assets/') || !/\.(css|js)$/.test(path)) return;
    checks.push(
      (async () => {
        const file = basename(path);
        expect(buildAssets.has(file), path).toBe(true);
        expect(response.request().redirectedFrom(), path).toBeNull();
        expect(response.status(), path).toBe(200);
        const type = response.headers()['content-type'];
        expect(type, path).toMatch(
          path.endsWith('.css') ? /^text\/css(?:;|$)/ : /^(?:text|application)\/javascript(?:;|$)/,
        );
        expect(
          Buffer.compare(await response.body(), readFileSync(join('dist/assets', file))),
          path,
        ).toBe(0);
        assets.push({ path, status: response.status(), type });
      })().catch((error: unknown) => {
        errors.push(String(error));
      }),
    );
  });
  return async (info: TestInfo) => {
    await Promise.all(checks);
    expect(errors).toEqual([]);
    expect(apis.length).toBeGreaterThan(0);
    expect(
      apis.every(
        ({ path, method, cookie, authorization }) =>
          path.startsWith('/api/public/online/games') &&
          method === 'GET' &&
          !cookie &&
          !authorization,
      ),
    ).toBe(true);
    expect(assets.some(({ path }) => /\/PublicArena-.*\.js$/.test(path))).toBe(true);
    expect(assets.some(({ path }) => /\/AuthenticatedArena-.*\.(js|css)$/.test(path))).toBe(false);
    expect(assets.some(({ path }) => path.endsWith('.css'))).toBe(true);
    await info.attach('production-assets', {
      body: JSON.stringify(assets, null, 2),
      contentType: 'application/json',
    });
  };
}

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
}

async function discoveryLayout(page: Page) {
  await expect(page.locator('.public-header')).toHaveCSS('display', 'flex');
  await expect(page.locator('.public-header-nav')).toHaveCSS('display', 'flex');
  await expect(page.locator('.public-header-nav')).toHaveCSS('flex-wrap', 'wrap');
  await expect(page.locator('.public-filters')).toHaveCSS('display', 'grid');
  const width = page.viewportSize()!.width;
  const padding = await page
    .locator('.public-intro')
    .evaluate((node) => parseFloat(getComputedStyle(node).paddingLeft));
  expect(padding).toBeGreaterThanOrEqual(20);
  const controls = await page.locator('.public-filters label').evaluateAll((nodes) =>
    nodes.map((node) => {
      const { x, y, width, height } = node.getBoundingClientRect();
      return { x, y, width, height };
    }),
  );
  // Grid items align at their lower edge; localized labels may wrap above it.
  // Native select/input heights and line wrapping must not create false rows.
  const rows: number[] = [];
  for (const { y, height } of controls) {
    const bottom = y + height;
    if (!rows.some((edge) => Math.abs(edge - bottom) < 2)) rows.push(bottom);
  }
  expect(rows.length).toBe(width > 1100 ? 1 : width < 390 ? 4 : 2);
  for (let i = 0; i < controls.length; i++) {
    for (const other of controls.slice(i + 1)) {
      const current = controls[i];
      expect(
        current.x + current.width <= other.x + 1 ||
          other.x + other.width <= current.x + 1 ||
          current.y + current.height <= other.y + 1 ||
          other.y + other.height <= current.y + 1,
      ).toBe(true);
    }
  }
  for (const control of await page
    .locator(
      '.public-header select, .public-filters input, .public-filters select, .public-filters button',
    )
    .all()) {
    const box = (await control.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    expect(await control.evaluate((node) => node.clientWidth >= node.scrollWidth - 1)).toBe(true);
  }
  await noOverflow(page);
}

async function capture(page: Page, info: TestInfo, name: string) {
  // Native-resolution captures are already retained by the output artifact.
  // Attaching copies duplicates hundreds of images without adding evidence.
  await page.screenshot({
    path: info.outputPath(`${name}.jpg`),
    fullPage: true,
    type: 'jpeg',
    quality: 85,
  });
}

async function filter(page: Page, values: { ruleset?: string; status?: string; player?: string }) {
  const form = page.locator('.public-filters');
  await form
    .locator('select')
    .nth(0)
    .selectOption(values.ruleset ?? '');
  await form
    .locator('select')
    .nth(1)
    .selectOption(values.status ?? '');
  await form.locator('input').fill(values.player ?? '');
  const response = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/public/online/games',
  );
  await form.locator('button').click();
  expect((await response).status()).toBe(200);
}

for (const theme of themes) {
  for (const language of languages) {
    test(`${theme} ${language}: production discovery layout, assets and history`, async ({
      page,
      context,
    }, info) => {
      const network = inspectNetwork(page);
      // A stale session cookie must not be forwarded by the public transport.
      await context.addCookies([
        {
          name: 'hnefatafl_session',
          value: 'invalid-fixture-session',
          url: 'http://127.0.0.1:8011',
        },
      ]);
      await page.addInitScript(
        ({ theme, language }) => {
          if (!localStorage.getItem('hnefatafl-appearance'))
            localStorage.setItem(
              'hnefatafl-appearance',
              JSON.stringify({ version: 2, uiTheme: theme }),
            );
          localStorage.setItem('hnefatafl-language', language);
        },
        { theme, language },
      );
      await page.goto('/watch');
      await expect(page.locator('.public-match')).toHaveCount(12);
      await capture(page, info, 'discovery-many');
      await discoveryLayout(page);
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
      await expect(page.locator('html')).toHaveAttribute('lang', language);
      const license = page.locator('.public-arena .license-notice');
      await expect(license).toBeVisible();
      await expect(
        license.getByRole('link', {
          name: language === 'de' ? 'Lizenzbedingungen' : 'License terms',
        }),
      ).toHaveAttribute('href', '/LICENSE.txt');
      const licenseText = await page.request.get('/LICENSE.txt');
      expect(licenseText.status()).toBe(200);
      expect(await licenseText.text()).toContain('PolyForm Noncommercial License 1.0.0');
      const notices = await page.request.get('/THIRD_PARTY_NOTICES.txt');
      expect(notices.status()).toBe(200);
      const colors = await page.locator('.public-intro').evaluate((node) => ({
        text: getComputedStyle(node.querySelector('p:last-child')!).color,
        background: getComputedStyle(node).backgroundColor,
      }));
      expect(contrast(colors.text, colors.background)).toBeGreaterThanOrEqual(4.5);
      await page.keyboard.press('Tab');
      await expect(page.locator('.skip-link')).toBeFocused();
      expect((await page.locator('.skip-link').boundingBox())!.x).toBeGreaterThanOrEqual(0);
      await page.keyboard.press('Enter');
      await expect(page.locator('#public-content')).toBeFocused();
      const card = page.locator('.public-match').first();
      await expect(card).toHaveCSS('display', 'grid');
      await expect(card).toHaveCSS('flex-grow', '1');
      const sizing = await card.evaluate((node) => {
        const style = getComputedStyle(node);
        return {
          width: node.getBoundingClientRect().width,
          max: parseFloat(style.maxWidth),
          basis: parseFloat(style.flexBasis),
          padding: parseFloat(style.paddingLeft),
          border: parseFloat(style.borderLeftWidth),
        };
      });
      expect(sizing.width).toBeLessThanOrEqual(sizing.max + 1);
      expect(sizing.basis).toBeGreaterThanOrEqual(320);
      expect(sizing.padding).toBeGreaterThanOrEqual(20);
      // Saga Hall deliberately uses a carved 3px frame on every arena surface.
      expect(sizing.border).toBe(theme === 'saga-hall' ? 3 : 4);
      const pagination = page.locator('.public-pagination');
      await pagination.locator('button').last().click();
      await expect(page.locator('.public-match')).toHaveCount(3);
      await pagination.locator('button').first().click();
      await expect(page.locator('.public-match')).toHaveCount(12);
      await filter(page, { ruleset: 'copenhagen', status: 'active' });
      await expect(page.locator('.public-match')).toHaveCount(1);
      await capture(page, info, 'discovery-one');
      await filter(page, { status: 'waiting' });
      await expect(page.locator('.public-match')).toHaveCount(2);
      await capture(page, info, 'discovery-two');
      await filter(page, { player: 'NoSuchPublicPlayer' });
      const empty = page.locator('.public-empty');
      await expect(empty).toBeVisible();
      await expect(empty).toHaveCSS('display', 'grid');
      await expect(empty).toHaveCSS('text-align', 'center');
      expect(
        await empty.evaluate((node) => parseFloat(getComputedStyle(node).paddingTop)),
      ).toBeGreaterThanOrEqual(48);
      await capture(page, info, 'discovery-empty');
      await page.reload();
      await discoveryLayout(page);
      await expect(page.locator('.public-match')).toHaveCount(12);
      await page.locator('.public-match-action').first().click();
      await expect(page.getByRole('grid')).toBeVisible();
      await page.goBack();
      await discoveryLayout(page);
      await page.goForward();
      await expect(page.getByRole('grid')).toBeVisible();
      await page.goBack();
      await discoveryLayout(page);
      await network(info);
    });

    test(`${theme} ${language}: real waiting, live, terminal and replay views`, async ({
      page,
      request,
    }, info) => {
      const network = inspectNetwork(page);
      const fixtures = (await (
        await request.get('http://127.0.0.1:8012/fixtures')
      ).json()) as Record<string, string>;
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
      await page.emulateMedia({ reducedMotion: 'reduce' });
      for (const state of [
        'waiting',
        'brandubh',
        'tablut',
        'copenhagen',
        'completed',
        'cancelled',
        'voided',
      ]) {
        const path = `/watch/games/${fixtures[state]}${state === 'completed' ? '/replay' : ''}`;
        await page.goto(path);
        await expect(page.getByRole('grid')).toBeVisible();
        await expect(page.locator('.public-header')).toHaveCSS('display', 'flex');
        await expect(page.locator('.public-header-nav')).toHaveCSS('display', 'flex');
        await expect(page.locator('.public-view')).toHaveCSS('display', 'grid');
        await expect(page.locator('.public-player img').first()).toBeVisible();
        for (const rating of await page.locator('.public-player small').all()) {
          await expect(rating).toHaveText(/^(?:Rating|Wertung): \d+$/);
        }
        await expect
          .poll(() =>
            page
              .locator('.public-player img')
              .first()
              .evaluate((node) => (node as HTMLImageElement).naturalWidth),
          )
          .toBeGreaterThan(0);
        await expect(page.locator('.public-view')).toHaveCSS('animation-name', 'none');
        await expect(page.locator('.public-view')).toHaveCSS('transition-duration', '0s');
        expect(
          await page
            .locator('.public-live-note')
            .first()
            .evaluate((node) => parseFloat(getComputedStyle(node).fontSize)),
        ).toBeGreaterThanOrEqual(12);
        const board = (await page.getByRole('grid').boundingBox())!;
        expect(Math.abs(board.width - board.height)).toBeLessThan(1);
        expect(board.width).toBeGreaterThanOrEqual(200);
        expect(board.width).toBeLessThanOrEqual(page.viewportSize()!.width);
        await expect(page.getByRole('gridcell').first()).toHaveAttribute('aria-disabled', 'true');
        await page.getByRole('gridcell').first().dispatchEvent('click');
        expect(await page.locator('.legal, .hint-source, .hint-target').count()).toBe(0);
        if (state === 'completed') {
          const controls = page.locator('.public-replay-controls');
          await expect(controls).toHaveCSS('display', 'flex');
          await controls.focus();
          await page.keyboard.press('End');
          await expect(controls.locator('output')).toContainText('2');
          await page.keyboard.press('Home');
          await expect(controls.locator('output')).toContainText('0');
          expect(await controls.evaluate((node) => getComputedStyle(node).outlineStyle)).toBe(
            'solid',
          );
          if (page.viewportSize()!.width < 768) {
            for (const button of await controls.locator('button').all())
              expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
          }
        }
        if (state === 'cancelled' || state === 'voided') {
          await expect(page.locator('.public-live-note').last()).toHaveText(
            state === 'cancelled'
              ? language === 'de'
                ? 'Wartende Partie abgesagt.'
                : 'Waiting game cancelled.'
              : language === 'de'
                ? 'Partie annulliert.'
                : 'Game voided.',
          );
          await expect(page.locator('.public-replay-controls')).toHaveCount(0);
        }
        await noOverflow(page);
        await capture(page, info, state);
        await page.reload();
        await expect(page.locator('.public-view')).toHaveCSS('display', 'grid');
      }
      await network(info);
    });
  }
}

test('anonymous static and private routes use the real security boundary', async ({ request }) => {
  for (const path of ['/watch', '/watch/games/not-found', '/watch/games/not-found/replay']) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('text/html');
    expect(response.headers()['content-security-policy']).toContain("default-src 'self'");
    expect(response.headers()['cache-control']).toBe('no-store');
  }
  for (const path of ['/dashboard', '/games/private', '/online/games/private', '/training']) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status()).toBe(307);
    expect(response.headers().location).toMatch(/^\/login\?next=/);
  }
  for (const path of ['/api/games', '/api/profile'])
    expect((await request.get(path)).status()).toBe(401);
  expect((await request.get('/api/public/online/games/private')).status()).toBe(404);
  expect((await request.post('/api/public/online/games', { data: {} })).status()).not.toBe(200);
  expect((await request.get('/assets/PublicArena-stale.css')).status()).toBe(404);
});

test('production errors keep their layout and retry recovers the real API', async ({
  page,
}, info) => {
  await page.route('**/api/public/online/games?*', (route) =>
    route.fulfill({ status: 503, json: {} }),
  );
  await page.goto('/watch');
  await expect(page.getByRole('alert')).toBeVisible();
  await discoveryLayout(page);
  await expect(page.locator('.public-notice')).toHaveCSS('display', 'grid');
  await capture(page, info, 'error');
  await page.unroute('**/api/public/online/games?*');
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.locator('.public-match')).toHaveCount(12);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await capture(page, info, 'retry');
});

test('switching entry routes and Browser Back preserve public styles and appearance', async ({
  page,
}) => {
  await page.goto('/watch');
  await discoveryLayout(page);
  await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption('saga-hall');
  await expect(page.locator('html')).toHaveAttribute('data-ui-theme', 'saga-hall');
  // Exercise EntryRouter's same-document popstate path as well as document
  // navigation: account CSS loaded later must not break the public layout.
  await page.evaluate(() => {
    history.pushState({}, '', '/login');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.getByRole('textbox', { name: /Username or email/ })).toBeVisible();
  await page.goBack();
  await discoveryLayout(page);
  await expect(page.locator('html')).toHaveAttribute('data-ui-theme', 'saga-hall');
  await page.reload();
  await discoveryLayout(page);
  await expect(page.locator('html')).toHaveAttribute('data-ui-theme', 'saga-hall');
});
