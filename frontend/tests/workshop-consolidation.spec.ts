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
for (const theme of ['kings-table', 'iron-ash', 'saga-hall', 'classic-table']) {
  test(`workshop ${theme} keeps bounded jobs, readable metadata and all actions across layouts`, async ({
    page,
  }, info) => {
    test.skip(
      info.project.name !== 'chromium',
      'Explicit viewport matrix includes mobile layouts.',
    );
    const jobs = Array.from({ length: 57 }, (_, index) => ({
      ...workshopFixture(
        `page-${String(index).padStart(3, '0')}`,
        2,
        index === 0 ? 'running' : 'completed',
      ),
      name: `Strategist ${String(index).padStart(3, '0')}`,
      created_at: '2026-01-01T00:00:00Z',
    }));
    await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: jobs }));
    await page.addInitScript(
      (theme) => localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: theme })),
      theme,
    );
    const output = 'test-results/workshop-consolidation';
    mkdirSync(output, { recursive: true });
    for (const language of ['en', 'de']) {
      await page.addInitScript(
        (language) => localStorage.setItem('hnefatafl-language', language),
        language,
      );
      for (const width of [320, 390, 768, 1024, 1366, 1920, 3840]) {
        await page.setViewportSize({
          width,
          height: width === 3840 ? 2160 : width === 1920 ? 1080 : 900,
        });
        await page.goto('/training');
        if (width === 1920 || width === 3840)
          await expect(page.locator('html')).toHaveAttribute(
            'data-density',
            width === 3840 ? 'large' : 'default',
          );
        await expect(page.locator('.workshop-heading h2')).toBeVisible();
        await expect(page.locator('.training-job-dashboard')).toBeVisible();
        await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme);
        expect(await page.locator('.jobs-list .job-row').count()).toBe(20);
        expect(await page.locator('.mobile-job-selector option').count()).toBeLessThanOrEqual(21);
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        ).toBe(true);
        const colors = await page
          .locator('.job-row')
          .first()
          .evaluate((node) => ({
            text: getComputedStyle(node.querySelector('small')!).color,
            background: getComputedStyle(node).backgroundColor,
          }));
        expect(contrast(colors.text, colors.background)).toBeGreaterThanOrEqual(4.5);
        // Capture real UI surfaces; lazy-loaded content remains its own source.
        await page.screenshot({
          path: `${output}/${theme}-${language}-${width}.png`,
          fullPage: true,
        });
      }
    }
    await page.addInitScript(() => localStorage.setItem('hnefatafl-language', 'en'));
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
    await expect(page.getByRole('heading', { name: 'Strategist 056', exact: true })).toBeVisible();
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

for (const theme of ['kings-table', 'iron-ash', 'saga-hall', 'classic-table']) {
  test(`AI comparison ${theme} remains distinct, readable and responsive`, async ({
    page,
  }, info) => {
    test.skip(info.project.name !== 'chromium', 'Explicit responsive matrix runs once.');
    await page.route(/\/api\/tournaments(?:\?.*)?$/, (route) => route.fulfill({ json: [] }));
    await page.addInitScript(
      (theme) => localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: theme })),
      theme,
    );
    const output = 'test-results/workshop-consolidation';
    mkdirSync(output, { recursive: true });
    for (const language of ['en', 'de']) {
      await page.addInitScript(
        (language) => localStorage.setItem('hnefatafl-language', language),
        language,
      );
      for (const width of [320, 768, 1920, 3840]) {
        await page.setViewportSize({
          width,
          height: width === 3840 ? 2160 : width === 1920 ? 1080 : 900,
        });
        await page.goto('/tournaments');
        if (width === 1920 || width === 3840)
          await expect(page.locator('html')).toHaveAttribute(
            'data-density',
            width === 3840 ? 'large' : 'default',
          );
        await expect(page.locator('.workshop-comparison-intro h2')).toBeVisible();
        await expect(page.locator('.tournament-history-empty')).toBeVisible();
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        ).toBe(true);
        const colors = await page
          .locator('.workshop-heading > p:not(.arena-kicker)')
          .evaluate((node) => ({
            color: getComputedStyle(node).color,
            background: getComputedStyle(node.closest('.arena-surface')!).backgroundColor,
          }));
        expect(contrast(colors.color, colors.background)).toBeGreaterThanOrEqual(4.5);
        await page.screenshot({
          path: `${output}/comparisons-${theme}-${language}-${width}.png`,
          fullPage: true,
        });
      }
    }
  });
}
