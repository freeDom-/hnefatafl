import { mkdirSync } from 'node:fs';
import type { Page } from '@playwright/test';
import { expect, test } from './auth-fixture';
import type { OnlineGame } from '../src/onlineTypes';
import type { PersonalOnline, PersonalOnlineHistory } from '../src/personalTypes';

/**
 * Arena v2 layout + theme acceptance captures. Every appearance renders the same
 * information architecture: the desktop rail above 1024px, and the compact header
 * with the bottom navigation below it. Only colors, rail, fonts and surface
 * treatment change between the three named themes; `legacy` keeps the original
 * color-mode look.
 */
const output = 'test-results/arena-redesign';

const appearances = [
  { id: 'kings-table', rail: 'rgb(9, 45, 34)' },
  { id: 'iron-ash', rail: 'rgb(32, 38, 41)' },
  { id: 'saga-hall', rail: 'rgb(30, 16, 10)' },
  { id: 'legacy', rail: null },
] as const;

const viewports = [
  { name: '1440', width: 1440, height: 900 },
  { name: '1366', width: 1366, height: 768 },
  { name: '1024', width: 1024, height: 768 },
  { name: '768', width: 768, height: 1024 },
  { name: '390', width: 390, height: 844 },
  { name: '320', width: 320, height: 640 },
] as const;

/**
 * The two desktop viewports the primary reference composition must fit without a
 * page scroll. The preserved Lobby and recent-record sections stay below the fold.
 */
const primaryFitViewports = viewports.filter((viewport) => viewport.width >= 1366);

/**
 * The per-theme hero illustration, applied as a contained cover backdrop instead
 * of tiling the 512x512 texture behind the hero copy.
 */
const heroArt: Record<string, string> = {
  'kings-table': 'northern-hall-vignette.webp',
  'iron-ash': 'iron-ash-forged-panel.webp',
  'saga-hall': 'saga-hall-carved-banner.webp',
};

/**
 * Measure the primary composition: the intro plus the reference grid (hero and
 * your games on the left, profile, standing cards and standings on the right).
 * Its bottom edge must stay inside the viewport; the Lobby and record sections
 * below it may scroll.
 */
async function primaryComposition(page: Page) {
  return page.evaluate(() => {
    const box = (selector: string) => {
      const node = document.querySelector(selector);
      if (!node) throw new Error(`missing ${selector}`);
      return node.getBoundingClientRect();
    };
    return {
      introTop: Math.round(box('.dashboard-intro').top + window.scrollY),
      layoutBottom: Math.round(box('.dashboard-layout').bottom + window.scrollY),
      overviewTop: Math.round(box('.dashboard-overview').top + window.scrollY),
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    };
  });
}

/**
 * A real captured active game (the `active` snapshot from
 * src/onlineFixture.testdata.json, brandubh). The default e2e backend leaves
 * online admission disabled, so the populated hero is seeded through the
 * dashboard's own read endpoints rather than POST /api/online/games; the Board
 * it renders is still the production read-only component. The deadline is
 * relative so the capture keeps a meaningful countdown instead of a fixed date.
 */
