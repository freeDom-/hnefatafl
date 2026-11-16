/** Server-owned progression contracts. Artwork paths always remain local. */
export type PortraitCatalog = {
  catalog_version: number;
  default_portrait_id: string;
  items: { id: string; name: string; unlock_achievement_id: string | null }[];
};
export type Achievement = {
  id: string;
  family_id: string;
  tier: number;
  points: number;
  name: string;
  description: string;
  category: string;
  scope: string | null;
  threshold: number;
  progress: number;
  unlocked: boolean;
  awarded_at: string | null;
  portrait_id: string | null;
};
export type AchievementFamily = {
  id: string;
  name: string;
  progress: number;
  collected_tier: number;
  collected_threshold: number;
  achieved_threshold: number;
  total_tiers: number;
  collected_count: number;
  next_milestone: Achievement | null;
  claimable_points: number;
  complete: boolean;
  milestones: Achievement[];
};
export type AchievementProgress = {
  catalog_version: number;
  portrait_catalog_version: number;
  as_of: string;
  summary: {
    unlocked: number;
    total: number;
    earned_points: number;
    total_points: number;
    claimable_points: number;
  };
  families: AchievementFamily[];
  items: Achievement[];
};
export type PortraitState = 'locked' | 'available' | 'earned' | 'selected';
