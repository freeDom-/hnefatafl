import { mkdirSync } from 'node:fs';
import { expect, test } from './auth-fixture';
import { projectAchievements } from './achievement-fixture';

test.use({ deviceScaleFactor: 2 });

const themes = ['kings-table', 'iron-ash', 'saga-hall', 'classic-table'];
function contrast(foreground: string, background: string) {
  const luminance = (value: string) => {
    const [r, g, b] = value
      .match(/[\d.]+/g)!
      .slice(0, 3)
      .map(Number)
      .map((channel) => {
        const linear = channel / 255;
        return linear <= 0.04045 ? linear / 12.92 : ((linear + 0.055) / 1.055) ** 2.4;
      });
    return r * 0.2126 + g * 0.7152 + b * 0.0722;
  };
  const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}
for (const theme of themes) {
  test(`Hall of Renown has manual portrait unlocks and accessible controls in ${theme}`, async ({
    page,
  }, info) => {
    // Four localized high-DPI viewport captures per theme need a larger image-encoding budget.
    test.setTimeout(240_000);
    let selected = 'shieldbearer';
    let awarded = false;
    let claimed = false;
    const achievements = async () => {
      const response = await page.request.get('/api/personal/achievements');
      expect(response.ok()).toBe(true);
      const progress = await response.json();
      progress.items = progress.items.map((item: { id: string }) => ({
        ...item,
        progress:
          item.id.startsWith('rated-wins') || item.id === 'first-rated-win'
            ? awarded
              ? 10
              : 0
            : 0,
        unlocked:
          item.id === 'brandubh-initiate' ||
          (awarded && ['first-rated-win', 'rated-wins-3', 'rated-wins-10'].includes(item.id)),
        awarded_at:
          item.id === 'brandubh-initiate' ||
          (awarded && ['first-rated-win', 'rated-wins-3', 'rated-wins-10'].includes(item.id))
            ? '2035-01-01T00:00:00Z'
            : null,
      }));
      progress.unlocked_portrait_ids = ['wanderer', ...(claimed ? ['shieldmaiden'] : [])];
      return projectAchievements(progress);
    };
    await page.route('**/api/personal/achievements', async (route) =>
      route.fulfill({ json: await achievements() }),
    );
    await page.route('**/api/profile', async (route) => {
      const response = await route.fetch();
      await route.fulfill({ json: { ...(await response.json()), portrait_id: selected } });
    });
    await page.route('**/api/portraits/unlock', async (route) => {
      expect(route.request().postDataJSON()).toEqual({ portrait_id: 'shieldmaiden' });
      expect(awarded).toBe(true);
      const newlyUnlocked = !claimed;
      claimed = true;
      await route.fulfill({ json: { portrait_id: 'shieldmaiden', newly_unlocked: newlyUnlocked } });
    });
    await page.route('**/api/profile/portrait', async (route) => {
      expect(route.request().method()).toBe('PATCH');
      selected = route.request().postDataJSON().portrait_id;
      const response = await page.request.get('/api/profile');
      await route.fulfill({ json: { ...(await response.json()), portrait_id: selected } });
    });
    const sizes =
      info.project.name === 'mobile'
        ? [
            { width: 768, height: 1024 },
            { width: 390, height: 844 },
          ]
        : [
            { width: 1920, height: 1080 },
            { width: 3840, height: 2160 },
          ];
    mkdirSync('test-results/portraits', { recursive: true });
    for (const language of ['en', 'de']) {
      await page.addInitScript(
        ({ theme, language }) => {
          const density =
            language === 'de' && screen.width < 1024
              ? screen.width < 500
                ? 'large'
                : 'compact'
              : undefined;
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ version: 2, uiTheme: theme, density }),
          );
          localStorage.setItem('hnefatafl-language', language);
        },
        { theme, language },
      );
      for (const size of sizes) {
        awarded = false;
        claimed = false;
        selected = 'shieldbearer';
        await page.setViewportSize(size);
        await page.goto('/portraits');
        if (size.width >= 1024)
          await expect(page.locator('.sidebar-nav a[href="/portraits"]')).toHaveAttribute(
            'aria-current',
            'page',
          );
        await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
        const density =
          size.width >= 3000 || (language === 'de' && size.width < 500)
            ? 'large'
            : language === 'de' && size.width < 1024
              ? 'compact'
              : 'default';
        await expect(page.locator('html')).toHaveAttribute('data-density', density);
        const gallery = page.locator('.portrait-gallery');
        await expect(gallery.locator('> li')).toHaveCount(12);
        await expect(page.locator('.portrait-achievement-list > li')).toHaveCount(10);
        await expect(page.locator('[data-family="rated-wins"]')).toHaveAttribute('data-tier', '0');
        await expect(page.locator('#achievement-score')).toHaveAttribute('value', '10');
        await expect(gallery.locator('[data-portrait-state="selected"]')).toHaveCount(1);
        await expect(gallery.locator('[data-portrait-state="earned"]')).toHaveCount(1);
        await expect(gallery.locator('[data-portrait-state="locked"]')).toHaveCount(10);
        for (const card of await gallery.locator('> li').all()) {
          await expect(async () => {
            const colors = await card.evaluate((node) => {
              if (!node.isConnected) return null;
              return {
                foreground: getComputedStyle(node.querySelector('p')!).color,
                background: getComputedStyle(node).backgroundColor,
              };
            });
            expect(colors).not.toBeNull();
            expect(contrast(colors!.foreground, colors!.background)).toBeGreaterThanOrEqual(4.5);
          }).toPass({ timeout: 10000 });
        }
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        ).toBe(true);
        // Full-page screenshots do not scroll lazy images into the viewport.
        // Exercise normal scrolling and verify every supplied portrait decodes.
        for (const image of await gallery.locator('img').all()) {
          await image.scrollIntoViewIfNeeded();
          await expect(async () => {
            expect(
              await image.evaluate(
                (node: HTMLImageElement) => node.complete && node.naturalWidth > 0,
              ),
            ).toBe(true);
          }).toPass({ timeout: 10000 });
        }
        // Device pixels must fit the native bitmap, including fractional DPI.
        for (const image of await gallery.locator('img').all()) {
          const pixels = await image.evaluate((node: HTMLImageElement) => ({
            rendered: node.getBoundingClientRect().width * devicePixelRatio,
            native: node.naturalWidth,
            fit: getComputedStyle(node).objectFit,
          }));
          expect(pixels.rendered).toBeLessThanOrEqual(pixels.native + 1);
          expect(pixels.fit).toBe('contain');
        }
        // Native 1024px gallery counterparts remain sharp at the actual display density.
        const naturalSizes = await gallery
          .locator('img')
          .evaluateAll((nodes) =>
            (nodes as HTMLImageElement[]).map((node) => [node.naturalWidth, node.naturalHeight]),
          );
        for (const [width, height] of naturalSizes) expect([width, height]).toEqual([1024, 1024]);
        for (const card of await gallery.locator('> li').all()) {
          const art = card.locator('.portrait-card-art');
          const artBox = await art.boundingBox();
          const cardBox = await card.boundingBox();
          expect(artBox).not.toBeNull();
          expect(cardBox).not.toBeNull();
          expect(artBox!.width).toBeLessThanOrEqual(440.5);
          expect(artBox!.height).toBeLessThanOrEqual(440.5);
          expect(Math.abs(artBox!.width - artBox!.height)).toBeLessThanOrEqual(1);
          expect(artBox!.width).toBeLessThanOrEqual(cardBox!.width + 0.5);
          expect(artBox!.height).toBeLessThanOrEqual(cardBox!.height + 0.5);
        }
        await page.locator('#portrait-hall-heading').scrollIntoViewIfNeeded();
        await page.screenshot({
          path: `test-results/portraits/${theme}-${language}-${size.width}.png`,
          fullPage: true,
        });
        await expect(page.getByRole('button', { name: /claim|collect/i })).toHaveCount(0);
        expect(
          await gallery
            .locator('> li img')
            .evaluateAll((nodes) =>
              nodes.map((node) =>
                (node as HTMLImageElement).src.split('/').pop()?.replace('-large.webp', ''),
              ),
            ),
        ).toEqual([
          'shieldbearer',
          'forge-keeper',
          'shieldmaiden',
          'raven-seer',
          'einherjar',
          'iron-helm',
          'valkyrie',
          'wanderer',
          'skald',
          'fjord-scout',
          'berserker',
          'one-eyed-jarl',
        ]);
        await expect(page.locator('.portrait-achievements .achievement-score')).toBeVisible();
        const shieldmaiden = gallery
          .locator('li')
          .filter({ has: page.locator('img[src$="/shieldmaiden-large.webp"]') });
        const image = shieldmaiden.locator('img');
        await expect(image).toHaveCSS('filter', 'grayscale(1)');
        await expect(shieldmaiden.getByRole('progressbar')).toBeVisible();
        await expect(shieldmaiden.getByRole('button')).toHaveCount(0);
        const artBefore = await image.boundingBox();
        // Simulate a game-completion wake with an updated server-owned projection.
        awarded = true;
        await page.evaluate(() => window.dispatchEvent(new Event('progression-changed')));
        await expect(page.locator('[data-family="rated-wins"]')).toHaveAttribute('data-tier', '3');
        await expect(page.locator('#achievement-score')).toHaveAttribute('value', '70');
        await expect(gallery.locator('[data-portrait-state="selected"] img')).toHaveAttribute(
          'src',
          /shieldbearer-large.webp$/,
        );
        await expect(shieldmaiden).toHaveAttribute('data-portrait-state', 'ready');
        await expect(shieldmaiden.getByRole('progressbar')).toHaveCount(0);
        await expect(image).toHaveCSS('filter', 'grayscale(1)');
        expect(await image.boundingBox()).toEqual(artBefore);
        const unlock = shieldmaiden.getByRole('button', {
          name: /Unlock portrait|Porträt freischalten/,
        });
        await unlock.focus();
        expect(await unlock.evaluate((node) => node.matches(':focus-visible'))).toBe(true);
        await page.keyboard.press('Enter');
        await expect(shieldmaiden).toHaveAttribute('data-portrait-state', 'earned');
        await expect(gallery.locator('[data-portrait-state="selected"] img')).toHaveAttribute(
          'src',
          /shieldbearer-large.webp$/,
        );
        const select = shieldmaiden.getByRole('button');
        await expect(select).toBeFocused();
        await page.keyboard.press('Enter');
        await expect(shieldmaiden).toHaveAttribute('data-portrait-state', 'selected');
        await expect(select).toBeDisabled();
        await page.goto('/profile');
        await expect(page.locator('.profile-portrait img')).toHaveAttribute(
          'src',
          /\/shieldmaiden.webp$/,
        );
        await page.locator('.profile-portrait a[href="/portraits"]').click();
        await expect(
          page.locator('.portrait-gallery [data-portrait-state="selected"] img'),
        ).toHaveAttribute('src', /\/shieldmaiden-large.webp$/);
        await page.goto('/dashboard');
        await expect(page.locator('.dashboard-portrait img')).toHaveAttribute(
          'src',
          /\/shieldmaiden.webp$/,
        );
        await page
          .locator('[aria-labelledby="stat-portraits-heading"] a[href="/portraits"]')
          .click();
        await expect(
          page.locator('.portrait-gallery [data-portrait-state="selected"] img'),
        ).toHaveAttribute('src', /\/shieldmaiden-large.webp$/);
      }
    }
  });
}
