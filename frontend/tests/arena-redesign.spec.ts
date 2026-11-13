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

      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      await page.screenshot({
        path: `${output}/${appearance.id}-${viewport.name}.png`,
        fullPage: true,
      });
    }
  });
}
