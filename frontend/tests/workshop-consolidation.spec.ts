import type { Locator, Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { expect, test } from './auth-fixture';
import { workshopComparisonFixture, workshopFixture } from './workshop-fixtures';
import { translate, type Language } from '../src/i18n';
import { openDestination } from './play-helpers';
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

async function dialogGeometry(page: Page, dialog: Locator) {
  await expect(dialog).toBeVisible();
  const box = await dialog.boundingBox();
  const viewport = page.viewportSize()!;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
  await page.keyboard.press('Tab');
  expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
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

test.describe('Interactive Workshop visual acceptance', () => {
  test.skip(
    ({ isMobile }) => isMobile,
    'The explicit desktop/phone matrix runs once; mobile navigation runs below.',
  );
  for (const theme of themes) {
    for (const language of ['en', 'de'] as Language[]) {
      for (const width of [390, 1920]) {
        test(`interactive comparisons ${theme} ${language} ${width}px`, async ({ page }) => {
          await prepare(page, theme, language, false);
          await page.setViewportSize({ width, height: width === 1920 ? 1080 : 844 });
          const text = (value: string) => translate(value, {}, language);
          const completed = workshopComparisonFixture();
          const active = {
            ...workshopComparisonFixture('visual-active', 'running'),
            name: 'Northern challenge',
          };
          let running = false;
          await page.route(/\/api\/tournaments(?:\?.*)?$/, (route) =>
            route.fulfill({ json: running ? [active, completed] : [completed] }),
          );
          await page.route(/\/api\/tournaments\/visual-(?:completed|active)$/, (route) =>
            route.fulfill({
              json: route.request().url().endsWith('visual-active') ? active : completed,
            }),
          );
          await page.goto('/tournaments');
          const detail = page.getByRole('region', { name: text('Tournament results') });
          await expect(page.locator('.tournament-saved')).toHaveCount(1);
          await expect(
            detail.getByRole('heading', { name: completed.name, exact: true }),
          ).toBeVisible();
          await expect(detail.locator('.tournament-status')).toHaveText(text('Completed'));
          await expect(detail.locator('.tournament-standings')).toContainText('Raven');
          await expect(detail.locator('.tournament-win-matrix')).toBeVisible();
          await expect(
            detail.getByRole('button', { name: text('Export results'), exact: true }),
          ).toBeEnabled();
          await appearance(page, theme, language, width);
          await page.screenshot({
            path: `${output}/results-${theme}-${language}-${width}.png`,
            fullPage: true,
            animations: 'disabled',
          });

          for (const title of ['Set up a tournament', 'Fixed-role comparison']) {
            const trigger = page
              .getByRole('button', {
                name: text(title === 'Set up a tournament' ? 'New tournament' : title),
                exact: true,
              })
              .first();
            await trigger.click();
            const dialog = page.getByRole('dialog', { name: text(title), exact: true });
            await dialogGeometry(page, dialog);
            await expect(
              dialog.getByRole('textbox', { name: text('Tournament name'), exact: true }),
            ).toBeEditable();
            await expect(
              dialog.getByLabel(text('Randomize starting positions'), { exact: true }),
            ).toBeEnabled();
            const importConfig = dialog.getByLabel(text('Import configuration'), { exact: true });
            await expect(importConfig).toBeEnabled();
            await expect(importConfig.locator('..')).toBeVisible();
            if (title === 'Set up a tournament') {
              await expect(
                dialog.getByLabel(text('Games per battlefield'), { exact: true }),
              ).toBeEditable();
              await expect(dialog.locator('.tournament-player')).toHaveCount(2);
            } else {
              await expect(
                dialog.getByRole('group', { name: text('Attackers'), exact: true }),
              ).toBeVisible();
              await expect(
                dialog.getByRole('group', { name: text('Defenders'), exact: true }),
              ).toBeVisible();
              await expect(
                dialog.getByLabel(text('Number of games'), { exact: true }),
              ).toBeEditable();
            }
            await page.screenshot({
              path: `${output}/${title === 'Set up a tournament' ? 'standard' : 'fixed'}-${theme}-${language}-${width}.png`,
              animations: 'disabled',
            });
            await page.keyboard.press('Escape');
            await expect(dialog).not.toBeVisible();
            await expect(trigger).toBeFocused();
          }

          running = true;
          await page.reload();
          await expect(page.locator('.tournament-saved')).toHaveCount(2);
          await expect(
            detail.getByRole('heading', { name: active.name, exact: true }),
          ).toBeVisible();
          await expect(detail.locator('.tournament-status')).toHaveText(text('In progress'));
          await expect(detail.getByRole('progressbar')).toHaveAttribute('value', '1');
          await expect(
            detail.getByRole('button', { name: text('Stop tournament'), exact: true }),
          ).toBeEnabled();
          await appearance(page, theme, language, width);
          await page.screenshot({
            path: `${output}/active-${theme}-${language}-${width}.png`,
            fullPage: true,
            animations: 'disabled',
          });
        });

        test(`interactive training ${theme} ${language} ${width}px`, async ({ page }) => {
          await prepare(page, theme, language, true);
          await page.setViewportSize({ width, height: width === 1920 ? 1080 : 844 });
          const text = (value: string) => translate(value, {}, language);
          const job = workshopFixture('completed', 60, 'completed');
          await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
            route.fulfill({ json: [job] }),
          );
          await page.route(/\/api\/training\/jobs\/completed\/resume$/, (route) =>
            route.fulfill({
              json: {
                job_id: job.id,
                checkpoint: 'last.pt',
                completed_iteration: 60,
                ruleset: 'tablut',
                channels: 48,
                blocks: 3,
                optimizer: 'adamw',
                model_name: 'Bjorn',
                can_resume: true,
                config: job.config,
              },
            }),
          );
          await page.goto('/training');
          await expect(page.locator('.training-job-dashboard')).toBeVisible();
          const resume = page.getByRole('button', {
            name: text('Resume from checkpoint'),
            exact: true,
          });
          await expect(resume).toBeEnabled();
          await expect(
            page.getByRole('button', { name: text('Export job'), exact: true }),
          ).toBeEnabled();
          const importJob = page.getByLabel(text('Import job'), { exact: true });
          await expect(importJob).toBeEnabled();
          await expect(importJob.locator('..')).toBeVisible();
          await resume.click();
          const dialog = page.getByRole('dialog', { name: text('Resume training'), exact: true });
          await dialogGeometry(page, dialog);
          const resumeImport = dialog.getByLabel(text('Import config.json'), { exact: true });
          await expect(resumeImport).toBeEnabled();
          await expect(resumeImport.locator('..')).toBeVisible();
          await page.screenshot({
            path: `${output}/resume-${theme}-${language}-${width}.png`,
            animations: 'disabled',
          });
          // Help buttons consume Escape while their tooltip is open. Move to
          // the dialog's close control before testing dialog dismissal itself.
          await dialog
            .getByRole('button', { name: text('Close resume training'), exact: true })
            .focus();
          await page.keyboard.press('Escape');
          await expect(dialog).not.toBeVisible();
          await expect(resume).toBeFocused();

          await page
            .getByRole('navigation', { name: text('Workshop tasks') })
            .getByRole('button', { name: text('Train or tune'), exact: true })
            .click();
          const creation = page.locator('#training-creation');
          const advanced = creation.getByRole('button', {
            name: new RegExp(text('Advanced configuration')),
          });
          await advanced.click();
          await expect(advanced).toHaveAttribute('aria-expanded', 'true');
          const decay = creation.getByLabel(text('Weight regularization'), { exact: true });
          await decay.fill('0.002');
          await expect(decay).toHaveValue('0.002');
          const creationImport = creation.getByLabel(text('Import config.json'), { exact: true });
          await expect(creationImport).toBeEnabled();
          await expect(creationImport.locator('..')).toBeVisible();
          await appearance(page, theme, language, width);
          await page.screenshot({
            path: `${output}/creation-${theme}-${language}-${width}.png`,
            fullPage: true,
            animations: 'disabled',
          });
          await creation
            .getByRole('button', { name: text('Tune parameters'), exact: true })
            .click();
          const trials = creation.locator('[data-training-field="study.trials"]');
          await trials.fill('5');
          await expect(trials).toHaveValue('5');
          await expect(decay).toHaveValue('0.002');
          await expect(advanced).toHaveAttribute('aria-expanded', 'true');
          await page.screenshot({
            path: `${output}/tuning-${theme}-${language}-${width}.png`,
            fullPage: true,
            animations: 'disabled',
          });
        });
      }
    }
  }
});

test('Workshop task and shell links reopen sections and navigate across routes without reloading', async ({
  page,
}) => {
  await prepare(page, 'kings-table', 'en', true);
  await page.route(/\/api\/tournaments(?:\?.*)?$/, (route) => route.fulfill({ json: [] }));
  let documents = 0;
  page.on('request', (request) => {
    if (request.resourceType() === 'document') documents++;
  });
  await page.goto('/training');
  await expect(page.locator('.training-job-dashboard')).toBeVisible();
  for (const [label, id] of [
    ['Model collection', 'model-collection'],
    ['Remote workers', 'workshop-workers'],
  ]) {
    const section = page.locator(`#${id}`);
    const link = page
      .getByRole('navigation', { name: 'Workshop tasks' })
      .getByRole('link', { name: label, exact: true });
    const toggle = page.locator(`button[aria-controls="${id}"]`);
    await link.focus();
    await page.keyboard.press('Enter');
    await expect(section).toBeVisible();
    await expect(section).toBeFocused();
    await toggle.click();
    await expect(section).not.toBeVisible();
    await link.click();
    await expect(section).toBeVisible();
    await expect(section).toBeFocused();
    await toggle.click();
    await openDestination(page, id);
    await expect(section).toBeVisible();
    await expect(section).toBeFocused();
  }
  await page
    .getByRole('navigation', { name: 'Workshop tasks' })
    .getByRole('link', { name: 'AI comparisons', exact: true })
    .click();
  await expect(page).toHaveURL(/\/tournaments$/);
  await expect(page.locator('.workshop-comparison-intro h2')).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/training#workshop-workers$/);
  await expect(page.locator('#workshop-workers')).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/tournaments$/);
  await page.getByRole('link', { name: 'Return to the AI workshop', exact: true }).click();
  await expect(page).toHaveURL(/\/training$/);
  await expect(page.locator('.training-job-dashboard')).toBeVisible();
  expect(documents).toBe(1);
});

