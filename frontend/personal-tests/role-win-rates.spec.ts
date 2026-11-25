import { expect, test, type Locator, type Page } from '@playwright/test';
import type { Density } from '../src/appearanceConfig';
import type { Language } from '../src/i18n';
import type { PersonalStatistics } from '../src/personalTypes';
import {
  applyAppearance,
  expectAppliedAppearance,
  selectLanguage,
  type AppearanceChoice,
} from './appearance-helpers';
import { multiplayerStatistics, singleStatistics } from './statistics-fixture';

const password = 'personal-browser-fixture-password';
const LANGUAGE_LOCALE: Record<Language, string> = { en: 'en-GB', de: 'de-DE' };
const ROLE_LABELS: Record<
  Language,
  { overall: string; attacker: string; defender: string; timeout: string }
> = {
  en: {
    overall: 'Win rate',
    attacker: 'Win rate as attacker',
    defender: 'Win rate as defender',
    timeout: 'Timed-out games',
  },
  de: {
    overall: 'Siegquote',
    attacker: 'Siegquote als Angreifer',
    defender: 'Siegquote als Verteidiger',
    timeout: 'Spiele mit Zeitüberschreitung',
  },
};

type Viewport = { width: number; height: number };
type ScaleCase = {
  name: string;
  viewport: Viewport;
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
    capture: true,
  },
  {
    name: '768-tablet default German Classic Table',
    viewport: { width: 768, height: 1024 },
    appearance: { uiTheme: 'classic-table', density: 'default', language: 'de' },
    expectedDensity: 'default',
  },
  {
    name: '1280-desktop compact English King’s Table',
    viewport: { width: 1280, height: 900 },
    appearance: { uiTheme: 'kings-table', density: 'compact', language: 'en' },
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

const normalize = (value: string | null | undefined) => (value ?? '').replace(/\u00a0/g, ' ');

function percent(language: Language, value: number | null): string {
  if (value === null) return '—';
  return normalize(
    new Intl.NumberFormat(LANGUAGE_LOCALE[language], {
      style: 'percent',
      maximumFractionDigits: 1,
    }).format(value),
  );
}

async function login(page: Page) {
  await page.goto('/dashboard');
  await page.getByLabel('Username or email').fill('PersonalArcher');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'The Great Hall', exact: true })).toBeVisible();
}

async function installStatisticsRoutes(
  page: Page,
  statistics: { single: PersonalStatistics; online: PersonalStatistics },
) {
  await page.route(/\/api\/personal\/(single-player|online)\?/, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    const single = new URL(route.request().url()).pathname.endsWith('/single-player');
    await route.fulfill({
      response,
      json: { ...data, statistics: single ? statistics.single : statistics.online },
    });
  });
  await page.route(/\/api\/personal\/progress$/, (route) =>
    route.fulfill({
      json: {
        as_of: '2035-01-01T00:00:00Z',
        streaks: { rated_win_current: 0, rated_win_best: 0, by_ruleset: [] },
      },
    }),
  );
  await page.route(/\/api\/personal\/ratings\/[a-z]+\/history/, (route) => {
    const ruleset = /\/ratings\/([a-z]+)\//.exec(route.request().url())?.[1] ?? 'brandubh';
    return route.fulfill({
      json: {
        ruleset,
        policy_version: 1,
        current_rating: null,
        as_of: '2035-01-01T00:00:00Z',
        legacy_timestamp_gaps: false,
        items: [],
        next_cursor: null,
        limit: 100,
        matching: 0,
      },
    });
  });
}

async function captureCard(page: Page, card: Locator, path: string) {
  const viewport = page.viewportSize()!;
  if (viewport.height < 1600) await page.setViewportSize({ ...viewport, height: 1600 });
  await card.screenshot({ path });
  if (viewport.height < 1600) await page.setViewportSize(viewport);
}

async function statisticsCard(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  const card = page.locator('.statistics-ruleset-card').first();
  await expect(card).toBeVisible();
  await expect(card.locator('.statistics-headline-metric')).toHaveCount(3);
  return card;
}

type MetricGeometry = {
  value: string;
  label: string;
  metric: { left: number; right: number; top: number; bottom: number; width: number };
  valueBox: { left: number; right: number; top: number; bottom: number; width: number };
  labelBox: { left: number; right: number; top: number; bottom: number; width: number };
  valueScrollWidth: number;
  valueClientWidth: number;
  labelScrollWidth: number;
  labelClientWidth: number;
  labelFontSize: number;
  labelWhiteSpace: string;
  valueOverflow: string;
  valueTextOverflow: string;
};

