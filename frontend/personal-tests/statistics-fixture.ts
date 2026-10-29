import type { PersonalStatisticGroup, PersonalStatisticMetrics } from '../src/personalTypes';
function metrics(
  wins: number,
  losses: number,
  draws: number,
  length: number,
): PersonalStatisticMetrics {
  const played = wins + losses + draws;
  return {
    played,
    completed: played,
    wins,
    losses,
    draws,
    win_rate: played ? wins / played : null,
    average_game_length: played ? length : null,
    average_game_length_unit: 'moves',
  };
}
function group(
  ruleset: string,
  wins: number,
  losses: number,
  draws: number,
): PersonalStatisticGroup {
  const attackers = metrics(wins / 2, losses / 2, draws / 2, 18);
  const defenders = metrics(wins / 2, losses / 2, draws / 2, 22);
  const empty = metrics(0, 0, 0, 0);
  const base = {
    ...metrics(wins, losses, draws, 20),
    ruleset,
    mode: null,
    win_rate_attackers: attackers.win_rate,
    win_rate_defenders: defenders.win_rate,
    by_side: { attackers, defenders },
  };
  return {
    ...base,
    agents: [
      {
        ...base,
        ...attackers,
        id: ruleset + '-baseline',
        name: 'Shield bearer',
        profile_id: 'baseline',
        by_side: { attackers, defenders: empty },
      },
      {
        ...base,
        ...defenders,
        id: ruleset + '-maiden',
        name: 'Shield maiden',
        profile_id: 'shield_maiden',
        by_side: { attackers: empty, defenders },
      },
    ],
  };
}
export const singleStatistics = {
  groups: [group('brandubh', 2, 6, 2), group('tablut', 6, 2, 2), group('copenhagen', 18, 0, 2)],
  coverage: { unattributed_games: 0 },
};
export const multiplayerStatistics = {
  groups: [
    { ...group('brandubh', 6, 2, 2), agents: undefined, rating: 1537.4, timed_out: 1 },
    { ...group('tablut', 0, 0, 0), agents: undefined, rating: 1500 },
    { ...group('copenhagen', 0, 0, 0), agents: undefined, rating: null },
  ],
  coverage: { unattributed_games: 0 },
};
