import { test as browserTest, type Locator, type Page } from '@playwright/test';
import { expect, test } from './auth-fixture';
import { projectAchievements } from './achievement-fixture';
import type { AchievementProgress } from '../src/portraitTypes';

const gallery = (page: Page) => page.locator('.portrait-gallery');
const card = (page: Page, name: string) =>
  gallery(page)
    .locator('> li')
    .filter({ has: page.getByRole('heading', { name, exact: true }) });
const reveal = '.portrait-reveal-canvas';
const celebration = '.portrait-unlock-celebration';

async function eligiblePortraits(page: Page, ids = ['shieldmaiden']) {
  const original = await page.request.get('/api/personal/achievements');
  expect(original.ok()).toBe(true);
  const data: AchievementProgress = await original.json();
  data.items = data.items.map((item) =>
    item.portrait_id && ids.includes(item.portrait_id)
      ? { ...item, progress: item.threshold, unlocked: true }
      : item,
  );
  const claimed: string[] = [];
  await page.route('**/api/personal/achievements', (route) =>
    route.fulfill({ json: projectAchievements({ ...data, unlocked_portrait_ids: claimed }) }),
  );
  return claimed;
}

async function colorCoverage(canvas: Locator) {
  const result = await canvas.evaluate((source: HTMLCanvasElement) => {
    const sample = document.createElement('canvas');
    sample.width = sample.height = 32;
    const context = sample.getContext('2d')!;
    context.drawImage(source, 0, 0, sample.width, sample.height);
    const { data } = context.getImageData(0, 0, sample.width, sample.height);
    let shown = 0,
      opaque = 0,
      feathered = 0,
      colored = 0;
    for (let index = 0; index < data.length; index += 4) {
      const [r, g, b, alpha] = data.subarray(index, index + 4);
      if (alpha > 8) shown++;
      if (alpha > 8 && alpha < 247) feathered++;
      if (alpha >= 247) {
        opaque++;
        if (Math.max(r, g, b) - Math.min(r, g, b) > 12) colored++;
      }
    }
    const samples = sample.width * sample.height;
    return { coverage: shown / samples, opaque: opaque / samples, feathered, colored };
  });
  return result;
}

test('reveals localized artwork after confirmation and restores the original image', async ({
  page,
}, info) => {
  const claimed = await eligiblePortraits(page);
  let requests = 0;
  let release = () => {};
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/portraits/unlock', async (route) => {
    requests++;
    await waiting;
    claimed.push('shieldmaiden');
    await route.fulfill({ json: { portrait_id: 'shieldmaiden', newly_unlocked: true } });
  });
  const downloads: string[] = [];
  page.on('request', (request) => {
    if (request.url().endsWith('/shieldmaiden-large.webp')) downloads.push(request.url());
  });
  const capture = process.env.PORTRAIT_UNLOCK_EVIDENCE && info.project.name === 'chromium';
  if (capture) await page.setViewportSize({ width: 1920, height: 1080 });
  await page.clock.install();
  await page.goto('/portraits');
  const portrait = card(page, 'Shieldmaiden');
  const unlock = portrait.getByRole('button', { name: 'Unlock portrait: Shieldmaiden' });
  await portrait.evaluate((element) => element.scrollIntoView({ block: 'center' }));
  const image = portrait.locator('img');
  await image.evaluate((element: HTMLImageElement) => element.decode());
  const original = await image.elementHandle();
  const artwork = await image.getAttribute('src');
  const artworkBox = await image.boundingBox();
  const cardBox = await portrait.boundingBox();
  const canvas = portrait.locator(reveal);
  const glow = portrait.locator(celebration);
  await expect(image).toHaveCSS('filter', 'grayscale(1)');
  await expect(gallery(page).locator('canvas')).toHaveCount(0);
  const save = async (stage: string) => {
    if (!capture) return;
    const frame = await portrait.locator('.portrait-card-frame').boundingBox();
    await page.screenshot({ path: info.outputPath(`${stage}.png`), clip: frame! });
  };
  await save('01-ready');

  await unlock.focus();
  await page.keyboard.press('Enter');
  await expect(portrait).toHaveAttribute('data-portrait-state', 'unlocking');
  await expect(unlock).toBeDisabled();
  await expect(unlock).toBeFocused();
  await expect(canvas).toHaveCount(0);
  await unlock.evaluate((button: HTMLButtonElement) => button.click());
  expect(requests).toBe(1);
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  release();
  await expect(canvas).toBeAttached();
  const layer = await canvas.elementHandle();
  await expect(canvas).toHaveAttribute('aria-hidden', 'true');
  await expect(image).toHaveCSS('filter', 'grayscale(1)');
  expect(await canvas.boundingBox()).toEqual(artworkBox);
  expect(await portrait.boundingBox()).toEqual(cardBox);
  expect(await canvas.evaluate((element: HTMLCanvasElement) => element.width)).toBeLessThanOrEqual(
    640,
  );
  await expect(
    portrait.getByRole('button', { name: 'Select portrait: Shieldmaiden' }),
  ).toBeEnabled();
  await expect(card(page, 'Shieldbearer')).toHaveAttribute('data-portrait-state', 'selected');

  await page.clock.runFor(100);
  expect((await colorCoverage(canvas)).coverage).toBe(0);
  await page.clock.runFor(220);
  const initial = await colorCoverage(canvas);
  expect(initial.coverage).toBeGreaterThan(0.01);
  expect(initial.coverage).toBeLessThan(0.16);
  expect(initial.feathered).toBeGreaterThan(0);
  expect(initial.colored).toBeGreaterThan(0);
  await save('02-first-reveal');
  await page.clock.runFor(980);
  const expanded = await colorCoverage(canvas);
  expect(expanded.coverage).toBeGreaterThan(0.4);
  expect(expanded.coverage).toBeLessThan(0.95);
  expect(expanded.colored).toBeGreaterThan(initial.colored);
  await save('03-expansion');
  await page.clock.runFor(1075);
  expect((await colorCoverage(canvas)).opaque).toBe(1);
  await page.clock.runFor(50);
  await expect(canvas).toHaveCount(0);
  expect(await layer?.evaluate((element) => element.width)).toBe(0);
  await expect(image).toHaveCSS('filter', 'none');
  await expect(glow).toHaveAttribute('aria-hidden', 'true');
  await expect(glow).toHaveCSS('pointer-events', 'none');
  expect(
    await glow.evaluate((element) => getComputedStyle(element, '::before').animationDuration),
  ).toBe('1.05s');
  if (capture) {
    await glow.evaluate((element) => {
      for (const animation of element.getAnimations({ subtree: true })) {
        animation.pause();
        animation.currentTime = 260;
      }
    });
    await save('04-celebration');
  }
  await page.clock.runFor(1050);
  await expect(glow).toHaveCount(0);
  expect(await image.boundingBox()).toEqual(artworkBox);
  expect(await portrait.boundingBox()).toEqual(cardBox);
  expect(downloads).toHaveLength(1);
  expect(requests).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await save('05-unlocked');
  expect(await original?.evaluate((element) => element.isConnected)).toBe(true);
  await expect(image).toHaveAttribute('src', artwork!);
  await page.clock.resume();
  await page.reload();
  await expect(portrait).toHaveAttribute('data-portrait-state', 'earned');
  await expect(canvas).toHaveCount(0);
  await expect(glow).toHaveCount(0);
  await expect(image).toHaveAttribute('src', artwork!);
  await expect(image).toHaveCSS('filter', 'none');
});