async function metricGeometry(card: Locator): Promise<MetricGeometry[]> {
  return card.locator('.statistics-headline-metric').evaluateAll((nodes) => {
    const box = (node: Element) => {
      const rect = node.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
      };
    };
    return nodes.map((node) => {
      const value = node.querySelector('strong') as HTMLElement;
      const label = node.querySelector('span') as HTMLElement;
      const valueStyle = getComputedStyle(value);
      return {
        value: value.textContent ?? '',
        label: label.textContent ?? '',
        metric: box(node),
        valueBox: box(value),
        labelBox: box(label),
        valueScrollWidth: value.scrollWidth,
        valueClientWidth: value.clientWidth,
        labelScrollWidth: label.scrollWidth,
        labelClientWidth: label.clientWidth,
        labelFontSize: parseFloat(getComputedStyle(label).fontSize),
        labelWhiteSpace: getComputedStyle(label).whiteSpace,
        valueOverflow: valueStyle.overflow,
        valueTextOverflow: valueStyle.textOverflow,
      };
    });
  });
}

function expectInside(
  inner: { left: number; right: number; top: number; bottom: number },
  outer: { left: number; right: number; top: number; bottom: number },
  context: string,
) {
  expect(inner.left, `${context}: left edge`).toBeGreaterThanOrEqual(outer.left - 1);
  expect(inner.right, `${context}: right edge`).toBeLessThanOrEqual(outer.right + 1);
  expect(inner.top, `${context}: top edge`).toBeGreaterThanOrEqual(outer.top - 1);
  expect(inner.bottom, `${context}: bottom edge`).toBeLessThanOrEqual(outer.bottom + 1);
}

function overlaps(
  a: { left: number; right: number; top: number; bottom: number },
  b: { left: number; right: number; top: number; bottom: number },
) {
  return (
    a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1
  );
}

async function expectRoleHeadline(
  card: Locator,
  language: Language,
  expected: [number | null, number | null, number | null],
) {
  const labels = ROLE_LABELS[language];
  const metrics = await metricGeometry(card);
  expect(metrics).toHaveLength(3);
  expect(metrics.map((metric) => metric.label)).toEqual([
    labels.overall,
    labels.attacker,
    labels.defender,
  ]);
  expect(metrics.map((metric) => normalize(metric.value))).toEqual(
    expected.map((value) => percent(language, value)),
  );
  for (const [index, metric] of metrics.entries()) {
    const context = `${language} metric ${index} (${metric.label})`;
    expectInside(metric.valueBox, metric.metric, `${context} value`);
    expectInside(metric.labelBox, metric.metric, `${context} label`);
    expect(metric.valueBox.bottom, `${context}: value above label`).toBeLessThanOrEqual(
      metric.labelBox.top + 1,
    );
    expect(metric.valueScrollWidth, `${context}: value scroll width`).toBeLessThanOrEqual(
      metric.valueClientWidth + 1,
    );
    expect(metric.labelScrollWidth, `${context}: label scroll width`).toBeLessThanOrEqual(
      metric.labelClientWidth + 1,
    );
    expect(metric.labelFontSize, `${context}: readable label font`).toBeGreaterThanOrEqual(10.5);
    expect(metric.labelWhiteSpace, `${context}: labels can wrap`).not.toBe('nowrap');
    expect(metric.valueOverflow, `${context}: value overflow`).not.toBe('hidden');
    expect(metric.valueTextOverflow, `${context}: value truncation`).not.toBe('ellipsis');
  }
  for (let left = 0; left < metrics.length; left++) {
    for (let right = left + 1; right < metrics.length; right++) {
      expect(
        overlaps(metrics[left].metric, metrics[right].metric),
        `${language}: metrics ${left} and ${right} do not overlap`,
      ).toBe(false);
    }
  }
  return metrics;
}

