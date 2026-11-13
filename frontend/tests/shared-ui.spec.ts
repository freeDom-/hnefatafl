import { expect, test } from './auth-fixture';
import { historicalGame } from './historical-fixture';

test('pages keep accessible titles and share navigation and section typography with play', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'chromium', 'Exercises desktop and phone widths in one session.');
  test.setTimeout(180000);
  const created = await historicalGame(request, {
    data: { ruleset: 'brandubh', mode: 'human-human' },
  });
  expect(created.ok()).toBe(true);
  const game = await created.json();
  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1568, height: 785 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    for (const density of ['compact', 'default', 'large']) {
      let reference: unknown;
      for (const path of [
        '/dashboard',
        '/statistics',
        '/leaderboard',
        '/archive',
        '/training',
        '/tournaments',
        `/games/${game.id}`,
      ]) {
        await page.goto(path);
        await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
        await page.locator('html').evaluate((node, value) => {
          node.dataset.density = value;
        }, density);
        // Exactly one primary navigation is visible: the desktop rail on wide
        // viewports, the header navigation on tablets, the compact bottom bar on
        // phones.
        await expect(
          page.locator(
            '.arena-sidebar nav:visible, .site-header nav:visible, .workspace-bottom-nav:visible',
          ),
        ).toHaveCount(1);
        const state = await page.evaluate(() => {
          const chrome =
            document.querySelector('.arena-sidebar') ?? document.querySelector('.site-header')!;
          const nav = chrome.querySelector('nav')!;
          const button = nav.querySelector('button, a')!;
          const style = getComputedStyle(button);
          const controls = chrome
            .querySelector('.sidebar-account, .header-controls')!
            .getBoundingClientRect();
          const brand = chrome
            .querySelector('.sidebar-brand-text, .brand > span:last-child')!
            .getBoundingClientRect();
          const title = document.querySelector('.page-heading')!.getBoundingClientRect();
          const intersects = (a: DOMRect, b: DOMRect) =>
            a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
          return {
            chrome: {
              height: chrome.getBoundingClientRect().height,
              menuTop: nav.getBoundingClientRect().top,
              font: style.fontFamily,
              size: style.fontSize,
              weight: style.fontWeight,
            },
            title: { width: title.width, height: title.height },
            width: document.documentElement.scrollWidth,
            chromeOverlap: intersects(brand, controls),
            headings: [...document.querySelectorAll('h2:not(.dialog-title)')]
              .filter(
                (node) =>
                  node.getBoundingClientRect().width > 1 && node.getBoundingClientRect().height > 1,
              )
              .map((node) => {
                const css = getComputedStyle(node);
                return [css.fontFamily, css.fontSize];
              }),
          };
        });
        const context = `${viewport.width}, ${density}, ${path}`;
        reference ??= state.chrome;
        // Live gameplay/replay is immersive (no rail) so its chrome intentionally
        // differs; the shared-typography and single-navigation checks still apply.
        if (!path.startsWith('/games/')) expect(state.chrome, context).toEqual(reference);
        expect(state.title.width, context).toBeLessThanOrEqual(1);
        expect(state.title.height, context).toBeLessThanOrEqual(1);
        expect(state.width, context).toBeLessThanOrEqual(viewport.width);
        expect(state.chromeOverlap, context).toBe(false);
        if (state.headings.length > 1)
          expect(new Set(state.headings.map((value) => JSON.stringify(value))).size, context).toBe(
            1,
          );
      }
    }
  }
});
