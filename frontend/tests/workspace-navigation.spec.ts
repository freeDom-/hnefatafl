import { mkdirSync } from 'node:fs';
import type { Page } from '@playwright/test';
import { expect, test } from './auth-fixture';
import { isCompactShell, openDestination, primaryNavigation } from './play-helpers';

const headerNav = '.site-header nav';
const bottomNav = '.workspace-bottom-nav';

/** Computed visibility of both primary navigations, not just DOM presence. */
async function navigationState(page: Page) {
  return page.evaluate(
    ([header, bottom]) => {
      const read = (selector: string) => {
        const node = document.querySelector(selector);
        if (!node) return { present: false, display: 'absent', visible: false };
        const style = getComputedStyle(node);
        const box = node.getBoundingClientRect();
        return {
          present: true,
          display: style.display,
          visible: style.visibility !== 'hidden' && box.width > 0 && box.height > 0,
        };
      };
      return { header: read(header), bottom: read(bottom) };
    },
    [headerNav, bottomNav],
  );
}

/** Reserved space below the page content, in CSS pixels. */
function reservedBottomSpace(page: Page) {
  return page.evaluate(() => parseFloat(getComputedStyle(document.body).paddingBottom));
}

test.describe('one primary navigation per viewport', () => {
  for (const viewport of [
    { name: 'portrait phone', width: 390, height: 844 },
    { name: 'narrow phone', width: 320, height: 568 },
  ]) {
    test(`shows only the compact bottom navigation on a ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto('/dashboard');
      await expect(
        page.getByRole('heading', { name: 'The Great Hall', exact: true }),
      ).toBeVisible();
      expect(await isCompactShell(page)).toBe(true);
      expect(await navigationState(page)).toEqual({
        header: { present: true, display: 'none', visible: false },
        bottom: { present: true, display: 'flex', visible: true },
      });
      await expect(page.locator(headerNav)).toBeHidden();
      await expect(page.locator(bottomNav)).toBeVisible();
      await expect(primaryNavigation(page)).toHaveCount(1);
      // `display: none` (not visibility/opacity) keeps the hidden header
      // navigation out of the tab order.
      expect(
        await page.evaluate(() => {
          document.querySelector<HTMLElement>('.site-header nav button')!.focus();
          return document.querySelector('.site-header nav')!.contains(document.activeElement);
        }),
      ).toBe(false);
      expect(await reservedBottomSpace(page)).toBe(56);
    });
  }

  test('shows only the header navigation on a tablet', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'The Great Hall', exact: true })).toBeVisible();
    expect(await isCompactShell(page)).toBe(false);
    expect(await navigationState(page)).toEqual({
      header: { present: true, display: 'flex', visible: true },
      bottom: { present: true, display: 'none', visible: false },
    });
    await expect(page.locator(headerNav)).toBeVisible();
    await expect(page.locator(bottomNav)).toBeHidden();
    await expect(primaryNavigation(page)).toHaveCount(1);
    expect(await reservedBottomSpace(page)).toBe(0);
  });

  test.describe('short landscape phone', () => {
    // An 844×390 phone landscape only takes the compact shell with a coarse pointer.
    test.use({ hasTouch: true });

    test('shows only the compact bottom navigation', async ({ page }) => {
      await page.setViewportSize({ width: 844, height: 390 });
      await page.goto('/dashboard');
      await expect(
        page.getByRole('heading', { name: 'The Great Hall', exact: true }),
      ).toBeVisible();
      expect(await isCompactShell(page)).toBe(true);
      expect(await navigationState(page)).toEqual({
        header: { present: true, display: 'none', visible: false },
        bottom: { present: true, display: 'flex', visible: true },
      });
      await expect(page.locator(headerNav)).toBeHidden();
      await expect(page.locator(bottomNav)).toBeVisible();
      await expect(primaryNavigation(page)).toHaveCount(1);
      // Short landscape reserves the smaller bar, so the board keeps its room.
      expect(await reservedBottomSpace(page)).toBe(48);
    });
  });
});

test('the compact bottom navigation reaches every destination in both workspaces', async ({
  page,
}) => {
  // Deterministic Play entry: with no saved games, Play settles on its own route
  // instead of racing an auto-resume from a game another test left behind.
  await page.route('**/api/personal/**', (route) => route.fulfill({ json: { games: [] } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/dashboard');
  await expect(page.locator(bottomNav)).toBeVisible();

  for (const [destination, path] of [
    ['play', null],
    ['statistics', '/statistics'],
    ['leaderboard', '/leaderboard'],
    ['archive', '/archive'],
    ['dashboard', '/dashboard'],
  ] as const) {
    await openDestination(page, destination);
    if (path) await expect(page).toHaveURL(new RegExp(`${path}$`));
    else await expect(page).not.toHaveURL(/\/dashboard$/);
    await expect(
      page.locator(bottomNav).locator(`[data-destination="${destination}"]`),
    ).toHaveAttribute('aria-current', 'page');
  }

  await page.getByRole('button', { name: 'Workshop', exact: true }).click();
  await expect(page).toHaveURL(/\/training$/);
  await expect(page.locator(bottomNav).locator('[data-destination="training"]')).toHaveAttribute(
    'aria-current',
    'page',
  );
  for (const [destination, url] of [
    ['tournaments', /\/tournaments$/],
    ['model-collection', /\/training#model-collection$/],
    ['workshop-workers', /\/training#workshop-workers$/],
  ] as const) {
    await openDestination(page, destination);
    await expect(page).toHaveURL(url);
    await expect(
      page.locator(bottomNav).locator(`[data-destination="${destination}"]`),
    ).toHaveAttribute('aria-current', 'page');
  }
});

test('keeps a workshop section anchor on the route that owns it', async ({ page }) => {
  await page.goto('/training#model-collection');
  await expect(
    primaryNavigation(page).locator('[data-destination="model-collection"]'),
  ).toHaveAttribute('aria-current', 'page');
  await expect(
    primaryNavigation(page).locator('[data-destination="training"]'),
  ).not.toHaveAttribute('aria-current', 'page');
  // The deep link still opens and focuses its section.
  await expect(page.locator('#model-collection')).toBeFocused();

  await page.goto('/training#workshop-workers');
  await expect(
    primaryNavigation(page).locator('[data-destination="workshop-workers"]'),
  ).toHaveAttribute('aria-current', 'page');
  await expect(
    primaryNavigation(page).locator('[data-destination="training"]'),
  ).not.toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#workshop-workers')).toBeFocused();

  await page.goto('/training');
  await expect(primaryNavigation(page).locator('[data-destination="training"]')).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(
    primaryNavigation(page).locator('[data-destination="model-collection"]'),
  ).not.toHaveAttribute('aria-current', 'page');

  // The same anchor on a different parent route keeps that parent active.
  await page.goto('/tournaments#model-collection');
  await expect(primaryNavigation(page).locator('[data-destination="tournaments"]')).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(
    primaryNavigation(page).locator('[data-destination="model-collection"]'),
  ).not.toHaveAttribute('aria-current', 'page');
});

test('reserves the bottom strip only where a bottom navigation exists', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ['/dashboard', '/statistics', '/training']) {
    await page.goto(path);
    await expect(page.locator(bottomNav), path).toBeVisible();
    expect(await reservedBottomSpace(page), path).toBe(56);
  }

  // Signed out there is no bottom navigation, so no strip may be reserved.
  await page.context().clearCookies();
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'HNEFATAFL' })).toBeVisible();
  await expect(page.locator(bottomNav)).toHaveCount(0);
  expect(await reservedBottomSpace(page)).toBe(0);
});

test('keeps a phone dialog action above the compact bar', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/dashboard');
  await page.getByRole('button', { name: 'Play vs AI', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Begin a new saga' });
  await expect(setup).toBeVisible();
  // Playwright refuses a click the fixed bar would intercept.
  await setup.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(setup).toHaveCount(0);
});

// --- Responsive navigation-label geometry and live language switching ---------
//
// The approved labels are longer than the names they replace, especially in
// German, and the compact bar ellipsises while the rail and header wrap. A
// page-level `scrollWidth` check cannot see an individual label being clipped or
// two controls colliding, so these tests measure each control's own box against
// its label box and its navigation container.

const navOutput = 'test-results/navigation-labels';
test.beforeAll(() => mkdirSync(navOutput, { recursive: true }));

type NavKind = 'sidebar' | 'header' | 'compact';
type NavLanguage = 'en' | 'de';
type NavRect = { x: number; y: number; width: number; height: number };
type LabelBox = {
  destination: string;
  text: string;
  rect: NavRect;
  scrollWidth: number;
  clientWidth: number;
  scrollHeight: number;
  clientHeight: number;
  whiteSpace: string;
  textOverflow: string;
};
type GroupBox = { text: string; rect: NavRect; scrollWidth: number; clientWidth: number };
type NavProbe = { kind: NavKind; rect: NavRect; controls: LabelBox[]; groups: GroupBox[] };

const APPROVED_LABELS: Record<NavKind, Record<NavLanguage, Record<string, string>>> = {
  sidebar: {
    en: {
      dashboard: 'The Great Hall',
      play: 'Battlefield',
      statistics: 'Statistics',
      leaderboard: 'Leaderboard',
      'player-tournaments': 'Player tournaments',
      archive: 'Replays & analysis',
      portraits: 'Hall of Renown',
    },
    de: {
      dashboard: 'Die große Halle',
      play: 'Schlachtfeld',
      statistics: 'Statistik',
      leaderboard: 'Rangliste',
      'player-tournaments': 'Spielerturniere',
      archive: 'Wiederholungen & Analyse',
      portraits: 'Halle des Ruhms',
    },
  },
  header: {
    en: {
      dashboard: 'The Great Hall',
      play: 'Battlefield',
      statistics: 'Statistics',
      leaderboard: 'Leaderboard',
      archive: 'Hall of records',
    },
    de: {
      dashboard: 'Die große Halle',
      play: 'Schlachtfeld',
      statistics: 'Statistik',
      leaderboard: 'Rangliste',
      archive: 'Halle der Aufzeichnungen',
    },
  },
  compact: {
    en: {
      dashboard: 'Great Hall',
      play: 'Battlefield',
      statistics: 'Stats',
      leaderboard: 'Trials',
      archive: 'Replays',
    },
    de: {
      dashboard: 'Große Halle',
      play: 'Schlachtfeld',
      statistics: 'Statistik',
      leaderboard: 'Wettstreit',
      archive: 'Wiederholungen',
    },
  },
};

async function probePrimaryNavigation(page: Page): Promise<NavProbe[]> {
  return page.evaluate(() => {
    const visible = (node: HTMLElement) => {
      const style = getComputedStyle(node);
      const box = node.getBoundingClientRect();
      return (
        style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0
      );
    };
    const rect = (node: Element) => {
      const box = node.getBoundingClientRect();
      return { x: box.x, y: box.y, width: box.width, height: box.height };
    };
    return Array.from(
      document.querySelectorAll<HTMLElement>(
        '.arena-sidebar nav, .site-header nav, .workspace-bottom-nav',
      ),
    )
      .filter(visible)
      .map((nav) => ({
        kind: nav.closest('.arena-sidebar')
          ? ('sidebar' as const)
          : nav.closest('.site-header')
            ? ('header' as const)
            : ('compact' as const),
        rect: rect(nav),
        controls: Array.from(nav.querySelectorAll<HTMLElement>('a, button')).map((control) => {
          const label = Array.from(control.querySelectorAll<HTMLElement>('span')).find(
            (span) =>
              !span.classList.contains('sidebar-chevron') &&
              !span.classList.contains('sidebar-soon') &&
              !span.classList.contains('sr-only'),
          );
          const style = label ? getComputedStyle(label) : null;
          return {
            destination: control.dataset.destination ?? '',
            text: (label?.textContent ?? '').trim(),
            rect: rect(control),
            scrollWidth: label?.scrollWidth ?? 0,
            clientWidth: label?.clientWidth ?? 0,
            scrollHeight: label?.scrollHeight ?? 0,
            clientHeight: label?.clientHeight ?? 0,
            whiteSpace: style?.whiteSpace ?? 'normal',
            textOverflow: style?.textOverflow ?? 'clip',
          };
        }),
        groups: Array.from(nav.querySelectorAll<HTMLElement>('.sidebar-group-label')).map(
          (group) => ({
            text: (group.textContent ?? '').trim(),
            rect: rect(group),
            scrollWidth: group.scrollWidth,
            clientWidth: group.clientWidth,
          }),
        ),
      }));
  });
}

function overlapArea(a: NavRect, b: NavRect) {
  const width = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
  const height = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
  return width * height;
}

async function expectUsableNavigation(page: Page, language: NavLanguage, context: string) {
  const probes = await probePrimaryNavigation(page);
  expect(probes, `${context}: exactly one visible primary navigation`).toHaveLength(1);
  const nav = probes[0];
  const viewport = page.viewportSize()!;

  // The navigation itself stays inside the viewport and keeps real geometry.
  expect(nav.rect.width, `${context}: nav width`).toBeGreaterThan(0);
  expect(nav.rect.height, `${context}: nav height`).toBeGreaterThan(0);
  expect(nav.rect.x, `${context}: nav left edge`).toBeGreaterThanOrEqual(-1);
  expect(nav.rect.x + nav.rect.width, `${context}: nav right edge`).toBeLessThanOrEqual(
    viewport.width + 1,
  );

  // Every approved destination exposes its full accessible name even when the
  // visible label is shortened or ellipsised.
  for (const [destination, name] of Object.entries(APPROVED_LABELS[nav.kind][language]))
    await expect(
      primaryNavigation(page).locator(`[data-destination="${destination}"]`),
      `${context}: ${destination} accessible name`,
    ).toHaveAccessibleName(name);

  if (nav.kind === 'sidebar') {
    const group = language === 'de' ? 'Prüfungen des Mutes' : 'Trials of Valor';
    expect(
      nav.controls.some((control) => control.text === group),
      `${context}: ${group} group label`,
    ).toBe(true);
  }

  for (const control of nav.controls) {
    const label = control.text || control.destination;
    expect(control.rect.width, `${context}: ${label} width`).toBeGreaterThan(0);
    expect(control.rect.height, `${context}: ${label} height`).toBeGreaterThan(0);
    expect(control.rect.x, `${context}: ${label} left edge`).toBeGreaterThanOrEqual(nav.rect.x - 1);
    expect(
      control.rect.x + control.rect.width,
      `${context}: ${label} right edge`,
    ).toBeLessThanOrEqual(nav.rect.x + nav.rect.width + 1);
    // The rail scrolls vertically, so only its horizontal containment is fixed.
    if (nav.kind !== 'sidebar') {
      expect(control.rect.y, `${context}: ${label} top edge`).toBeGreaterThanOrEqual(
        nav.rect.y - 1,
      );
      expect(
        control.rect.y + control.rect.height,
        `${context}: ${label} bottom edge`,
      ).toBeLessThanOrEqual(nav.rect.y + nav.rect.height + 1);
    }
    if (control.scrollWidth > control.clientWidth + 1)
      expect(control.textOverflow, `${context}: ${label} is clipped without an ellipsis`).toBe(
        'ellipsis',
      );
    if (control.whiteSpace !== 'nowrap') {
      expect(
        control.scrollWidth,
        `${context}: ${label} wraps without horizontal overflow`,
      ).toBeLessThanOrEqual(control.clientWidth + 1);
      expect(
        control.scrollHeight,
        `${context}: ${label} grows to fit wrapped text`,
      ).toBeLessThanOrEqual(control.clientHeight + 1);
    }
  }

  for (let i = 0; i < nav.controls.length; i += 1)
    for (let j = i + 1; j < nav.controls.length; j += 1)
      expect(
        overlapArea(nav.controls[i].rect, nav.controls[j].rect),
        `${context}: "${nav.controls[i].text}" overlaps "${nav.controls[j].text}"`,
      ).toBeLessThanOrEqual(1);

  for (const group of nav.groups) {
    expect(group.rect.width, `${context}: group "${group.text}" width`).toBeGreaterThan(0);
    expect(
      group.scrollWidth,
      `${context}: group "${group.text}" horizontal clipping`,
    ).toBeLessThanOrEqual(group.clientWidth + 1);
  }

  // The active route keeps exactly one visible indicator.
  await expect(
    primaryNavigation(page).locator('[aria-current="page"]'),
    `${context}: active indicator`,
  ).toHaveAttribute('data-destination', 'dashboard');

  // Every visible control is keyboard reachable with a non-empty accessible name.
  const controls = primaryNavigation(page).locator('a, button');
  for (let index = 0; index < (await controls.count()); index += 1) {
    const control = controls.nth(index);
    if (!(await control.isVisible())) continue;
    await control.focus();
    await expect(control, `${context}: control ${index} focus`).toBeFocused();
    await expect(control, `${context}: control ${index} name`).toHaveAccessibleName(/\S/);
  }

  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    `${context}: page horizontal overflow`,
  ).toBe(true);
}

const NAV_THEMES = ['kings-table', 'iron-ash', 'saga-hall', 'classic-table'] as const;
const NAV_DENSITIES = ['default', 'compact', 'large', 'default'] as const;

async function setNavigationAppearance(page: Page, theme: string, density: string) {
  await page.addInitScript(
    ({ uiTheme, density: value }) =>
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme, density: value })),
    { uiTheme: theme, density },
  );
}

test.describe('responsive navigation labels', () => {
  for (const viewport of [
    { name: '320-phone', width: 320, height: 568 },
    { name: '360-phone', width: 360, height: 640 },
    { name: '390-phone', width: 390, height: 844 },
    { name: '768-tablet', width: 768, height: 1024 },
    { name: '1440-desktop', width: 1440, height: 900 },
  ]) {
    for (const [index, theme] of NAV_THEMES.entries()) {
      test(`${viewport.name} ${theme}: labels stay visible and named in English and German`, async ({
        page,
      }, info) => {
        test.skip(info.project.name === 'mobile', 'Explicit viewports; the matrix runs once.');
        test.setTimeout(120000);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await setNavigationAppearance(page, theme, NAV_DENSITIES[index]);
        await page.goto('/dashboard');
        const language = page.locator('.language-select select');
        await language.selectOption('en');
        await expectUsableNavigation(page, 'en', `${viewport.name}-${theme}-en`);
        await language.selectOption('de');
        await expectUsableNavigation(page, 'de', `${viewport.name}-${theme}-de`);
        if (index === 0)
          await primaryNavigation(page).screenshot({
            path: `${navOutput}/${viewport.name}-de.png`,
          });
        await language.selectOption('en');
        await expectUsableNavigation(page, 'en', `${viewport.name}-${theme}-en-restored`);
      });
    }
  }

  test.describe('short landscape phone', () => {
    test.use({ hasTouch: true });
    for (const [index, theme] of NAV_THEMES.entries()) {
      test(`${theme}: compact labels stay usable in English and German`, async ({ page }, info) => {
        test.skip(info.project.name === 'mobile', 'Explicit viewports; the matrix runs once.');
        test.setTimeout(120000);
        await page.setViewportSize({ width: 844, height: 390 });
        await setNavigationAppearance(page, theme, NAV_DENSITIES[index]);
        await page.goto('/dashboard');
        const language = page.locator('.language-select select');
        await language.selectOption('en');
        await expectUsableNavigation(page, 'en', `short-landscape-${theme}-en`);
        await language.selectOption('de');
        await expectUsableNavigation(page, 'de', `short-landscape-${theme}-de`);
        if (index === 0)
          await primaryNavigation(page).screenshot({
            path: `${navOutput}/short-landscape-de.png`,
          });
        await language.selectOption('en');
        await expectUsableNavigation(page, 'en', `short-landscape-${theme}-en-restored`);
      });
    }
  });
});
