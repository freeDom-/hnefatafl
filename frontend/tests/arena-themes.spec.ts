import { mkdirSync } from 'node:fs';
import { expect, test } from './auth-fixture';
import { historicalGame } from './historical-fixture';

// Chromium is the only configured browser, so these checks run wherever
// `npm --prefix frontend run test:e2e` is available with an installed Chromium.
const themes = [
  {
    id: 'kings-table',
    accent: 'rgb(30, 80, 55)',
    line: 'rgb(199, 176, 139)',
    raised: 'rgb(239, 228, 209)',
    copy: 'rgb(78, 89, 76)',
    colorScheme: 'light',
  },
  {
    id: 'iron-ash',
    accent: 'rgb(233, 194, 126)',
    line: 'rgb(86, 100, 106)',
    raised: 'rgb(43, 54, 61)',
    copy: 'rgb(196, 206, 208)',
    colorScheme: 'dark',
  },
  {
    id: 'saga-hall',
    accent: 'rgb(233, 191, 121)',
    line: 'rgb(146, 112, 80)',
    raised: 'rgb(72, 51, 40)',
    copy: 'rgb(224, 205, 177)',
    colorScheme: 'dark',
  },
] as const;
/**
 * The live record badge must read the appearance's own tokens instead of the
 * former fixed `#7d4f28`/`#fff` pair, which ignored every named theme and the
 * legacy color modes. Each entry is the resolved `--surface-raised` background,
 * the `--ui-copy` ink (`--text` in legacy light, where `--ui-copy` is unset)
 * and the `--green` accent border.
 */
const liveBadgeAppearances: readonly {
  id: string;
  stored: { uiTheme?: string; mode?: string };
  background: string;
  color: string;
  border: string;
}[] = [
  ...themes.map((theme) => ({
    id: theme.id,
    stored: { uiTheme: theme.id },
    background: theme.raised,
    color: theme.copy,
    border: theme.accent,
  })),
  {
    id: 'legacy-light',
    stored: { mode: 'light' },
    background: 'rgb(240, 242, 232)',
    color: 'rgb(38, 53, 46)',
    border: 'rgb(23, 61, 55)',
  },
  {
    id: 'legacy-dark',
    stored: { mode: 'dark' },
    background: 'rgb(41, 55, 45)',
    color: 'rgb(191, 204, 179)',
    border: 'rgb(167, 208, 187)',
  },
  {
    id: 'legacy-wood',
    stored: { mode: 'wood' },
    background: 'rgb(71, 50, 37)',
    color: 'rgb(222, 204, 172)',
    border: 'rgb(230, 189, 125)',
  },
];
/** A live online record: the Hall lists it with the `.record-live` badge. */
const liveHallPage = {
  games: [
    {
      id: 'a'.repeat(32),
      category: 'online',
      ruleset: 'brandubh',
      status: 'active',
      winner: null,
      eligible: true,
      created_at: '2026-01-01T00:00:00+00:00',
      recorded_at: '2026-01-01T00:00:00+00:00',
      attackers: 'Odin',
      defenders: 'Jarl',
      owner: null,
      attacker_rating: null,
      defender_rating: null,
      winner_delta: null,
    },
  ],
  matching: 1,
  limit: 20,
  next_cursor: null,
  totals: [
    { category: 'online', games: 1, completed: 0, attackers_wins: 0, defenders_wins: 0, draws: 0 },
  ],
};
/** WCAG 2.1 relative luminance of an opaque `rgb(...)` computed color. */
function relativeLuminance(color: string) {
  const [r, g, b] = color
    .match(/[\d.]+/g)!
    .slice(0, 3)
    .map(Number)
    .map((value) => {
      const channel = value / 255;
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
/** WCAG AA contrast ratio of a foreground and a background computed color. */
function contrastRatio(foreground: string, background: string) {
  const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)].sort(
    (a, b) => b - a,
  );
  return (lighter + 0.05) / (darker + 0.05);
}
const viewports = [
  { name: '320', width: 320, height: 640 },
  { name: '390', width: 390, height: 844 },
  { name: '768', width: 768, height: 1024 },
  { name: '1024', width: 1024, height: 768 },
  { name: '1440', width: 1440, height: 900 },
  { name: 'landscape', width: 740, height: 360 },
];
const modes = ['light', 'dark', 'system', 'wood'] as const;
const output = 'test-results/arena-themes';

test.beforeAll(() => {
  mkdirSync(output, { recursive: true });
});

for (const theme of themes) {
  test(`captures the ${theme.id} UI theme at every required viewport`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: value }));
    }, theme.id);
    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/dashboard');
      await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
      await expect(page.locator('html')).toHaveAttribute('data-ui-theme', theme.id);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      await page.screenshot({
        path: `${output}/${theme.id}-${viewport.name}.png`,
        fullPage: true,
      });
    }
  });

  for (const mode of modes) {
    test(`keeps the ${theme.id} controls and color-scheme on the theme in ${mode} mode`, async ({
      page,
    }) => {
      // `wood` is an application color mode, not a browser preference; the stored
      // mode still drives data-color-mode and the theme's own color-scheme.
      await page.emulateMedia({
        colorScheme: mode === 'dark' || mode === 'system' ? 'dark' : 'light',
      });
      await page.addInitScript(
        ([id, selected]) => {
          localStorage.setItem(
            'hnefatafl-appearance',
            JSON.stringify({ uiTheme: id, mode: selected }),
          );
        },
        [theme.id, mode],
      );
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.goto('/dashboard');
      const root = page.locator('html');
      await expect(root).toHaveAttribute('data-ui-theme', theme.id);
      await expect(root).toHaveAttribute('data-color-mode', mode === 'system' ? 'dark' : mode);
      // The legacy data-color-mode button variants must not repaint the theme.
      const primary = page.getByRole('button', { name: 'New online game' });
      await expect(primary).toBeVisible();
      expect(await primary.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe(
        theme.accent,
      );
      // The Play cards must read the theme's own line token, never a hard-coded
      // light fallback that would break the dark named themes.
      const card = page.locator('.play-mode-card').first();
      await expect(card).toBeVisible();
      expect(await card.evaluate((node) => getComputedStyle(node).borderTopColor)).toBe(theme.line);
      expect(await root.evaluate((node) => getComputedStyle(node).colorScheme)).toContain(
        theme.colorScheme,
      );
      expect(await root.evaluate((node) => node.style.colorScheme)).toBe('');
      await page.screenshot({
        path: `${output}/${theme.id}-${mode}-mode.png`,
        fullPage: false,
      });
    });
  }
}

