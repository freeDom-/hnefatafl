/** Server-owned progression contracts. Artwork paths always remain local. */
export type PortraitCatalog = {
  catalog_version: number;
  default_portrait_id: string;
  items: { id: string; name: string; unlock_achievement_id: string | null }[];
};
export type Achievement = {
  id: string;
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
export type AchievementProgress = {
  catalog_version: number;
  portrait_catalog_version: number;
  as_of: string;
  summary: { unlocked: number; total: number };
  items: Achievement[];
};
export type PortraitState = 'locked' | 'available' | 'earned' | 'selected';
