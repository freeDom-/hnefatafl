import type { AchievementProgress } from '../src/portraitTypes';

/** Keep controlled browser responses consistent with the server family contract. */
export function projectAchievements(progress: AchievementProgress): AchievementProgress {
  progress.families = progress.families.map((family) => {
    const milestones = progress.items.filter((item) => item.family_id === family.id);
    const collected = milestones.filter((item) => item.unlocked);
    const eligible = milestones.filter((item) => !item.unlocked && item.progress >= item.threshold);
    return {
      ...family,
      milestones,
      progress: milestones[0].progress,
      collected_tier: Math.max(0, ...collected.map((item) => item.tier)),
      collected_threshold: Math.max(0, ...collected.map((item) => item.threshold)),
      achieved_threshold: Math.max(
        0,
        ...milestones
          .filter((item) => item.progress >= item.threshold)
          .map((item) => item.threshold),
      ),
      collected_count: collected.length,
      complete: collected.length === milestones.length,
      next_milestone: milestones.find((item) => !item.unlocked) ?? null,
      claimable_points: eligible.reduce((sum, item) => sum + item.points, 0),
    };
  });
  progress.summary.earned_points = progress.items
    .filter((item) => item.unlocked)
    .reduce((sum, item) => sum + item.points, 0);
  progress.summary.unlocked = progress.items.filter((item) => item.unlocked).length;
  progress.summary.claimable_points = progress.families.reduce(
    (sum, family) => sum + family.claimable_points,
    0,
  );
  return progress;
}
