import type { AchievementProgress } from '../src/portraitTypes';

/** Server-shaped projections for controlled UI tests; scoring remains server-owned. */
export function projectAchievements(progress: AchievementProgress): AchievementProgress {
  progress.families = [...new Set(progress.items.map((item) => item.family_id))].map((id) => {
    const milestones = progress.items.filter((item) => item.family_id === id);
    const awarded = milestones.filter((item) => item.unlocked);
    const highest = [...awarded].sort((first, second) => second.tier - first.tier)[0];
    const existing = progress.families.find((family) => family.id === id);
    const league = existing?.league
      ? {
          ...existing.league,
          permanent_progress: highest?.tier ?? 0,
          highest_tier: highest?.league_tier ?? null,
          highest_tier_name: highest?.league_tier_name ?? null,
          tiers: existing.league.tiers.map((tier) => ({
            ...tier,
            unlocked: milestones.some(
              (milestone) => milestone.unlocked && milestone.league_tier === tier.id,
            ),
            current: highest?.league_tier === tier.id,
          })),
        }
      : null;
    return {
      id,
      name: existing?.name ?? milestones[0].family_name ?? milestones[0].name,
      milestones,
      progress: milestones[0].progress,
      awarded_tier: Math.max(0, ...awarded.map((item) => item.tier)),
      awarded_threshold: Math.max(0, ...awarded.map((item) => item.threshold)),
      achieved_threshold: Math.max(
        0,
        ...milestones
          .filter((item) => item.progress >= item.threshold)
          .map((item) => item.threshold),
      ),
      total_tiers: milestones.length,
      awarded_count: awarded.length,
      next_milestone:
        milestones.find((item) => !item.unlocked && item.threshold > item.progress) ?? null,
      complete: awarded.length === milestones.length,
      league,
    };
  });
  progress.summary = {
    total: progress.items.length,
    unlocked: progress.items.filter((item) => item.unlocked).length,
    earned_points: progress.items
      .filter((item) => item.unlocked)
      .reduce((sum, item) => sum + item.points, 0),
    total_points: progress.items.reduce((sum, item) => sum + item.points, 0),
  };
  return progress;
}
