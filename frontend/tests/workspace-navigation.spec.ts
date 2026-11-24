import { mkdirSync } from 'node:fs';
import type { Page } from '@playwright/test';
import onlineFixture from '../src/onlineFixture.testdata.json' with { type: 'json' };
import { expect, test } from './auth-fixture';
import { historicalGame } from './historical-fixture';
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

/** Fixed navigation geometry, its rows and its reserved page clearance. */
function bottomNavigationMetrics(page: Page) {
  return page.evaluate(() => {
    const nav = document.querySelector<HTMLElement>('.workspace-bottom-nav');
    if (!nav) return null;
    const rect = nav.getBoundingClientRect();
    const links = Array.from(nav.querySelectorAll<HTMLElement>('a'));
    const rowHeights = Array.from(
      new Set(links.map((link) => Math.round(link.getBoundingClientRect().height * 100) / 100)),
    );
    return {
      height: rect.height,
      bottom: rect.bottom,
      reserved: parseFloat(getComputedStyle(document.body).paddingBottom),
      rows: new Set(links.map((link) => Math.round(link.getBoundingClientRect().top))).size,
      rowHeights,
      minTarget: Math.min(...links.map((link) => link.getBoundingClientRect().height)),
    };
  });
}

test.describe('one primary navigation per viewport', () => {
  for (const viewport of [
    { name: 'portrait phone', width: 390, height: 844, wrap: 'nowrap', reserved: 56 },
    { name: 'narrow phone', width: 320, height: 568, wrap: 'wrap', reserved: 89 },
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
      expect(
        await page.locator(bottomNav).evaluate((node) => getComputedStyle(node).flexWrap),
      ).toBe(viewport.wrap);
      // `display: none` (not visibility/opacity) keeps the hidden header
      // navigation out of the tab order.
      expect(
        await page.evaluate(() => {
          document.querySelector<HTMLElement>('.site-header nav button')!.focus();
          return document.querySelector('.site-header nav')!.contains(document.activeElement);
        }),
      ).toBe(false);
      expect(await reservedBottomSpace(page)).toBe(viewport.reserved);
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
    ['online', '/online'],
    ['leagues', '/leagues'],
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
type NavDensity = 'default' | 'compact' | 'large';
type NavRect = { x: number; y: number; width: number; height: number };
type LabelBox = {
  destination: string;
  /** Visible label text; decorative descendants such as chevrons are excluded. */
  text: string;
  /** The interactive control's own box, used for geometry and overlap checks. */
  rect: NavRect;
  /** The measured rendered label: a nested label element or a direct text Range. */
  labelRect: NavRect;
  scrollWidth: number;
  clientWidth: number;
  scrollHeight: number;
  clientHeight: number;
  whiteSpace: string;
  textOverflow: string;
  /** True when the label is a nested element, false for a direct text node. */
  nested: boolean;
};
type GroupBox = { text: string; rect: NavRect; scrollWidth: number; clientWidth: number };
type NavProbe = { kind: NavKind; rect: NavRect; controls: LabelBox[]; groups: GroupBox[] };
type NavViewport = { name: string; width: number; height: number; kind: NavKind };

const APPROVED_LABELS: Record<NavKind, Record<NavLanguage, Record<string, string>>> = {
  sidebar: {
    en: {
      dashboard: 'The Great Hall',
      play: 'Battlefield',
      online: 'Online Play',
      leagues: 'Leagues',
      statistics: 'Statistics',
      leaderboard: 'Leaderboard',
      'player-tournaments': 'Player tournaments',
      archive: 'Hall of records',
      portraits: 'Hall of Renown',
    },
    de: {
      dashboard: 'Die große Halle',
      play: 'Schlachtfeld',
      online: 'Online spielen',
      leagues: 'Ligen',
      statistics: 'Statistik',
      leaderboard: 'Rangliste',
      'player-tournaments': 'Spielerturniere',
      archive: 'Halle der Aufzeichnungen',
      portraits: 'Halle des Ruhms',
    },
  },
  header: {
    en: {
      dashboard: 'The Great Hall',
      play: 'Battlefield',
      online: 'Online Play',
      leagues: 'Leagues',
      statistics: 'Statistics',
      leaderboard: 'Leaderboard',
      archive: 'Hall of records',
    },
    de: {
      dashboard: 'Die große Halle',
      play: 'Schlachtfeld',
      online: 'Online spielen',
      leagues: 'Ligen',
      statistics: 'Statistik',
      leaderboard: 'Rangliste',
      archive: 'Halle der Aufzeichnungen',
    },
  },
  compact: {
    en: {
      dashboard: 'Great Hall',
      play: 'Battlefield',
      online: 'Online',
      leagues: 'Leagues',
      statistics: 'Stats',
      leaderboard: 'Trials',
      archive: 'Replays',
    },
    de: {
      dashboard: 'Große Halle',
      play: 'Schlachtfeld',
      online: 'Online',
      leagues: 'Ligen',
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
    // Chevrons, "Soon" badges, external-link arrows and screen-reader-only text
    // are not the destination label and must not be measured as one.
    const isDecorative = (element: Element) =>
      element.classList.contains('sidebar-chevron') ||
      element.classList.contains('sidebar-soon') ||
      element.classList.contains('sidebar-external') ||
      element.classList.contains('sr-only') ||
      element.getAttribute('aria-hidden') === 'true';

    // A nested label element (desktop rail, compact bar). Icons are <svg> and
    // excluded; the longest remaining text is the destination name.
    const labelElement = (control: HTMLElement) => {
      const candidates = Array.from(
        control.querySelectorAll<HTMLElement>('span, small, strong, em, b, i'),
      ).filter(
        (element) => !isDecorative(element) && (element.textContent ?? '').trim().length > 0,
      );
      candidates.sort(
        (a, b) => (b.textContent ?? '').trim().length - (a.textContent ?? '').trim().length,
      );
      return candidates[0];
    };

    // Tablet header navigation renders the translated label directly in the
    // button, so measure its text nodes with a DOM Range instead of a child span.
    const directTextNodes = (control: HTMLElement) =>
      Array.from(control.childNodes).filter(
        (node): node is Text =>
          node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim().length > 0,
      );

    const measureControl = (control: HTMLElement): LabelBox => {
      const destination = control.dataset.destination ?? '';
      const controlRect = rect(control);
      const nested = labelElement(control);
      if (nested) {
        const style = getComputedStyle(nested);
        return {
          destination,
          text: (nested.textContent ?? '').trim(),
          rect: controlRect,
          labelRect: rect(nested),
          scrollWidth: nested.scrollWidth,
          clientWidth: nested.clientWidth,
          scrollHeight: nested.scrollHeight,
          clientHeight: nested.clientHeight,
          whiteSpace: style.whiteSpace,
          textOverflow: style.textOverflow,
          nested: true,
        };
      }
      const style = getComputedStyle(control);
      const nodes = directTextNodes(control);
      if (nodes.length === 0)
        return {
          destination,
          text: '',
          rect: controlRect,
          labelRect: { x: 0, y: 0, width: 0, height: 0 },
          scrollWidth: 0,
          clientWidth: 0,
          scrollHeight: 0,
          clientHeight: 0,
          whiteSpace: style.whiteSpace,
          textOverflow: style.textOverflow,
          nested: false,
        };
      const range = document.createRange();
      range.setStartBefore(nodes[0]);
      range.setEndAfter(nodes[nodes.length - 1]);
      const box = range.getBoundingClientRect();
      // For a direct text node the control is its label container; subtract the
      // control's padding so the painted text is compared with the usable area.
      return {
        destination,
        text: nodes
          .map((node) => node.textContent ?? '')
          .join('')
          .trim(),
        rect: controlRect,
        labelRect: { x: box.x, y: box.y, width: box.width, height: box.height },
        scrollWidth: box.width,
        clientWidth:
          control.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
        scrollHeight: box.height,
        clientHeight:
          control.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom),
        whiteSpace: style.whiteSpace,
        textOverflow: style.textOverflow,
        nested: false,
      };
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
        controls: Array.from(nav.querySelectorAll<HTMLElement>('a, button')).map(measureControl),
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

/**
 * The compact archive destination keeps its pre-existing German abbreviation
 * "Wiederholungen" (English "Replays"). It is the only destination whose compact
 * name does not fit a one-row control at larger interface scales, and it stays
 * deliberately ellipsised there: the accessible name is always the full "Hall of
 * records" / "Halle der Aufzeichnungen". Every other destination, including the
 * labels this change introduces, must fit. Keyed by `kind:language:destination`.
 */
const ACCEPTED_TRUNCATION = new Set(['compact:de:archive']);

// Every truncated label must stay readable: at least this fraction of its
// rendered text must remain visible. Platform font fallback (for example a
// Linux runner without Georgia) shifts word widths, so a bounded fraction is
// used instead of an exact fit.
const MIN_VISIBLE_LABEL_FRACTION = 0.85;
// The one accepted pre-existing compact label is a deliberate abbreviation and
// may keep a smaller, still-readable fraction.
const MIN_ACCEPTED_LABEL_FRACTION = 0.6;

async function expectUsableNavigation(
  page: Page,
  language: NavLanguage,
  context: string,
  expectedKind: NavKind,
) {
  const probes = await probePrimaryNavigation(page);
  expect(probes, `${context}: exactly one visible primary navigation`).toHaveLength(1);
  const nav = probes[0];
  expect(nav.kind, `${context}: expected primary navigation type`).toBe(expectedKind);
  const viewport = page.viewportSize()!;

  if (nav.kind === 'compact') {
    const metrics = await bottomNavigationMetrics(page);
    expect(metrics, `${context}: bottom navigation geometry`).not.toBeNull();
    // The fixed bar and the body clearance use the same responsive row sizing.
    expect(
      Math.abs(metrics!.height - metrics!.reserved),
      `${context}: reserved height matches the real navigation height`,
    ).toBeLessThanOrEqual(1);
    expect(
      Math.abs(metrics!.bottom - viewport.height),
      `${context}: navigation remains pinned to the viewport bottom`,
    ).toBeLessThanOrEqual(1);
  }

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
    const label = control.text || control.destination || '(unlabelled control)';
    // The label must exist and be measurable. A missing nested span or direct
    // text node fails here instead of silently substituting zero dimensions.
    expect(control.text.trim().length, `${context}: ${label} has label text`).toBeGreaterThan(0);
    expect(control.labelRect.width, `${context}: ${label} label width`).toBeGreaterThan(0);
    expect(control.labelRect.height, `${context}: ${label} label height`).toBeGreaterThan(0);
    expect(control.rect.width, `${context}: ${label} width`).toBeGreaterThan(0);
    expect(control.rect.height, `${context}: ${label} height`).toBeGreaterThan(0);
    expect(control.rect.x, `${context}: ${label} left edge`).toBeGreaterThanOrEqual(nav.rect.x - 1);
    expect(
      control.rect.x + control.rect.width,
      `${context}: ${label} right edge`,
    ).toBeLessThanOrEqual(nav.rect.x + nav.rect.width + 1);
    // The measured label sits inside the control that owns it.
    expect(control.labelRect.x, `${context}: ${label} label left`).toBeGreaterThanOrEqual(
      control.rect.x - 1,
    );
    expect(
      control.labelRect.x + control.labelRect.width,
      `${context}: ${label} label right`,
    ).toBeLessThanOrEqual(control.rect.x + control.rect.width + 1);
    expect(control.labelRect.y, `${context}: ${label} label top`).toBeGreaterThanOrEqual(
      control.rect.y - 1,
    );
    expect(
      control.labelRect.y + control.labelRect.height,
      `${context}: ${label} label bottom`,
    ).toBeLessThanOrEqual(control.rect.y + control.rect.height + 1);
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
    const overflow = control.scrollWidth - control.clientWidth;
    if (overflow > 1) {
      expect(control.textOverflow, `${context}: ${label} is clipped without an ellipsis`).toBe(
        'ellipsis',
      );
      // Ellipsis alone is not a licence to clip arbitrarily: a label must stay
      // readable, and only the one justified pre-existing compact name may keep
      // a smaller fraction.
      const accepted = ACCEPTED_TRUNCATION.has(`${nav.kind}:${language}:${control.destination}`);
      expect(
        control.clientWidth / control.scrollWidth,
        `${context}: ${label} visible fraction`,
      ).toBeGreaterThanOrEqual(accepted ? MIN_ACCEPTED_LABEL_FRACTION : MIN_VISIBLE_LABEL_FRACTION);
    }
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

  // The tablet header renders translated text directly in its buttons; make sure
  // that direct-text path is what produced the measurements above rather than a
  // silently empty nested-label lookup.
  if (expectedKind === 'header')
    expect(
      nav.controls.some((control) => !control.nested),
      `${context}: tablet header labels are measured from direct text nodes`,
    ).toBe(true);

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
  const visibleIndexes: number[] = [];
  for (let index = 0; index < (await controls.count()); index += 1) {
    const control = controls.nth(index);
    if (!(await control.isVisible())) continue;
    await control.focus();
    await expect(control, `${context}: control ${index} focus`).toBeFocused();
    await expect(control, `${context}: control ${index} name`).toHaveAccessibleName(/\S/);
    visibleIndexes.push(index);
  }

  // Sequential Tab reaches every visible control without leaving the navigation
  // before it has visited them all.
  if (visibleIndexes.length > 1) {
    await controls.nth(visibleIndexes[0]).focus();
    const reached = new Set<number>();
    for (let step = 0; step < visibleIndexes.length * 2 + 4; step += 1) {
      const activeIndex = await page.evaluate(() => {
        const active = document.activeElement as HTMLElement | null;
        const nav = active?.closest('.arena-sidebar nav, .site-header nav, .workspace-bottom-nav');
        if (!active || !nav) return null;
        return Array.from(nav.querySelectorAll('a, button')).indexOf(active);
      });
      if (activeIndex === null) break;
      reached.add(activeIndex);
      if (reached.size === visibleIndexes.length) break;
      await page.keyboard.press('Tab');
    }
    expect(reached.size, `${context}: sequential Tab reaches every navigation control`).toBe(
      visibleIndexes.length,
    );
  }

  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    `${context}: page horizontal overflow`,
  ).toBe(true);
}

test('compact navigation height tracks text resize and keeps Workshop unchanged', async ({
  page,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Explicit viewport; the matrix runs once.');
  test.setTimeout(180000);
  const compact = APPROVED_LABELS.compact;

  for (const width of [320, 360, 380, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 640 });
    await page.goto('/dashboard');
    await setNavigationDensity(page, 'default');
    for (const rootSize of [16, 24, 32]) {
      await page.evaluate((size) => {
        document.documentElement.style.fontSize = `${size}px`;
      }, rootSize);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      for (const language of ['en', 'de'] as const) {
        await page.locator('.language-select select').selectOption(language);
        const context = `${width}px-root-${rootSize}-${language}`;
        const nav = primaryNavigation(page);
        await expect(nav.locator('a'), `${context}: all Arena destinations`).toHaveCount(7);
        const metrics = await bottomNavigationMetrics(page);
        expect(metrics, `${context}: bottom navigation geometry`).not.toBeNull();
        expect(
          Math.abs(metrics!.height - metrics!.reserved),
          `${context}: navigation and reserved height stay synchronized`,
        ).toBeLessThanOrEqual(1);
        expect(metrics!.rows, `${context}: narrow Arena rows`).toBe(width <= 380 ? 2 : 1);
        expect(
          metrics!.rowHeights.every((height) => height >= 44),
          `${context}: navigation row heights`,
        ).toBe(true);
        expect(metrics!.minTarget, `${context}: touch target height`).toBeGreaterThanOrEqual(44);
        for (const [destination, name] of Object.entries(compact[language]))
          await expect(
            nav.locator(`[data-destination="${destination}"]`),
            `${context}: ${destination} accessible name`,
          ).toHaveAccessibleName(name);
        await expect(nav.locator('[aria-current="page"]')).toHaveAttribute(
          'data-destination',
          'dashboard',
        );
      }
    }
  }

  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto('/dashboard');
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '32px';
  });
  await expect(page.locator(bottomNav)).toBeHidden();
  expect(await reservedBottomSpace(page), 'tablet header navigation reserves no bottom strip').toBe(
    0,
  );
  await expect(primaryNavigation(page)).toBeVisible();

  // Workshop keeps its four-destination one-row layout. Its reserved height is
  // unchanged at normal text size and still follows enlarged text without overlap.
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto('/training');
  await setNavigationDensity(page, 'default');
  for (const rootSize of [16, 24, 32]) {
    await page.evaluate((size) => {
      document.documentElement.style.fontSize = `${size}px`;
    }, rootSize);
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    const context = `workshop-320px-root-${rootSize}`;
    const nav = primaryNavigation(page);
    await expect(nav.locator('a'), `${context}: Workshop destinations`).toHaveCount(4);
    const metrics = await bottomNavigationMetrics(page);
    expect(metrics, `${context}: Workshop navigation geometry`).not.toBeNull();
    expect(metrics!.rows, `${context}: Workshop stays on one row`).toBe(1);
    expect(metrics!.rowHeights, `${context}: Workshop row heights`).toHaveLength(1);
    expect(metrics!.minTarget, `${context}: Workshop touch target height`).toBeGreaterThanOrEqual(
      44,
    );
    expect(
      Math.abs(metrics!.height - metrics!.reserved),
      `${context}: Workshop navigation and clearance stay synchronized`,
    ).toBeLessThanOrEqual(1);
    if (rootSize === 16)
      expect(metrics!.reserved, `${context}: unchanged normal clearance`).toBe(56);
  }
  const workshopNav = await primaryNavigation(page).boundingBox();
  expect(workshopNav, 'workshop navigation box').not.toBeNull();
  expect(workshopNav!.x, 'workshop navigation left edge').toBeGreaterThanOrEqual(-1);
  expect(workshopNav!.x + workshopNav!.width, 'workshop navigation right edge').toBeLessThanOrEqual(
    (await page.viewportSize())!.width + 1,
  );
});

test('keeps enlarged navigation clear of single-player and online game screens', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Explicit viewport; the matrix runs once.');
  test.setTimeout(180000);
  const singleResponse = await historicalGame(request, {
    data: { ruleset: 'tablut', mode: 'human-human' },
  });
  expect(singleResponse.ok()).toBe(true);
  const single = (await singleResponse.json()) as { id: string };
  const online = onlineFixture.active as { id: string };

  await page.route('**/api/online/games?*', (route) =>
    route.fulfill({ json: { games: [], limit: 50, offset: 0, section: 'mine' } }),
  );
  await page.route('**/api/online/games/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === `/api/online/games/${online.id}`) return route.fulfill({ json: online });
    return route.continue();
  });
  await page.route('**/api/online/availability', (route) =>
    route.fulfill({ json: { enabled: true, eligible: true } }),
  );

  for (const [name, path] of [
    ['single-player', `/games/${single.id}`],
    ['online', `/online/games/${online.id}`],
  ] as const) {
    for (const rootSize of [24, 32]) {
      await page.setViewportSize({ width: 320, height: 720 });
      await page.goto(path);
      await page.evaluate((size) => {
        document.documentElement.style.fontSize = `${size}px`;
      }, rootSize);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      await expect(page.getByRole('grid'), `${name}-${rootSize}: board`).toBeVisible();
      const layout = await page.evaluate(() => {
        const nav = document.querySelector('.workspace-bottom-nav')!.getBoundingClientRect();
        const board = document.querySelector('.board')!.getBoundingClientRect();
        const inspector = document.querySelector('.mobile-inspector')!.getBoundingClientRect();
        const intersects = (a: DOMRect, b: DOMRect) =>
          a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
        return {
          navHeight: nav.height,
          navTop: nav.top,
          reserved: parseFloat(getComputedStyle(document.body).paddingBottom),
          boardVisible: board.width > 0 && board.height > 0,
          boardBottom: board.bottom,
          inspectorBottom: inspector.bottom,
          navOverlapsBoard: intersects(nav, board),
          navOverlapsInspector: intersects(nav, inspector),
        };
      });
      const context = `${name}-320px-root-${rootSize}`;
      expect(
        Math.abs(layout.navHeight - layout.reserved),
        `${context}: navigation and gameplay clearance stay synchronized`,
      ).toBeLessThanOrEqual(1);
      expect(layout.boardVisible, `${context}: board remains visible`).toBe(true);
      expect(layout.navOverlapsBoard, `${context}: navigation does not cover the board`).toBe(
        false,
      );
      expect(
        layout.navOverlapsInspector,
        `${context}: navigation does not cover the inspector`,
      ).toBe(false);
      expect(layout.boardBottom, `${context}: board ends above the navigation`).toBeLessThanOrEqual(
        layout.navTop + 1,
      );
      expect(
        layout.inspectorBottom,
        `${context}: inspector ends above the navigation`,
      ).toBeLessThanOrEqual(layout.navTop + 1);
    }
  }
});

const NAV_THEMES = ['kings-table', 'iron-ash', 'saga-hall', 'classic-table'] as const;
// The original one-scale-per-theme pairing keeps Default and Compact coverage.
const NAV_DENSITIES: NavDensity[] = ['default', 'compact', 'large', 'default'];

const NAV_VIEWPORTS: readonly NavViewport[] = [
  { name: '320-phone', width: 320, height: 568, kind: 'compact' },
  { name: '360-phone', width: 360, height: 640, kind: 'compact' },
  { name: '390-phone', width: 390, height: 844, kind: 'compact' },
  { name: '768-tablet', width: 768, height: 1024, kind: 'header' },
  { name: '1440-desktop', width: 1440, height: 900, kind: 'sidebar' },
];

// Highest-risk scale/viewport pairs: the Large scale grows every label, and the
// German compact names are longest on a 320px phone and the tablet header.
const LARGE_RISK_VIEWPORTS: readonly NavViewport[] = [
  { name: '320-phone', width: 320, height: 568, kind: 'compact' },
  { name: '768-tablet', width: 768, height: 1024, kind: 'header' },
];

async function setNavigationAppearance(page: Page, theme: string, density: NavDensity) {
  await page.addInitScript(
    ({ uiTheme, density: value }) =>
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme, density: value })),
    { uiTheme: theme, density },
  );
}