const activeOnlineGameId = 'online-f8157aaad48a49d4ad338588bec9a3f5';
const activeBoard = [
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'D',
  '.',
  '.',
  '.',
  'A',
  'A',
  'D',
  'K',
  'D',
  'A',
  'A',
  '.',
  '.',
  '.',
  'D',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
  '.',
  '.',
  '.',
  'A',
  '.',
  '.',
  '.',
];
function activeOnlineGame(): OnlineGame {
  const startedAt = '2035-01-01T00:00:00.000000Z';
  return {
    id: activeOnlineGameId,
    category: 'online',
    creator_id: '7fed11edc170408facbcdc6f60c0cf1f',
    creator_side: 'attackers',
    match: {
      id: 'match-1aec358465fa4402bac7f4ef1e8cfb3d',
      mode: 'single',
      side_selection: 'attackers',
      first_creator_side: 'attackers',
      leg: 1,
      games: [{ id: activeOnlineGameId, leg: 1, status: 'active', version: 1 }],
    },
    status: 'active',
    version: 1,
    ruleset: 'brandubh',
    ruleset_version: '1',
    time_per_move: 259200,
    time_control: 'per_move',
    increment_seconds: 0,
    remaining_seconds: null,
    rated: true,
    created_at: startedAt,
    updated_at: startedAt,
    started_at: startedAt,
    deadline: new Date(Date.now() + 14 * 60 * 60 * 1000).toISOString(),
    completed_at: null,
    participants: {
      attackers: { account_id: '7fed11edc170408facbcdc6f60c0cf1f', name: 'BrowserAdmin' },
      defenders: { account_id: 'c224053c2762463cb463894c90ebb8c3', name: 'Keeper' },
    },
    position: {
      ruleset: 'brandubh',
      board: activeBoard,
      turn: 'attackers',
      ply: 0,
      winner: null,
      reason: null,
    },
    terminal_king_square: null,
    captured: { attackers: 0, defenders: 0 },
    moves: [],
    result: null,
  };
}
/** The account-owned history the dashboard lists; one active game in this pool. */
function activeOnlineHistory(): PersonalOnlineHistory {
  const game = activeOnlineGame();
  const summary: PersonalOnline = {
    id: game.id,
    category: 'online',
    ruleset: game.ruleset,
    status: game.status,
    version: game.version,
    rated: game.rated,
    turn: game.position.turn,
    own_side: 'attackers',
    your_turn: true,
    overdue: false,
    deadline: game.deadline,
    created_at: game.created_at,
    updated_at: game.updated_at,
    players: { attackers: 'BrowserAdmin', defenders: 'Keeper' },
    outcome: null,
    abandoned: false,
    match: { id: game.match.id, mode: game.match.mode, leg: 1, games: game.match.games },
    result: null,
  };
  return {
    category: 'online',
    limit: 25,
    matching: 1,
    next_cursor: null,
    as_of: game.updated_at,
    games: [summary],
    overview: { your_turn: 1, other_active: 0, waiting: 0, completed: 0, cancelled: 0, voided: 0 },
    totals: { category: 'online', played: 42, wins: 27, losses: 15, draws: 0, abandoned_games: 0 },
    statistics: {
      groups: [
        {
          ruleset: 'brandubh',
          mode: 'single',
          played: 42,
          completed: 42,
          wins: 27,
          losses: 15,
          draws: 0,
          win_rate: 0.64,
          win_rate_attackers: 0.64,
          win_rate_defenders: null,
          average_game_length: 24,
          average_game_length_unit: 'moves',
          rating: 1576,
        },
      ],
      coverage: { unattributed_games: 0 },
    },
  };
}

/**
 * Seed the dashboard's own game lists so the hero state is deterministic: no
 * single-player or local game, then either no online game (empty state) or the
 * captured active online game (populated hero with the real read-only board).
 */
async function seedEmptyGameLists(page: Page) {
  await page.route('**/api/personal/single-player**', (route) =>
    route.fulfill({ json: { games: [] } }),
  );
  await page.route('**/api/personal/local**', (route) => route.fulfill({ json: { games: [] } }));
}
function emptyOnlineHistory(): PersonalOnlineHistory {
  return {
    category: 'online',
    limit: 25,
    matching: 0,
    next_cursor: null,
    as_of: '2035-01-01T00:00:00.000000Z',
    games: [],
    overview: { your_turn: 0, other_active: 0, waiting: 0, completed: 0, cancelled: 0, voided: 0 },
    totals: { category: 'online', played: 0, wins: 0, losses: 0, draws: 0, abandoned_games: 0 },
    statistics: { groups: [], coverage: { unattributed_games: 0 } },
  };
}
async function seedOnlineHistory(page: Page, online: PersonalOnlineHistory) {
  await page.unroute('**/api/personal/online**');
  await page.route('**/api/personal/online**', (route) => {
    const { pathname } = new URL(route.request().url());
    return route.fulfill({
      json: pathname === `/api/personal/online/${activeOnlineGameId}` ? activeOnlineGame() : online,
    });
  });
}