async function expectElo(card: Locator, expected: string) {
  const elo = card.locator('.statistics-headline > small');
  await expect(elo).toBeVisible();
  await expect(elo).toHaveText(expected);
  const geometry = await card.evaluate((node) => {
    const headline = node.querySelector('.statistics-headline')!.getBoundingClientRect();
    const metrics = [...node.querySelectorAll('.statistics-headline-metric')].map((metric) =>
      metric.getBoundingClientRect(),
    );
    const elo = node.querySelector('.statistics-headline > small')!.getBoundingClientRect();
    return { headline, metrics, elo };
  });
  expectInside(geometry.elo, geometry.headline, 'Elo stays inside the headline');
  for (const [index, metric] of geometry.metrics.entries()) {
    expect(overlaps(geometry.elo, metric), `Elo does not overlap metric ${index}`).toBe(false);
  }
  expect(geometry.elo.top, 'Elo sits below the metric row').toBeGreaterThanOrEqual(
    Math.max(...geometry.metrics.map((metric) => metric.bottom)) - 1,
  );
}

async function expectNoPageOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}

async function expectUsableTable(card: Locator, language: Language) {
  const scroll = card.locator('.statistics-table-scroll').first();
  const table = scroll.locator('.statistics-table');
  await expect(table).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(3);
  await expect(table.locator('tbody tr').nth(1)).toContainText(
    language === 'de' ? 'Als Angreifer' : 'As attacker',
  );
  await expect(table.locator('tbody tr').nth(2)).toContainText(
    language === 'de' ? 'Als Verteidiger' : 'As defender',
  );
  expect(
    await scroll.evaluate((node) => getComputedStyle(node).overflowX),
    'the detailed table keeps its own horizontal scroll region',
  ).toBe('auto');
  await expect(scroll).toHaveAttribute('tabindex', '0');
  if (
    await table.evaluate(
      (node) => getComputedStyle(node.querySelector('tbody')!).display === 'grid',
    )
  ) {
    const overflowing = await table
      .locator('tbody td')
      .evaluateAll((nodes) =>
        nodes
          .filter((node) => node.scrollWidth > node.clientWidth + 1)
          .map((node) => (node.getAttribute('data-label') ?? '') + ':' + node.scrollWidth),
      );
    expect(
      overflowing,
      'mobile metric cards keep their labels and values inside each cell',
    ).toEqual([]);
  }
}

function singleRoleStatistics(
  overall: number | null,
  attacker: number | null,
  defender: number | null,
): PersonalStatistics {
  const base = singleStatistics.groups.find((group) => group.ruleset === 'brandubh')!;
  const empty = {
    played: 0,
    completed: 0,
    wins: 0,
    losses: 0,
    draws: 0,
    win_rate: null,
    average_game_length: null,
    average_game_length_unit: 'moves',
  };
  const completed = overall === null ? 0 : 1000;
  return {
    groups: [
      {
        ...base,
        agents: undefined,
        played: completed,
        completed,
        wins: overall === null ? 0 : 999,
        losses: overall === null ? 0 : 1,
        draws: 0,
        win_rate: overall,
        win_rate_attackers: attacker,
        win_rate_defenders: defender,
        by_side: {
          attackers:
            overall === null
              ? empty
              : {
                  ...empty,
                  played: completed,
                  completed,
                  wins: 999,
                  losses: 1,
                  win_rate: overall,
                  average_game_length: 20,
                },
          defenders: empty,
        },
      },
    ],
    coverage: { unattributed_games: 0 },
  };
}

test.beforeEach(async ({ request }) => {
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
});

