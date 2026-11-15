import type { Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { expect, test } from './auth-fixture';
import { workshopFixture } from './workshop-fixtures';
function contrast(foreground: string, background: string) {
  const luminance = (color: string) => {
    const channels = color
      .match(/[\d.]+/g)!
      .slice(0, 3)
      .map(Number)
      .map((n) => n / 255)
      .map((n) => (n <= 0.03928 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4));
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}
const themes = ['kings-table', 'iron-ash', 'saga-hall', 'classic-table'];
const output = 'test-results/workshop-consolidation';
const jobs = Array.from({ length: 57 }, (_, index) => ({
  ...workshopFixture(
    `page-${String(index).padStart(3, '0')}`,
    2,
    index === 0 ? 'running' : 'completed',
  ),
  name: `Strategist ${String(index).padStart(3, '0')}`,
  created_at: '2026-01-01T00:00:00Z',
}));
async function prepare(page: Page, theme: string, language: string, training: boolean) {
  await page.route(
    training ? /\/api\/training\/jobs(?:\?.*)?$/ : /\/api\/tournaments(?:\?.*)?$/,
    (route) => route.fulfill({ json: training ? jobs : [] }),
  );
  // One initializer per page: locale scripts must never race each other.
  await page.addInitScript(
    ({ theme, language }) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ version: 2, uiTheme: theme }));
      localStorage.setItem('hnefatafl-language', language);
    },
    { theme, language },
  );
  mkdirSync(output, { recursive: true });
}
async function appearance(page: Page, theme: string, language: string, width: number) {
  await expect(page.locator('html')).toHaveAttribute('lang', language);
  await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
  if (width === 1920 || width === 3840)
    await expect(page.locator('html')).toHaveAttribute(
      'data-density',
      width === 3840 ? 'large' : 'default',
    );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}
test.describe('Workshop visual acceptance', () => {
  test.skip(
    ({ isMobile }) => isMobile,
    'Explicit CSS viewport matrix runs in Chromium; mobile interaction regressions run separately.',
  );
  for (const theme of themes) {
    for (const language of ['en', 'de']) {
      for (const width of [320, 390, 768, 1024, 1366, 1920, 3840]) {
        test(`workshop ${theme} ${language} ${width}px`, async ({ page }) => {
          await prepare(page, theme, language, true);
          await page.setViewportSize({
            width,
            height: width === 3840 ? 2160 : width === 1920 ? 1080 : 900,
          });
          await page.goto('/training');
          await expect(page.locator('.workshop-heading h2')).toBeVisible();
          await expect(page.locator('.training-job-dashboard')).toBeVisible();
          await appearance(page, theme, language, width);
          expect(await page.locator('.jobs-list .job-row').count()).toBe(20);
          expect(await page.locator('.mobile-job-selector option').count()).toBeLessThanOrEqual(21);
          const colors = await page
            .locator('.job-row')
            .first()
            .evaluate((node) => ({
              text: getComputedStyle(node.querySelector('small')!).color,
              background: getComputedStyle(node).backgroundColor,
            }));
          expect(contrast(colors.text, colors.background)).toBeGreaterThanOrEqual(4.5);
          await page.screenshot({
            path: `${output}/${theme}-${language}-${width}.png`,
            fullPage: width < 1920,
            animations: 'disabled',
          });
        });
      }
      for (const width of [320, 768, 1920, 3840]) {
        test(`AI comparison ${theme} ${language} ${width}px`, async ({ page }) => {
          await prepare(page, theme, language, false);
          await page.setViewportSize({
            width,
            height: width === 3840 ? 2160 : width === 1920 ? 1080 : 900,
          });
          await page.goto('/tournaments');
          await expect(page.locator('.workshop-comparison-intro h2')).toBeVisible();
          await expect(page.locator('.tournament-history-empty')).toBeVisible();
          await appearance(page, theme, language, width);
          const colors = await page
            .locator('.workshop-heading > p:not(.arena-kicker)')
            .evaluate((node) => ({
              color: getComputedStyle(node).color,
              background: getComputedStyle(node.closest('.arena-surface')!).backgroundColor,
            }));
          expect(contrast(colors.color, colors.background)).toBeGreaterThanOrEqual(4.5);
          await page.screenshot({
            path: `${output}/comparisons-${theme}-${language}-${width}.png`,
            fullPage: width < 1920,
            animations: 'disabled',
          });
        });
      }
    }
    test(`workshop ${theme} preserves keyboard selection and task links`, async ({ page }) => {
      await prepare(page, theme, 'en', true);
      await page.setViewportSize({ width: 1920, height: 1080 });
      await page.goto('/training');
      const nav = page.locator('.training-job-navigator');
      await nav.getByRole('button', { name: 'Next job page' }).click();
      await nav.getByRole('button', { name: 'Next job page' }).click();
      await expect(nav.locator('.job-row')).toHaveCount(17);
      const last = nav.locator('.job-row').last();
      await last.focus();
      await page.keyboard.press('Enter');
      await expect(last).toHaveAttribute('aria-current', 'true');
      await expect(
        page.getByRole('heading', { name: 'Strategist 056', exact: true }),
      ).toBeVisible();
      await nav.getByRole('button', { name: 'Previous job page' }).click();
      const show = nav.getByRole('button', { name: 'Show selected job' });
      await show.focus();
      await page.keyboard.press('Enter');
      await expect(nav.locator('[data-job-id="page-056"]')).toHaveAttribute('aria-current', 'true');
      await page
        .getByRole('navigation', { name: 'Workshop tasks' })
        .getByRole('link', { name: 'Model collection' })
        .click();
      await expect(page.locator('#model-collection')).toBeVisible();
      await page
        .getByRole('navigation', { name: 'Workshop tasks' })
        .getByRole('link', { name: 'Remote workers' })
        .click();
      await expect(page.locator('#workshop-workers')).toBeVisible();
    });
  }
});