/** Apply a density through the same persisted appearance key the app reads. */
async function setNavigationDensity(page: Page, density: NavDensity) {
  await page.evaluate((value) => {
    const stored = localStorage.getItem('hnefatafl-appearance');
    const appearance = stored ? JSON.parse(stored) : {};
    appearance.uiTheme = 'kings-table';
    appearance.density = value;
    localStorage.setItem('hnefatafl-appearance', JSON.stringify(appearance));
  }, density);
  await page.reload();
  await expectNavigationAppearance(page, 'kings-table', density);
  await expect(primaryNavigation(page)).toBeVisible();
}

/** Rendered compact-navigation font size for one destination label. */
async function compactLabelFontSize(page: Page, destination: string) {
  return primaryNavigation(page)
    .locator(`[data-destination="${destination}"] > span`)
    .evaluate((node) => parseFloat(getComputedStyle(node).fontSize));
}

/** The applied theme/density must match the request before geometry is read. */
async function expectNavigationAppearance(page: Page, theme: string, density: NavDensity) {
  const applied = await page.evaluate(() => ({
    theme: document.documentElement.dataset.uiTheme,
    density: document.documentElement.dataset.density,
  }));
  expect(applied.theme, 'data-ui-theme').toBe(theme);
  expect(applied.density, 'data-density').toBe(density);
}