test.beforeAll(() => {
  mkdirSync(output, { recursive: true });
});

for (const appearance of appearances) {
  test(`captures the ${appearance.id} appearance at every required viewport`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: value }));
    }, appearance.id);
    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/dashboard');
      await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
      const root = page.locator('html');
      if (appearance.id === 'legacy') await expect(root).not.toHaveAttribute('data-ui-theme');
      else await expect(root).toHaveAttribute('data-ui-theme', appearance.id);

      // One primary navigation per viewport: the rail on desktop, the compact
      // header plus bottom bar below it.
      const desktop = viewport.width >= 1024;
      const rail = page.locator('.arena-sidebar');
      await expect(rail).toHaveCount(desktop ? 1 : 0);
      await expect(page.locator('.arena-topbar')).toHaveCount(desktop ? 1 : 0);
      if (desktop) {
        await expect(page.locator('.site-header')).toHaveCount(0);
        await expect(rail.locator('[data-destination="dashboard"]')).toHaveAttribute(
          'aria-current',
          'page',
        );
        await expect(rail.locator('.sidebar-brand img[src*="hnefatafl-knot"]')).toHaveCount(1);
        await expect(rail.locator('.sidebar-brand')).toContainText('HNEFATAFL');
        if (appearance.rail) {
          await expect(rail).toHaveCSS('background-color', appearance.rail);
          const width = await rail.evaluate((node) => node.getBoundingClientRect().width);
          expect(width, `${appearance.id} rail width`).toBeGreaterThanOrEqual(230);
          expect(width, `${appearance.id} rail width`).toBeLessThanOrEqual(281);
        }
      } else {
        await expect(page.locator('.site-header')).toHaveCount(1);
        // Tablets keep the header navigation; phones use the bottom bar.
        await expect(
          page.locator('.site-header nav:visible, .workspace-bottom-nav:visible'),
        ).toHaveCount(1);
      }

      // The same dashboard information architecture in every appearance: intro,
      // your-turn hero, four standing cards, games that need you, profile column
      // and the standings card. Only colour, ornament and fonts may differ.
      await expect(page.locator('.dashboard-intro')).toBeVisible();
      await expect(page.locator('.dashboard-intro-title')).toContainText(
        'Your place at the table.',
      );
      await expect(page.locator('.arena-hero')).toBeVisible();
      await expect(page.locator('.dashboard-stat')).toHaveCount(4);
      await expect(page.locator('.dashboard-needs-you')).toBeVisible();
      await expect(page.locator('.dashboard-profile')).toBeVisible();
      await expect(page.locator('.leaderboard-compact')).toBeVisible();
      if (viewport.width >= 1200) {
        // The reference two-column split: main content left, profile column right.
        // Narrower desktops stack both columns for readability.
        const main = await page.locator('.dashboard-main').boundingBox();
        const aside = await page.locator('.dashboard-aside').boundingBox();
        expect(main!.x, `${appearance.id} main column`).toBeLessThan(aside!.x);
      }
      // The theme texture/ornament layers are decorative and must never intercept
      // a pointer or cover real copy.
      expect(
        await page.evaluate(
          () => getComputedStyle(document.querySelector('.arena-hero')!, '::after').pointerEvents,
        ),
      ).toBe('none');
      expect(
        await page.evaluate(
          () => getComputedStyle(document.querySelector('.arena-hero')!, '::before').pointerEvents,
        ),
      ).toBe('none');

      // The hero uses the theme's illustration as a contained cover backdrop
      // instead of tiling the 512px texture, and the knot band is a real trim
      // (not a 1px hairline) on the hero and on the primary section headings.
      if (appearance.id !== 'legacy') {
        const art = await page.evaluate(() => {
          const hero = document.querySelector('.arena-hero')!;
          const heading = document.querySelector('.dashboard-needs-you .section-heading')!;
          const heroStyle = getComputedStyle(hero);
          return {
            image: heroStyle.backgroundImage,
            size: heroStyle.backgroundSize,
            heroBand: getComputedStyle(hero, '::after'),
            headingBand: getComputedStyle(heading, '::after'),
          };
        });
        expect(art.image).toContain(heroArt[appearance.id]);
        expect(art.image).not.toContain('textures/');
        expect(art.size).toContain('cover');
        expect(art.heroBand.backgroundImage).toContain('knot-band.webp');
        expect(parseFloat(art.heroBand.height)).toBeGreaterThanOrEqual(10);
        expect(art.headingBand.backgroundImage).toContain('knot-band.webp');
        expect(parseFloat(art.headingBand.height)).toBeGreaterThanOrEqual(6);
        expect(art.headingBand.pointerEvents).toBe('none');
      }

      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      await page.screenshot({
        path: `${output}/${appearance.id}-${viewport.name}.png`,
        fullPage: true,
      });

      // Fix A: the primary composition (intro plus the reference grid) fits both
      // desktop viewports with no page scroll; the Lobby and record sections stay
      // below the fold, which is why the document itself still scrolls.
      if (viewport.width >= 1366) {
        const fit = await primaryComposition(page);
        expect(
          fit.layoutBottom,
          `${appearance.id} ${viewport.name} primary grid bottom`,
        ).toBeLessThanOrEqual(fit.innerHeight);
        expect(fit.introTop, `${appearance.id} ${viewport.name} intro top`).toBeLessThan(
          fit.innerHeight,
        );
        expect(fit.overviewTop).toBeGreaterThanOrEqual(fit.layoutBottom - 1);
        expect(fit.scrollHeight).toBeGreaterThan(fit.innerHeight);
      }
    }
  });

  test(`captures the full ${appearance.id} dashboard`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ uiTheme: value }));
    }, appearance.id);
    await page.setViewportSize({ width: 1440, height: 900 });
    // Empty state: no running game, so the hero keeps its placeholder.
    await seedEmptyGameLists(page);
    await seedOnlineHistory(page, emptyOnlineHistory());
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    await expect(page.locator('.dashboard-layout')).toBeVisible();
    await expect(page.locator('.arena-hero-empty')).toBeVisible();
    await page.screenshot({
      path: `${output}/${appearance.id}-dashboard-full.png`,
      fullPage: true,
    });
    // Viewport capture: the rail is position:fixed, so a full-page screenshot
    // only shows it for the first viewport. This matches the reference framing.
    await page.screenshot({
      path: `${output}/${appearance.id}-dashboard-empty-viewport.png`,
    });

    // Seeded active game: the YOUR TURN hero renders the real read-only board
    // instead of the empty placeholder, in the same reference framing.
    await seedOnlineHistory(page, activeOnlineHistory());
    await page.reload();
    await expect(page.locator('.arena-hero .arena-kicker')).toContainText('YOUR TURN');
    const heroBoard = page.locator('.arena-hero-board');
    await expect(heroBoard.getByRole('grid')).toBeVisible();
    await expect(heroBoard.locator('.board-frame')).toHaveCount(1);
    await expect(heroBoard.getByText('No game on the table yet.')).toHaveCount(0);
    await page.screenshot({
      path: `${output}/${appearance.id}-dashboard-viewport.png`,
    });
    await page.screenshot({
      path: `${output}/${appearance.id}-dashboard-active-full.png`,
      fullPage: true,
    });
    // The hero board makes the primary grid its tallest, so the fit has to hold
    // in this state at both desktop viewports too.
    for (const viewport of primaryFitViewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const fit = await primaryComposition(page);
      expect(
        fit.layoutBottom,
        `${appearance.id} ${viewport.name} active primary grid bottom`,
      ).toBeLessThanOrEqual(fit.innerHeight);
      await page.screenshot({
        path: `${output}/${appearance.id}-dashboard-active-${viewport.name}.png`,
      });
    }
  });
}
