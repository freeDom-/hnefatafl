/** Server-owned progression contracts. Artwork paths always remain local. */
import type { LeagueRuleset, LeagueTier } from './leagueTypes';

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
  ruleset?: LeagueRuleset | null;
  league_tier?: LeagueTier | null;
  league_tier_name?: string | null;
  minimum_points?: number | null;
  family_name?: string | null;
};
export type AchievementLeagueTier = {
  id: LeagueTier;
  name: string;
  minimum_points: number;
  achievement_id: string;
  points: number;
  unlocked: boolean;
  current: boolean;
};
export type AchievementLeagueSeason = {
  id: string;
  available: boolean;
  status: 'current' | 'provisional' | 'finalized' | 'rebuilding';
  points: number | null;
  tier: LeagueTier | null;
  tier_name: string | null;
  qualified: boolean | null;
  played: number | null;
  qualifying_results: number;
  next_tier: {
    id: LeagueTier;
    name: string;
    minimum_points: number;
    points_needed: number;
    progress_points: number;
    progress_total: number;
  } | null;
};
export type AchievementLeague = {
  ruleset: LeagueRuleset;
  ruleset_name: string;
  tiers: AchievementLeagueTier[];
  highest_tier: LeagueTier | null;
  highest_tier_name: string | null;
  permanent_progress: number;
  permanent_total: number;
  season: AchievementLeagueSeason;
};
export type AchievementFamily = {
  id: string;
  name: string;
  progress: number;
  awarded_tier: number;
  awarded_threshold: number;
  achieved_threshold: number;
  total_tiers: number;
  awarded_count: number;
  next_milestone: Achievement | null;
  complete: boolean;
  milestones: Achievement[];
  league?: AchievementLeague | null;
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
  };
  unlocked_portrait_ids: string[];
  families: AchievementFamily[];
  items: Achievement[];
};
export type PortraitState = 'locked' | 'ready' | 'earned' | 'selected';
export type PortraitUnlock = { portrait_id: string; newly_unlocked: boolean };