test('reports a network error, then uses an idempotent confirmation without a reveal', async ({
  page,
}) => {
  await eligiblePortraits(page);
  let attempts = 0;
  await page.route('**/api/portraits/unlock', async (route) => {
    if (++attempts === 1) return route.abort('failed');
    await route.fulfill({ json: { portrait_id: 'shieldmaiden', newly_unlocked: false } });
  });
  await page.goto('/portraits');
  const portrait = card(page, 'Shieldmaiden');
  const unlock = portrait.getByRole('button', { name: 'Unlock portrait: Shieldmaiden' });
  await unlock.click();
  await expect(
    page.getByRole('alert').getByText('Could not unlock Shieldmaiden. Please try again.'),
  ).toBeVisible();
  await expect(unlock).toBeEnabled();
  await expect(portrait.locator('img')).toHaveCSS('filter', 'grayscale(1)');
  await expect(portrait.locator(reveal)).toHaveCount(0);
  await unlock.click();
  await expect(portrait).toHaveAttribute('data-portrait-state', 'earned');
  await expect(portrait.locator('img')).toHaveCSS('filter', 'none');
  await expect(portrait.locator(`${reveal}, ${celebration}`)).toHaveCount(0);
  expect(attempts).toBe(2);
});

test('recovers a committed claim when its reply is lost', async ({ page }) => {
  const claimed = await eligiblePortraits(page);
  await page.route('**/api/portraits/unlock', async (route) => {
    claimed.push('shieldmaiden');
    await route.abort('failed');
  });
  await page.goto('/portraits');
  const portrait = card(page, 'Shieldmaiden');
  await portrait.getByRole('button', { name: 'Unlock portrait: Shieldmaiden' }).click();
  await expect(portrait).toHaveAttribute('data-portrait-state', 'earned');
  await expect(portrait.locator(`${reveal}, ${celebration}`)).toHaveCount(0);
});