/** A marker that a language switch reloaded the document would clear. */
async function markDocument(page: Page) {
  await page.evaluate(() => {
    (window as Window & { __navLoadMarker?: number }).__navLoadMarker = 1;
  });
}

async function expectNoReload(page: Page, context: string) {
  expect(
    await page.evaluate(() => (window as Window & { __navLoadMarker?: number }).__navLoadMarker),
    `${context}: language switching did not reload the application`,
  ).toBe(1);
}

test.describe('responsive navigation labels', () => {
  for (const viewport of NAV_VIEWPORTS) {
    for (const [index, theme] of NAV_THEMES.entries()) {
      const density = NAV_DENSITIES[index];
      test(`${viewport.name} ${theme} (${density}): labels stay visible and named in English and German`, async ({
        page,
      }, info) => {
        test.skip(info.project.name === 'mobile', 'Explicit viewports; the matrix runs once.');
        test.setTimeout(120000);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await setNavigationAppearance(page, theme, density);
        await page.goto('/dashboard');
        await expectNavigationAppearance(page, theme, density);
        await markDocument(page);
        const language = page.locator('.language-select select');
        await language.selectOption('en');
        await expectUsableNavigation(page, 'en', `${viewport.name}-${theme}-en`, viewport.kind);
        await language.selectOption('de');
        await expectUsableNavigation(page, 'de', `${viewport.name}-${theme}-de`, viewport.kind);
        await expectNoReload(page, `${viewport.name}-${theme}`);
        if (index === 0)
          await primaryNavigation(page).screenshot({
            path: `${navOutput}/${viewport.name}-de.png`,
          });
        await language.selectOption('en');
        await expectUsableNavigation(
          page,
          'en',
          `${viewport.name}-${theme}-en-restored`,
          viewport.kind,
        );
      });
    }
  }

  test.describe('large interface scale high-risk combinations', () => {
    for (const viewport of LARGE_RISK_VIEWPORTS) {
      for (const theme of NAV_THEMES) {
        test(`${viewport.name} ${theme} (large): labels stay visible and named in English and German`, async ({
          page,
        }, info) => {
          test.skip(info.project.name === 'mobile', 'Explicit viewports; the matrix runs once.');
          test.setTimeout(120000);
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
          await setNavigationAppearance(page, theme, 'large');
          await page.goto('/dashboard');
          await expectNavigationAppearance(page, theme, 'large');
          await markDocument(page);
          const language = page.locator('.language-select select');
          await language.selectOption('en');
          await expectUsableNavigation(
            page,
            'en',
            `${viewport.name}-${theme}-large-en`,
            viewport.kind,
          );
          await language.selectOption('de');
          await expectUsableNavigation(
            page,
            'de',
            `${viewport.name}-${theme}-large-de`,
            viewport.kind,
          );
          await expectNoReload(page, `${viewport.name}-${theme}-large`);
          await language.selectOption('en');
          await expectUsableNavigation(
            page,
            'en',
            `${viewport.name}-${theme}-large-en-restored`,
            viewport.kind,
          );
        });
      }
    }
  });

  test.describe('short landscape phone', () => {
    test.use({ hasTouch: true });
    for (const [index, theme] of NAV_THEMES.entries()) {
      test(`${theme}: compact labels stay usable in English and German`, async ({ page }, info) => {
        test.skip(info.project.name === 'mobile', 'Explicit viewports; the matrix runs once.');
        test.setTimeout(120000);
        await page.setViewportSize({ width: 844, height: 390 });
        await setNavigationAppearance(page, theme, NAV_DENSITIES[index]);
        await page.goto('/dashboard');
        await expectNavigationAppearance(page, theme, NAV_DENSITIES[index]);
        const language = page.locator('.language-select select');
        await language.selectOption('en');
        await expectUsableNavigation(page, 'en', `short-landscape-${theme}-en`, 'compact');
        await language.selectOption('de');
        await expectUsableNavigation(page, 'de', `short-landscape-${theme}-de`, 'compact');
        if (index === 0)
          await primaryNavigation(page).screenshot({
            path: `${navOutput}/short-landscape-de.png`,
          });
        await language.selectOption('en');
        await expectUsableNavigation(page, 'en', `short-landscape-${theme}-en-restored`, 'compact');
      });
    }
  });
});
