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
      await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
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
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
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
      await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
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