test('keeps simultaneous animations independent and disposes them on hiding or navigation', async ({
  page,
}) => {
  const claimed = await eligiblePortraits(page, ['shieldmaiden', 'forge-keeper']);
  await page.route('**/api/portraits/unlock', async (route) => {
    const { portrait_id } = route.request().postDataJSON();
    claimed.push(portrait_id);
    await route.fulfill({ json: { portrait_id, newly_unlocked: true } });
  });
  await page.clock.install();
  await page.goto('/portraits');
  const maiden = card(page, 'Shieldmaiden');
  const keeper = card(page, 'Forge Keeper');
  for (const portrait of [maiden, keeper]) {
    const image = portrait.locator('img');
    await image.scrollIntoViewIfNeeded();
    await image.evaluate((element: HTMLImageElement) => element.decode());
  }
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await maiden.getByRole('button').evaluate((button: HTMLButtonElement) => button.click());
  await keeper.getByRole('button').evaluate((button: HTMLButtonElement) => button.click());
  const first = await maiden.locator(reveal).elementHandle();
  const second = await keeper.locator(reveal).elementHandle();
  expect(await first?.evaluate((element) => element.width)).toBeGreaterThan(0);
  expect(await second?.evaluate((element) => element.width)).toBeGreaterThan(0);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(gallery(page).locator(reveal)).toHaveCount(0);
  expect(await first?.evaluate((element) => element.width)).toBe(0);
  expect(await second?.evaluate((element) => element.width)).toBe(0);
  await expect(maiden.locator('img')).toHaveCSS('filter', 'none');
  await page.clock.resume();
  await page.evaluate(() => {
    Reflect.deleteProperty(document, 'visibilityState');
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.locator('a[href="/dashboard"]:visible').first().click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.goto('/portraits');
  await expect(keeper).toHaveAttribute('data-portrait-state', 'earned');
  await expect(maiden).toHaveAttribute('data-portrait-state', 'earned');
  await expect(gallery(page).locator(`${reveal}, ${celebration}`)).toHaveCount(0);
});

for (const fallback of ['reduced motion', 'failed canvas', 'failed image'] as const) {
  test(`keeps the unlock and shows the standard portrait with ${fallback}`, async ({ page }) => {
    await eligiblePortraits(page);
    await page.route('**/api/portraits/unlock', (route) =>
      route.fulfill({ json: { portrait_id: 'shieldmaiden', newly_unlocked: true } }),
    );
    if (fallback === 'reduced motion') await page.emulateMedia({ reducedMotion: 'reduce' });
    if (fallback === 'failed image')
      await page.route('**/shieldmaiden-large.webp', (route) => route.abort());
    if (fallback === 'failed canvas')
      await page.addInitScript(() => {
        const original = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (
          this: HTMLCanvasElement,
          ...args: Parameters<typeof original>
        ) {
          if (this.classList.contains('portrait-reveal-canvas')) return null;
          return original.apply(this, args);
        } as typeof original;
      });
    await page.goto('/portraits');
    const portrait = card(page, 'Shieldmaiden');
    const button = portrait.getByRole('button', { name: 'Unlock portrait: Shieldmaiden' });
    await button.scrollIntoViewIfNeeded();
    const artwork = portrait.locator('.portrait-art');
    if (fallback === 'failed image') await expect(artwork).toHaveClass(/portrait-fallback/);
    else await artwork.evaluate((element: HTMLImageElement) => element.decode());
    await button.click();
    await expect(portrait).toHaveAttribute('data-portrait-state', 'earned');
    await expect(portrait.locator(`${reveal}, ${celebration}`)).toHaveCount(0);
    await expect(artwork).toHaveCSS('filter', 'none');
    await expect(
      portrait.getByRole('button', { name: 'Select portrait: Shieldmaiden' }),
    ).toBeEnabled();
  });
}

browserTest(
  'claims through the real API and equips during the reveal',
  async ({ page, request }) => {
    const fixture = await request.post('http://127.0.0.1:8002/portrait-unlock-account', {
      data: {},
    });
    expect(fixture.ok()).toBe(true);
    const { name } = await fixture.json();
    const login = await page.request.post('http://127.0.0.1:8001/api/auth/login', {
      headers: { Origin: 'http://127.0.0.1:8001' },
      data: { identity: name, password: 'browser-fixture-only-password' },
    });
    expect(login.ok()).toBe(true);
    await page.goto('/portraits');
    const portrait = card(page, 'Forge Keeper');
    const unlock = portrait.getByRole('button', { name: 'Unlock portrait: Forge Keeper' });
    await unlock.scrollIntoViewIfNeeded();
    await portrait.locator('img').evaluate((element: HTMLImageElement) => element.decode());
    await unlock.click();
    await expect(portrait.locator(reveal)).toBeVisible();
    const layer = await portrait.locator(reveal).elementHandle();
    await expect(card(page, 'Shieldbearer')).toHaveAttribute('data-portrait-state', 'selected');
    await portrait.getByRole('button', { name: 'Select portrait: Forge Keeper' }).click();
    await expect(portrait).toHaveAttribute('data-portrait-state', 'selected');
    await expect(portrait.locator(reveal)).toBeVisible();
    await page.locator('a[href="/dashboard"]:visible').first().click();
    await expect(page).toHaveURL(/\/dashboard$/);
    expect(await layer?.evaluate((element) => (element as HTMLCanvasElement).width)).toBe(0);
    await expect(page.locator('.dashboard-portrait img')).toHaveAttribute(
      'src',
      /forge-keeper\.webp$/,
    );
    await page.goto('/portraits');
    await expect(portrait).toHaveAttribute('data-portrait-state', 'selected');
    await expect(portrait.locator(`${reveal}, ${celebration}`)).toHaveCount(0);
  },
);