test('polling reconciles job pages without restoring stale pages or resetting the selected history', async ({
  page,
}) => {
  await page.clock.install();
  await prepare(page, 'kings-table', 'en', true);
  let currentJobs = jobs;
  await page.route(/\/api\/training\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: currentJobs }),
  );
  await page.goto('/training');
  const nav = page.locator('.training-job-navigator');
  await expect(page.getByRole('heading', { name: 'Strategist 000', exact: true })).toBeVisible();
  const learning = page.getByRole('tab', { name: 'Learning', exact: true });
  await learning.click();
  await page.getByLabel('Iteration range', { exact: true }).selectOption('10');
  await page.getByText('View graph data', { exact: true }).click();
  const history = page.getByRole('table', { name: 'Training history data', exact: true });
  await expect(history.getByRole('row')).toHaveCount(3);
  await nav.getByRole('button', { name: 'Next job page' }).click();
  await nav.getByRole('button', { name: 'Next job page' }).click();
  for (const [count, pagination] of [
    [21, 'Page 2 of 2'],
    [57, 'Page 2 of 3'],
    [57, 'Page 2 of 3'],
    [1, null],
    [57, 'Page 1 of 3'],
  ] as const) {
    currentJobs = jobs.slice(0, count).map((job) => ({ ...job }));
    await page.clock.runFor(3000);
    if (pagination) await expect(nav).toContainText(pagination);
    else await expect(nav.getByRole('group', { name: 'Training job pages' })).toHaveCount(0);
    await expect(learning).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByLabel('Iteration range', { exact: true })).toHaveValue('10');
    await expect(history.getByRole('row')).toHaveCount(3);
    await expect(page.getByRole('heading', { name: 'Strategist 000', exact: true })).toBeVisible();
    expect(await nav.locator('.mobile-job-selector option').count()).toBeLessThanOrEqual(21);
  }
});