test('role win rates fit their metric columns across themes, interface scales and 4K', async ({
  page,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Explicit viewports cover the matrix once.');
  await installStatisticsRoutes(page, {
    single: singleStatistics,
    online: multiplayerStatistics,
  });
  await login(page);
  const observedDensities = new Set<Density>();
  for (const current of SCALE_CASES) {
    await page.setViewportSize(current.viewport);
    await applyAppearance(page, current.appearance);
    await page.goto('/statistics?mode=single-player&ruleset=brandubh');
    const density = await expectAppliedAppearance(page, current.appearance);
    expect(density, `${current.name}: production density resolution`).toBe(current.expectedDensity);
    observedDensities.add(density);
    await expect(
      page.getByRole('heading', { name: /^(Your statistics|Deine Statistik)$/ }),
    ).toBeVisible();
    const card = await statisticsCard(page);
    await expectRoleHeadline(card, current.appearance.language, [0.2, 1, 0]);
    await expect(card.locator('.statistics-headline > small')).toHaveCount(0);
    await expectUsableTable(card, current.appearance.language);
    await expectNoPageOverflow(page);
    if (current.capture) {
      await captureCard(
        page,
        card,
        info.outputPath(`role-win-rates-${current.name.replaceAll(' ', '-')}.png`),
      );
    }
  }
  expect([...observedDensities].sort()).toEqual(['compact', 'default', 'large']);
});

test('fractional and unavailable role values keep locale formatting and fit at narrow large scale', async ({
  page,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Explicit viewports cover the matrix once.');
  const routes: { single: PersonalStatistics; online: PersonalStatistics } = {
    single: singleStatistics,
    online: multiplayerStatistics,
  };
  await installStatisticsRoutes(page, routes);
  await login(page);
  routes.single = singleRoleStatistics(0.999, 0.999, null);
  for (const current of [
    {
      name: 'English default',
      viewport: { width: 1280, height: 900 },
      appearance: { uiTheme: 'kings-table', density: 'default', language: 'en' } as const,
    },
    {
      name: 'German large phone',
      viewport: { width: 320, height: 640 },
      appearance: { uiTheme: 'saga-hall', density: 'large', language: 'de' } as const,
    },
  ]) {
    await page.setViewportSize(current.viewport);
    await applyAppearance(page, current.appearance);
    await page.goto('/statistics?mode=single-player&ruleset=brandubh');
    await expectAppliedAppearance(page, current.appearance);
    const card = await statisticsCard(page);
    await expectRoleHeadline(card, current.appearance.language, [0.999, 0.999, null]);
    await expectUsableTable(card, current.appearance.language);
    await expectNoPageOverflow(page);
    if (current.name === 'German large phone') {
      await captureCard(
        page,
        card,
        info.outputPath('role-win-rates-fractional-german-large-phone.png'),
      );
    }
  }
});

test('online role rates, Elo, timeouts and mode/ruleset changes remain current and localized', async ({
  page,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Explicit viewports cover the matrix once.');
  await installStatisticsRoutes(page, {
    single: singleStatistics,
    online: multiplayerStatistics,
  });
  await login(page);
  const choice: AppearanceChoice = {
    uiTheme: 'saga-hall',
    density: 'default',
    language: 'en',
  };
  await page.setViewportSize({ width: 1280, height: 900 });
  await applyAppearance(page, choice);
  await page.goto('/statistics?mode=multiplayer&ruleset=brandubh');
  await expectAppliedAppearance(page, choice);
  const card = await statisticsCard(page);
  await expectRoleHeadline(card, 'en', [0.6, 1, 0]);
  await expectElo(card, 'Elo 1537');
  await expect(card.getByText(/Timed-out games: 1/)).toBeVisible();
  await expectUsableTable(card, 'en');
  await expectNoPageOverflow(page);

  await page.getByLabel('Ruleset', { exact: true }).selectOption('tablut');
  await expect(page).toHaveURL(/ruleset=tablut/);
  const emptyCard = await statisticsCard(page);
  await expectRoleHeadline(emptyCard, 'en', [null, null, null]);
  await expect(emptyCard.locator('.statistics-headline > small')).toHaveCount(0);
  await expectNoPageOverflow(page);

  await page.getByLabel('Ruleset', { exact: true }).selectOption('brandubh');
  await expect(page).toHaveURL(/ruleset=brandubh/);
  const restored = await statisticsCard(page);
  await expectRoleHeadline(restored, 'en', [0.6, 1, 0]);
  await expectElo(restored, 'Elo 1537');

  await selectLanguage(page, 'de');
  await expectRoleHeadline(restored, 'de', [0.6, 1, 0]);
  await expectElo(restored, 'Elo 1537');
  await expect(restored.getByText(/Spiele mit Zeitüberschreitung: 1/)).toBeVisible();
  await expectNoPageOverflow(page);

  await page.getByRole('tab', { name: 'Einzelspieler', exact: true }).click();
  await expect(page).toHaveURL(/mode=single-player/);
  const singleCard = await statisticsCard(page);
  await expectRoleHeadline(singleCard, 'de', [0.2, 1, 0]);
  await expect(singleCard.locator('.statistics-headline > small')).toHaveCount(0);
  await expect(singleCard.getByText(/Spiele mit Zeitüberschreitung/)).toHaveCount(0);
  await expectNoPageOverflow(page);

  await page.getByRole('tab', { name: 'Mehrspieler', exact: true }).click();
  await expect(page).toHaveURL(/mode=multiplayer/);
  const onlineAgain = await statisticsCard(page);
  await expectRoleHeadline(onlineAgain, 'de', [0.6, 1, 0]);
  await expectElo(onlineAgain, 'Elo 1537');
  await expect(onlineAgain.getByText(/Spiele mit Zeitüberschreitung: 1/)).toBeVisible();
});
