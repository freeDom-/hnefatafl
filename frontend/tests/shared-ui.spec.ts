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
        await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
        const state = await page.evaluate(() => {
          const header = document.querySelector('.site-header')!;
          const nav = header.querySelector('nav')!;
          const button = nav.querySelector('button')!;
          const style = getComputedStyle(button);
          const controls = header.querySelector('.header-controls')!.getBoundingClientRect();
          const brand = header.querySelector('.brand > span:last-child')!.getBoundingClientRect();
          const title = document.querySelector('.page-heading')!.getBoundingClientRect();
          return {
            chrome: {
              height: header.getBoundingClientRect().height,
              menuTop: nav.getBoundingClientRect().top,
              font: style.fontFamily,
              size: style.fontSize,
              weight: style.fontWeight,
            },
            title: { width: title.width, height: title.height },
            width: document.documentElement.scrollWidth,
            headerOverlap: brand.right > controls.left,
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
        expect(state.chrome, context).toEqual(reference);
        expect(state.title.width, context).toBeLessThanOrEqual(1);
        expect(state.title.height, context).toBeLessThanOrEqual(1);
        expect(state.width, context).toBeLessThanOrEqual(viewport.width);
        expect(state.headerOverlap, context).toBe(false);
        if (state.headings.length > 1)
          expect(new Set(state.headings.map((value) => JSON.stringify(value))).size, context).toBe(
            1,
          );
      }
    }
  }
});
