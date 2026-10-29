import type { PersonalStatisticMetrics } from './personalTypes';

export const statisticRulesets = ['brandubh', 'tablut', 'copenhagen'] as const;
export const statisticRuleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
export type StatisticsMode = 'single-player' | 'multiplayer';
export function statisticsLocation() {
  const query = new URLSearchParams(location.search);
  const candidate = query.get('ruleset') ?? 'all';
  return {
    mode: (query.get('mode') === 'multiplayer' ? 'multiplayer' : 'single-player') as StatisticsMode,
    ruleset: statisticRulesets.some((id) => id === candidate) ? candidate : 'all',
  };
}
export const statisticsHref = (mode: StatisticsMode, ruleset = 'all') =>
  `/statistics?mode=${mode}&ruleset=${ruleset}`;
export const completedGames = (group: PersonalStatisticMetrics) =>
  group.completed ?? (group.wins ?? 0) + (group.losses ?? 0) + (group.draws ?? 0);

/** Cosmetic decoration only, derived from the displayed personal win rate. */
export function badgeTier(rate: number | null | undefined) {
  if (rate === null || rate === undefined) return 'unplayed';
  return rate >= 0.85 ? 'royal' : rate >= 0.65 ? 'gold' : rate >= 0.4 ? 'silver' : 'bronze';
}
