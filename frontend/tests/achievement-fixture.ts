import type { AchievementProgress } from '../src/portraitTypes';

/** Server-shaped projections for controlled UI tests; scoring remains server-owned. */
export function projectAchievements(progress: AchievementProgress): AchievementProgress {
  progress.families = [...new Set(progress.items.map((item) => item.family_id))].map((id) => {
    const milestones = progress.items.filter((item) => item.family_id === id);
    const awarded = milestones.filter((item) => item.unlocked);
    return {
      id,
      name: milestones[0].name,
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
