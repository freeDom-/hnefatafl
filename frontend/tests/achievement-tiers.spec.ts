import { mkdirSync } from 'node:fs';
import { test, expect } from './auth-fixture';
import { projectAchievements } from './achievement-fixture';
import type { AchievementProgress } from '../src/portraitTypes';

for (const theme of ['classic-table', 'iron-ash', 'kings-table', 'saga-hall']) {
  test(`Evolving achievement tiers and score in ${theme}`, async ({ page }, info) => {
    const response = await page.request.get('/api/personal/achievements');
    expect(response.ok()).toBe(true);
    const progress: AchievementProgress = await response.json();
    const leagueFamily = progress.families.find((family) => family.league);
    const availableSeason = leagueFamily?.league?.season;
    const collected: Record<string, number> = {
      'rated-wins': 4,
      'brandubh-wins': 9,
      'tablut-wins': 7,
      'copenhagen-wins': 2,
      'defender-wins': 0,
      'attacker-wins': 0,
      'ai-wins': 3,
      'analyzed-replays': 5,
      'rated-win-streak': 4,
      'local-first-game': 1,
      'brandubh-league': 3,
      'tablut-league': 0,
      'copenhagen-league': 5,
    };
    const counts: Record<string, number> = {
      'rated-wins': 40,
      'brandubh-wins': 1000,
      'tablut-wins': 250,
      'copenhagen-wins': 3,
      'defender-wins': 0,
      'attacker-wins': 0,
      'ai-wins': 10,
      'analyzed-replays': 55,
      'rated-win-streak': 10,
      'local-first-game': 1,
      'brandubh-league': 3,
      'tablut-league': 0,
      'copenhagen-league': 5,
    };
    progress.items = progress.items.map((item) => ({
      ...item,
      progress: counts[item.family_id],
      unlocked: item.tier <= collected[item.family_id],
      awarded_at: item.tier <= collected[item.family_id] ? '2035-01-01T00:00:00Z' : null,
    }));
    await page.route('**/api/personal/achievements', (route) =>
      route.fulfill({ json: projectAchievements(progress) }),
    );
    await page.addInitScript((theme) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ version: 2, uiTheme: theme }));
    }, theme);
    mkdirSync('test-results/achievement-tiers', { recursive: true });
    const widths = info.project.name === 'mobile' ? [390, 768] : [1920, 3840];
    for (const width of widths) {
      await page.setViewportSize({
        width,
        height: width === 390 ? 844 : width === 768 ? 1024 : width === 1920 ? 1080 : 2160,
      });
      await page.goto('/portraits');
      const list = page.locator('.portrait-achievement-list');
      await expect(list.locator('> li')).toHaveCount(13);
      for (const [family, tier] of Object.entries(collected))
        await expect(list.locator(`[data-family="${family}"]`)).toHaveAttribute(
          'data-tier',
          String(tier),
        );
      await expect(list.locator('.achievement-engraved')).toHaveCount(3);
      await expect(list.locator('.achievement-bronze')).toHaveCount(2);
      await expect(list.locator('.achievement-silver')).toHaveCount(3);
      await expect(list.locator('.achievement-gold')).toHaveCount(1);
      await expect(list.locator('.achievement-legendary')).toHaveCount(4);
      const league = list.locator('[data-family="brandubh-league"]');
      await expect(league).toHaveAttribute('data-tier', '3');
      await expect(league.locator('.achievement-league-tiers > li')).toHaveCount(5);
      await expect(league.locator('.achievement-league-state')).toHaveCount(5);
      await expect(
        league.locator('.achievement-league-tiers > li[data-state="current"]'),
      ).toHaveCount(1);
      if (availableSeason) {
        const setSeason = (available: boolean) => {
          progress.families = progress.families.map((family) =>
            family.id === 'brandubh-league' && family.league
              ? {
                  ...family,
                  league: {
                    ...family.league,
                    season: available
                      ? availableSeason
                      : {
                          ...availableSeason,
                          available: false,
                          status: 'rebuilding',
                          points: null,
                          tier: null,
                          tier_name: null,
                          qualified: null,
                          played: null,
                          next_tier: null,
                        },
                  },
                }
              : family,
          );
        };
        setSeason(false);
        await page.evaluate(() => window.dispatchEvent(new Event('progression-changed')));
        await expect(
          league.getByText('Personal progression will return after the season rebuild completes.'),
        ).toBeVisible();
        await expect(league.locator('progress')).toHaveCount(0);
        await expect(league.getByText('Permanent league tier')).toBeVisible();
        setSeason(true);
        await page.evaluate(() => window.dispatchEvent(new Event('progression-changed')));
        await expect(league.locator('.achievement-league-season progress')).toHaveCount(1);
        await expect(
          league.getByText('Personal progression will return after the season rebuild completes.'),
        ).toHaveCount(0);
      }
      const completed = list.locator('[data-family="brandubh-wins"]');
      await expect(completed).toHaveAttribute('data-state', 'complete');
      await expect(completed.locator('progress')).toHaveCount(0);
      await expect(page.locator('#achievement-score')).toHaveAttribute('max', '6770');
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      if (width === 768) {
        const gallery = page.locator('.portrait-gallery');
        for (const image of await gallery.locator('img').all()) {
          await image.scrollIntoViewIfNeeded();
          await expect(async () => {
            const size = await image.evaluate((node: HTMLImageElement) => ({
              complete: node.complete && node.naturalWidth > 0,
              pixels: node.getBoundingClientRect().width * devicePixelRatio,
              native: node.naturalWidth,
            }));
            expect(size.complete).toBe(true);
            expect(size.pixels).toBeLessThanOrEqual(size.native + 1);
          }).toPass();
        }
        await gallery.screenshot({
          path: `test-results/achievement-tiers/${theme}-tablet-gallery.png`,
        });
      }
      if (width === 1920) {
        await page
          .locator('.achievement-score')
          .screenshot({ path: `test-results/achievement-tiers/${theme}-score.png` });
      }
      await page
        .locator('.portrait-achievements')
        .screenshot({ path: `test-results/achievement-tiers/${theme}-${width}.png` });
    }
    const defender = page.locator('[data-family="defender-wins"]');
    const before = Number(await page.locator('#achievement-score').getAttribute('value'));
    // A completed authoritative event upgrades the response; there is no collection mutation.
    progress.items = progress.items.map((item) =>
      item.family_id === 'defender-wins'
        ? { ...item, progress: 1000, unlocked: true, awarded_at: '2035-01-01T00:00:00Z' }
        : item,
    );
    await page.evaluate(() => window.dispatchEvent(new Event('profile-changed')));
    await expect(defender).toHaveAttribute('data-tier', '9');
    await expect(defender).toHaveClass(/achievement-legendary/);
    await expect(page.locator('#achievement-score')).toHaveAttribute('value', String(before + 760));
    await expect(defender.locator('progress')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /claim|collect/i })).toHaveCount(0);
  });
}

