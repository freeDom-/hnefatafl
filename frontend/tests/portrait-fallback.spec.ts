import { expect, test, type Page } from './auth-fixture';

const themes = ['kings-table', 'iron-ash', 'saga-hall', 'classic-table'];

/** A small rated pool whose entries all publish a portrait. */
const leaderboard = {
  ruleset: 'copenhagen',
  policy: { version: 1, initial_rating: 1500, k_factor: 32, provisional_games: 10 },
  entries: Array.from({ length: 3 }, (_, index) => ({
    account_id: `account-${index}`,
    name: `Player ${index}`,
    username: `player${index}`,
    rating: 1600 - index,
    rated_games: 5,
    wins: 3,
    losses: 2,
    draws: 0,
    rank: index + 1,
    provisional: false,
    portrait_id: 'shieldbearer',
  })),
  total: 3,
  limit: 25,
  offset: 0,
  me: null,
};

/**
 * The shared Portrait fallback must be styled without the lazy Portrait Hall
 * chunk. Check that the placeholder is visible, contained by its context and
 * centred, using only the shared stylesheet plus the context rule.
 */
async function expectContainedFallback(
  page: Page,
  container: string,
  expected?: { width?: number; height?: number },
) {
  const element = page.locator(container).first();
  const fallback = element.locator('.portrait-art.portrait-fallback');
  await expect(fallback).toBeVisible({ timeout: 30000 });
  const metrics = await page.evaluate((selector) => {
    const element = document.querySelector(selector);
    const fallback = element?.querySelector('.portrait-art.portrait-fallback');
    const glyph = fallback?.querySelector('span');
    if (!element || !fallback || !glyph) return null;
    const box = fallback.getBoundingClientRect();
    const glyphBox = glyph.getBoundingClientRect();
    const containerBox = element.getBoundingClientRect();
    return {
      display: getComputedStyle(fallback).display,
      box: {
        width: box.width,
        height: box.height,
        cx: box.left + box.width / 2,
        cy: box.top + box.height / 2,
      },
      glyph: {
        width: glyphBox.width,
        height: glyphBox.height,
        cx: glyphBox.left + glyphBox.width / 2,
        cy: glyphBox.top + glyphBox.height / 2,
      },
      container: { width: containerBox.width, height: containerBox.height },
    };
  }, container);
  expect(metrics, `${container} fallback`).not.toBeNull();
  expect(metrics!.display).toBe('grid');
  expect(metrics!.box.width).toBeGreaterThan(0);
  expect(metrics!.box.height).toBeGreaterThan(0);
  expect(metrics!.box.width).toBeLessThanOrEqual(metrics!.container.width + 1);
  expect(metrics!.box.height).toBeLessThanOrEqual(metrics!.container.height + 1);
  if (expected?.width !== undefined) expect(metrics!.box.width).toBeCloseTo(expected.width, 0);
  if (expected?.height !== undefined) expect(metrics!.box.height).toBeCloseTo(expected.height, 0);
  expect(metrics!.glyph.width).toBeLessThanOrEqual(metrics!.box.width + 0.5);
  expect(metrics!.glyph.height).toBeLessThanOrEqual(metrics!.box.height + 0.5);
  expect(Math.abs(metrics!.glyph.cx - metrics!.box.cx)).toBeLessThanOrEqual(3);
  expect(Math.abs(metrics!.glyph.cy - metrics!.box.cy)).toBeLessThanOrEqual(3);
}

test('shared portrait fallback is styled on direct routes without the gallery chunk', async ({
  page,
}, info) => {
  test.setTimeout(180000);
  await page.route('**/portraits/*.webp', (route) => route.abort());
  await page.route(/\/api\/leaderboard\?/, (route) => route.fulfill({ json: leaderboard }));
  await page.setViewportSize(
    info.project.name === 'mobile' ? { width: 390, height: 844 } : { width: 1920, height: 1080 },
  );
  await page.goto('/dashboard');
  for (const theme of themes) {
    await page.evaluate(
      (value) =>
        localStorage.setItem(
          'hnefatafl-appearance',
          JSON.stringify({ version: 2, uiTheme: value }),
        ),
      theme,
    );
    // Direct navigation: never open Portrait Hall first.
    await page.goto('/profile');
    await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
    await expectContainedFallback(page, '.profile-portrait', { width: 88, height: 88 });
    await page.goto('/dashboard');
    await expectContainedFallback(page, '.dashboard-portrait');
    await page.goto('/leaderboard');
    await expectContainedFallback(page, '.leaderboard-avatar');
  }
});

test('portrait fallback scales across mobile and desktop viewports without overflow', async ({
  page,
}, info) => {
  test.setTimeout(180000);
  await page.route('**/portraits/*.webp', (route) => route.abort());
  const sizes =
    info.project.name === 'mobile'
      ? [
          { width: 320, height: 640 },
          { width: 390, height: 844 },
        ]
      : [
          { width: 1280, height: 720 },
          { width: 1920, height: 1080 },
          { width: 3840, height: 2160 },
        ];
  for (const size of sizes) {
    await page.setViewportSize(size);
    await page.goto('/profile');
    await expectContainedFallback(page, '.profile-portrait', { width: 88, height: 88 });
    await page.goto('/dashboard');
    await expectContainedFallback(page, '.dashboard-portrait');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  }
});

test('successful portrait image loading is unchanged on direct routes', async ({ page }) => {
  await page.goto('/profile');
  const image = page.locator('.profile-portrait img.portrait-art');
  await expect(image).toBeVisible({ timeout: 30000 });
  await expect(async () => {
    expect(
      await image.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth === 320),
    ).toBe(true);
  }).toPass({ timeout: 15000 });
  await expect(page.locator('.profile-portrait .portrait-fallback')).toHaveCount(0);
});
