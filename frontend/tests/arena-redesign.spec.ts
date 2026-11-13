import { mkdirSync } from 'node:fs';
import { expect, test } from './auth-fixture';

/**
 * Arena v2 layout + theme acceptance captures. Every appearance renders the same
 * information architecture: the desktop rail above 1024px, and the compact header
 * with the bottom navigation below it. Only colors, rail, fonts and surface
 * treatment change between the three named themes; `legacy` keeps the original
 * color-mode look.
 */
const output = 'test-results/arena-redesign';

const appearances = [
  { id: 'kings-table', rail: 'rgb(9, 45, 34)' },
  { id: 'iron-ash', rail: 'rgb(32, 38, 41)' },
  { id: 'saga-hall', rail: 'rgb(30, 16, 10)' },
  { id: 'legacy', rail: null },
] as const;

const viewports = [
  { name: '1440', width: 1440, height: 900 },
  { name: '1024', width: 1024, height: 768 },
  { name: '768', width: 768, height: 1024 },
  { name: '390', width: 390, height: 844 },
  { name: '320', width: 320, height: 640 },
] as const;

test.beforeAll(() => {
  mkdirSync(output, { recursive: true });
});

for (const appearance of appearances) {
  test(`captures the ${appearance.id} appearance at every required viewport`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: value }));
    }, appearance.id);
    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/dashboard');
      await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
      const root = page.locator('html');
      if (appearance.id === 'legacy') await expect(root).not.toHaveAttribute('data-ui-theme');
      else await expect(root).toHaveAttribute('data-ui-theme', appearance.id);

      // One primary navigation per viewport: the rail on desktop, the compact
      // header plus bottom bar below it.
      const desktop = viewport.width >= 1024;
      const rail = page.locator('.arena-sidebar');
      await expect(rail).toHaveCount(desktop ? 1 : 0);
      await expect(page.locator('.arena-topbar')).toHaveCount(desktop ? 1 : 0);
      if (desktop) {
        await expect(page.locator('.site-header')).toHaveCount(0);
        await expect(rail.locator('[data-destination="dashboard"]')).toHaveAttribute(
          'aria-current',
          'page',
        );
        await expect(rail.locator('.sidebar-brand img[src*="hnefatafl-knot"]')).toHaveCount(1);
        await expect(rail.locator('.sidebar-brand')).toContainText('HNEFATAFL');
        if (appearance.rail) {
          await expect(rail).toHaveCSS('background-color', appearance.rail);
          const width = await rail.evaluate((node) => node.getBoundingClientRect().width);
          expect(width, `${appearance.id} rail width`).toBeGreaterThanOrEqual(230);
          expect(width, `${appearance.id} rail width`).toBeLessThanOrEqual(281);
        }
      } else {
        await expect(page.locator('.site-header')).toHaveCount(1);
        // Tablets keep the header navigation; phones use the bottom bar.
        await expect(
          page.locator('.site-header nav:visible, .workspace-bottom-nav:visible'),
        ).toHaveCount(1);
      }

      // The same dashboard information architecture in every appearance: intro,
      // your-turn hero, four standing cards, games that need you, profile column
      // and the standings card. Only colour, ornament and fonts may differ.
      await expect(page.locator('.dashboard-intro')).toBeVisible();
      await expect(page.locator('.dashboard-intro-title')).toContainText(
        'Your place at the table.',
      );
      await expect(page.locator('.arena-hero')).toBeVisible();
      await expect(page.locator('.dashboard-stat')).toHaveCount(4);
      await expect(page.locator('.dashboard-needs-you')).toBeVisible();
      await expect(page.locator('.dashboard-profile')).toBeVisible();
      await expect(page.locator('.leaderboard-compact')).toBeVisible();
      if (viewport.width >= 1200) {
        // The reference two-column split: main content left, profile column right.
        // Narrower desktops stack both columns for readability.
        const main = await page.locator('.dashboard-main').boundingBox();
        const aside = await page.locator('.dashboard-aside').boundingBox();
        expect(main!.x, `${appearance.id} main column`).toBeLessThan(aside!.x);
      }
      // The theme texture/ornament layers are decorative and must never intercept
      // a pointer or cover real copy.
      expect(
        await page.evaluate(
          () => getComputedStyle(document.querySelector('.arena-hero')!, '::after').pointerEvents,
        ),
      ).toBe('none');

      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      await page.screenshot({
        path: `${output}/${appearance.id}-${viewport.name}.png`,
        fullPage: true,
      });
    }
  });

  test(`captures the full ${appearance.id} dashboard`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: value }));
    }, appearance.id);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    await expect(page.locator('.dashboard-layout')).toBeVisible();
    await page.screenshot({
      path: `${output}/${appearance.id}-dashboard-full.png`,
      fullPage: true,
    });
  });
}
