import { mkdirSync } from 'node:fs';
import { test, expect } from './auth-fixture';
import { projectAchievements } from './achievement-fixture';
import type { AchievementProgress } from '../src/portraitTypes';

for (const theme of ['classic-table', 'iron-ash', 'kings-table', 'saga-hall']) {
  test(`Evolving achievement tiers and score in ${theme}`, async ({ page }, info) => {
    const response = await page.request.get('/api/personal/achievements');
    expect(response.ok()).toBe(true);
    const progress: AchievementProgress = await response.json();
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
    };
    const counts: Record<string, number> = {
      'rated-wins': 40,
      'brandubh-wins': 1000,
      'tablut-wins': 250,
      'copenhagen-wins': 3,
      'defender-wins': 1000,
      'attacker-wins': 0,
      'ai-wins': 10,
      'analyzed-replays': 55,
      'rated-win-streak': 10,
      'local-first-game': 1,
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
    await page.route('**/api/personal/achievements/confirm', async (route) => {
      expect(route.request().postDataJSON()).toEqual({ family_id: 'defender-wins' });
      progress.items = progress.items.map((item) =>
        item.family_id === 'defender-wins'
          ? { ...item, unlocked: true, awarded_at: '2035-01-01T00:00:00Z' }
          : item,
      );
      await route.fulfill({ json: projectAchievements(progress) });
    });
    await page.addInitScript((theme) => {
      localStorage.setItem('hnefatafl-appearance', JSON.stringify({ version: 2, uiTheme: theme }));
    }, theme);
    mkdirSync('test-results/achievement-tiers', { recursive: true });
    const widths = info.project.name === 'mobile' ? [390] : [1920, 3840];
    for (const width of widths) {
      await page.setViewportSize({
        width,
        height: width === 390 ? 844 : width === 1920 ? 1080 : 2160,
      });
      await page.goto('/portraits');
      const list = page.locator('.portrait-achievement-list');
      await expect(list.locator('> li')).toHaveCount(10);
      for (const [family, tier] of Object.entries(collected))
        await expect(list.locator(`[data-family="${family}"]`)).toHaveAttribute(
          'data-tier',
          String(tier),
        );
      await expect(list.locator('.achievement-engraved')).toHaveCount(2);
      await expect(list.locator('.achievement-bronze')).toHaveCount(2);
      await expect(list.locator('.achievement-silver')).toHaveCount(2);
      await expect(list.locator('.achievement-gold')).toHaveCount(1);
      await expect(list.locator('.achievement-legendary')).toHaveCount(3);
      const completed = list.locator('[data-family="brandubh-wins"]');
      await expect(completed).toHaveAttribute('data-state', 'complete');
      await expect(completed.locator('progress')).toHaveCount(0);
      await expect(page.locator('#achievement-score')).toHaveAttribute('max', '6290');
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      await page
        .locator('.portrait-achievements')
        .screenshot({ path: `test-results/achievement-tiers/${theme}-${width}.png` });
    }
    const defender = page.locator('[data-family="defender-wins"]');
    const before = Number(await page.locator('#achievement-score').getAttribute('value'));
    const claim = defender.getByRole('button', {
      name: 'Collect milestones: Rated victories as Defender',
    });
    await claim.focus();
    await page.keyboard.press('Enter');
    await expect(defender).toHaveAttribute('data-tier', '9');
    await expect(defender).toHaveClass(/achievement-legendary/);
    await expect(page.locator('#achievement-score')).toHaveAttribute('value', String(before + 760));
    await expect(defender.locator('progress')).toHaveCount(0);
  });
}