for (const permissions of [['training'], ['tournaments'], ['training', 'tournaments']]) {
  test(`Workshop route links respect ${permissions.join(' and ')} permissions`, async ({
    page,
  }) => {
    await prepare(page, 'kings-table', 'en', true);
    await page.route('**/api/auth/me', async (route) => {
      const response = await route.fetch();
      const session = await response.json();
      await route.fulfill({
        json: {
          ...session,
          account: { ...session.account, permissions: ['play', 'profile', ...permissions] },
        },
      });
    });
    await page.route(/\/api\/tournaments(?:\?.*)?$/, (route) => route.fulfill({ json: [] }));
    if (permissions.includes('training')) {
      await page.goto('/training');
      const tasks = page.getByRole('navigation', { name: 'Workshop tasks' });
      await expect(tasks).toBeVisible();
      await expect(
        tasks.getByRole('link', { name: 'Model collection', exact: true }),
      ).toBeVisible();
      await expect(tasks.getByRole('link', { name: 'Remote workers', exact: true })).toBeVisible();
      await expect(tasks.getByRole('link', { name: 'AI comparisons', exact: true })).toHaveCount(
        permissions.includes('tournaments') ? 1 : 0,
      );
      if (permissions.includes('tournaments'))
        await tasks.getByRole('link', { name: 'AI comparisons', exact: true }).click();
    } else await page.goto('/tournaments');
    if (permissions.includes('tournaments')) {
      await expect(page.locator('.workshop-comparison-intro h2')).toBeVisible();
      await expect(page.getByRole('button', { name: 'New tournament', exact: true })).toBeEnabled();
      await expect(
        page.getByRole('link', { name: 'Return to the AI workshop', exact: true }),
      ).toHaveCount(permissions.includes('training') ? 1 : 0);
    }
  });
}
