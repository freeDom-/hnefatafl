import { expect, test, type Page } from '@playwright/test';
import type { Density } from '../src/appearanceConfig';
import type { Language } from '../src/i18n';
import {
  applyAppearance,
  expectAppliedAppearance,
  type AppearanceChoice,
} from './appearance-helpers';

const password = 'personal-browser-fixture-password';
const LABELS: Record<
  Language,
  {
    hall: string;
    deeds: string;
    earnedTier: RegExp;
    progress: RegExp;
    nextMilestone: string;
    rewards: string;
    threshold: RegExp;
    highest: RegExp;
  }
> = {
  en: {
    hall: 'Hall of Renown',
    deeds: 'Deeds of Valor',
    earnedTier: /^Earned tier: \d+ \/ \d+$/,
    progress: /^Recorded progress: \d+$/,
    nextMilestone: 'Next milestone:',
    rewards: 'Milestone rewards',
    threshold: /threshold/i,
    highest: /highest achieved/i,
  },
  de: {
    hall: 'Halle des Ruhms',
    deeds: 'Heldentaten',
    earnedTier: /^Verdiente Stufe: \d+ \/ \d+$/,
    progress: /^Erfasster Fortschritt: \d+$/,
    nextMilestone: 'Nächster Meilenstein:',
    rewards: 'Meilenstein-Belohnungen',
    threshold: /Schwelle/,
    highest: /höchste erreichte/i,
  },
};

type ScaleCase = {
  name: string;
  viewport: { width: number; height: number };
  appearance: AppearanceChoice;
  expectedDensity: Density;
  capture?: boolean;
};

const SCALE_CASES: ScaleCase[] = [
  {
    name: '320-phone large German Saga Hall',
    viewport: { width: 320, height: 640 },
    appearance: { uiTheme: 'saga-hall', density: 'large', language: 'de' },
    expectedDensity: 'large',
    capture: true,
  },
  {
    name: '390-phone compact English Iron & Ash',
    viewport: { width: 390, height: 844 },
    appearance: { uiTheme: 'iron-ash', density: 'compact', language: 'en' },
    expectedDensity: 'compact',
  },
  {
    name: '768-tablet default German King’s Table',
    viewport: { width: 768, height: 1024 },
    appearance: { uiTheme: 'kings-table', density: 'default', language: 'de' },
    expectedDensity: 'default',
  },
  {
    name: '1280-desktop compact English Classic Table',
    viewport: { width: 1280, height: 900 },
    appearance: { uiTheme: 'classic-table', density: 'compact', language: 'en' },
    expectedDensity: 'compact',
  },
  {
    name: '1920-desktop automatic default Saga Hall',
    viewport: { width: 1920, height: 1080 },
    appearance: { uiTheme: 'saga-hall', language: 'en' },
    expectedDensity: 'default',
  },
  {
    name: '2560-desktop explicit default Iron & Ash',
    viewport: { width: 2560, height: 1440 },
    appearance: { uiTheme: 'iron-ash', density: 'default', language: 'de' },
    expectedDensity: 'default',
  },
  {
    name: '3840-desktop automatic large King’s Table',
    viewport: { width: 3840, height: 2160 },
    appearance: { uiTheme: 'kings-table', language: 'en' },
    expectedDensity: 'large',
    capture: true,
  },
  {
    name: '3840-desktop explicit compact German Classic Table',
    viewport: { width: 3840, height: 2160 },
    appearance: { uiTheme: 'classic-table', density: 'compact', language: 'de' },
    expectedDensity: 'compact',
  },
];

async function login(page: Page) {
  await page.goto('/dashboard');
  await page.getByLabel('Username or email').fill('PersonalArcher');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'The Great Hall', exact: true })).toBeVisible();
}

test.beforeEach(async ({ request }) => {
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
});

test('achievement cards omit removed details across interface scales, themes and 4K', async ({
  page,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Explicit viewports cover the matrix once.');
  await login(page);
  const observedDensities = new Set<Density>();
  for (const current of SCALE_CASES) {
    await page.setViewportSize(current.viewport);
    await applyAppearance(page, current.appearance);
    await page.goto('/portraits');
    const density = await expectAppliedAppearance(page, current.appearance);
    expect(density, `${current.name}: production density resolution`).toBe(current.expectedDensity);
    observedDensities.add(density);
    const labels = LABELS[current.appearance.language];
    await expect(page.getByRole('heading', { name: labels.hall })).toBeVisible();
    await expect(page.getByRole('heading', { name: labels.deeds })).toBeVisible();
    const cards = page.locator('.portrait-achievement-list > li');
    await expect(cards.first()).toBeVisible();
    const card = page.locator('[data-family="rated-wins"]');
    await expect(card).toBeVisible();
    await expect(card.locator('p').first()).toHaveText(labels.earnedTier);
    await expect(card.locator('p').nth(1)).toHaveText(labels.progress);
    await expect(card).toContainText(labels.nextMilestone);
    await expect(card.getByText(labels.rewards)).toBeVisible();
    await expect(card).not.toContainText(labels.threshold);
    await expect(card).not.toContainText(labels.highest);
    await expect(
      page.getByRole('progressbar', { name: /(Achievement Score|Errungenschaftspunkte)/ }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: /(claim|collect|sammeln|abholen)/i }),
    ).toHaveCount(0);
    for (const achievement of await cards.all()) {
      await expect(achievement).not.toContainText(labels.threshold);
      await expect(achievement).not.toContainText(labels.highest);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    if (current.capture) {
      await page.locator('.portrait-achievements').screenshot({
        path: info.outputPath(`achievement-cards-${current.name.replaceAll(' ', '-')}.png`),
      });
    }
  }
  expect([...observedDensities].sort()).toEqual(['compact', 'default', 'large']);
});