test('a real local game awards a milestone automatically without a Hall visit', async ({
  page,
  request,
}) => {
  const created = await request.post('http://127.0.0.1:8001/api/games/local', {
    data: { ruleset: 'brandubh', attackers_label: 'Raven', defenders_label: 'Wolf' },
  });
  expect(created.ok()).toBe(true);
  let game = await created.json();
  for (const [source, target] of [
    [3, 2],
    [17, 16],
    [2, 3],
    [16, 17],
    [3, 2],
    [17, 16],
    [2, 3],
    [16, 17],
  ]) {
    const moved = await request.post(`http://127.0.0.1:8001/api/games/${game.id}/moves`, {
      data: { version: game.version, source, target },
    });
    expect(moved.ok()).toBe(true);
    game = await moved.json();
  }
  expect(game.recorded_winner).toBe('draw');
  const earned = await request.get('http://127.0.0.1:8001/api/personal/achievements');
  expect(
    (await earned.json()).items.find((item: { id: string }) => item.id === 'local-first-game')
      .unlocked,
  ).toBe(true);
  await page.goto('/portraits');
  await expect(page.locator('[data-family="local-first-game"]')).toHaveAttribute('data-tier', '1');
  // Keep the shared fixture's later statistics empty-state regression isolated.
  expect(
    (
      await request.delete(`http://127.0.0.1:8001/api/games/${game.id}?version=${game.version}`)
    ).ok(),
  ).toBe(true);
  await expect(page.getByRole('button', { name: /claim|collect/i })).toHaveCount(0);
});