for (const appearance of liveBadgeAppearances) {
  test(`keeps the live record badge on the ${appearance.id} tokens`, async ({ page }) => {
    await page.addInitScript((stored: { uiTheme?: string; mode?: string }) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify(stored));
    }, appearance.stored);
    await page.route(/\/api\/history\/games(?:\?.*)?$/, (route) =>
      route.fulfill({ json: liveHallPage }),
    );
    await page.goto('/archive');
    const badge = page.locator('.record-live');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('Live');
    const rendered = await badge.evaluate((node) => {
      const style = getComputedStyle(node);
      return {
        background: style.backgroundColor,
        color: style.color,
        border: style.borderTopColor,
      };
    });
    expect(rendered.background).toBe(appearance.background);
    expect(rendered.color).toBe(appearance.color);
    expect(rendered.border).toBe(appearance.border);
    expect(contrastRatio(rendered.color, rendered.background)).toBeGreaterThanOrEqual(4.5);
    await badge.screenshot({ path: `${output}/live-badge-${appearance.id}.png` });
  });
}

test('keeps the legacy appearance when no UI theme is stored', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await expect(page.locator('html')).not.toHaveAttribute('data-ui-theme');
  await page.screenshot({ path: `${output}/legacy-default-desktop.png`, fullPage: true });
});

test('keeps the compact navigation clear of the mobile board', async ({ page, request }, info) => {
  test.skip(
    info.project.name !== 'mobile',
    'Phone-specific screen layout; desktop gameplay has separate coverage.',
  );
  const response = await historicalGame(request, {
    data: { ruleset: 'tablut', mode: 'human-human' },
  });
  expect(response.ok()).toBe(true);
  const game = await response.json();
  await page.addInitScript((id) => {
    localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: id }));
  }, 'saga-hall');
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto(`/games/${game.id}`);
  await expect(page.getByRole('grid', { name: 'tablut game board' })).toBeVisible();
  const overlap = await page.evaluate(() => {
    const nav = document.querySelector('.workspace-bottom-nav')!.getBoundingClientRect();
    const board = document.querySelector('.board')!.getBoundingClientRect();
    const inspector = document.querySelector('.mobile-inspector')!.getBoundingClientRect();
    const intersects = (a: DOMRect, b: DOMRect) =>
      a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    return {
      board: intersects(nav, board),
      inspector: intersects(nav, inspector),
      scrolls: document.documentElement.scrollHeight > innerHeight + 1,
      navVisible: nav.height > 0,
    };
  });
  expect(overlap.navVisible).toBe(true);
  expect(overlap.board).toBe(false);
  expect(overlap.inspector).toBe(false);
  expect(overlap.scrolls).toBe(false);
  await page.screenshot({ path: `${output}/saga-hall-mobile-gameplay.png`, fullPage: false });
});
