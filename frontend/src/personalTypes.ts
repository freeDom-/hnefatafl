import type { Move, Side } from './types';
import type { MatchMode, OnlinePosition, OnlineStatus } from './onlineTypes';

export type AccountSummary = {
  id: string;
  category: 'account-single-player';
  ruleset: string;
  mode: string;
  created_at: string;
  ended_at: string | null;
  status: 'in_progress' | 'completed';
  winner: Side | 'draw' | null;
  plies: number;
  players: Record<Side, { name: string; kind: 'human' | 'ai'; username?: string }>;
};
export type Paging = { limit: number; matching: number; next_cursor: string | null };
export type PersonalStatisticMetrics = {
  played: number;
  completed?: number;
  wins: number | null;
  losses: number | null;
  draws: number | null;
  timed_out?: number;
  win_rate: number | null;
  average_game_length: number | null;
  average_game_length_unit: string;
};
export type PersonalStatisticGroup = PersonalStatisticMetrics & {
  ruleset: string;
  mode: string | null;
  rating?: number | null;
  win_rate_attackers: number | null;
  win_rate_defenders: number | null;
  by_side?: Record<Side, PersonalStatisticMetrics>;
  agents?: (PersonalStatisticGroup & { id: string; name: string; profile_id: string | null })[];
};
export type PersonalStatistics = {
  groups: PersonalStatisticGroup[];
  coverage: { unattributed_games: number };
};
export type AccountHistory = Paging & {
  category: 'account-single-player';
  games: AccountSummary[];
  totals: {
    total_games: number;
    completed_games: number;
    in_progress: number;
    attackers_wins: number;
    defenders_wins: number;
    draws: number;
    average_game_length: number;
  };
  statistics: PersonalStatistics;
};
export type PersonalOnline = {
  id: string;
  category: 'online';
  ruleset: string;
  status: OnlineStatus;
  version: number;
  rated: boolean;
  turn: Side;
  own_side: Side | null;
  your_turn: boolean;
  overdue: boolean;
  deadline: string | null;
  created_at: string;
  updated_at: string;
  players: Record<Side, string | null>;
  outcome: 'win' | 'loss' | 'draw' | null;
  abandoned: boolean;
  match: {
    id: string;
    mode: MatchMode;
    leg: number;
    games: { id: string; leg: number; status: OnlineStatus; version: number }[];
  };
  result: null | {
    winner: Side | 'draw' | null;
    reason: string;
    revision: number;
    eligible: boolean;
  };
};
export type PersonalOnlineHistory = Paging & {
  category: 'online';
  games: PersonalOnline[];
  as_of: string;
  overview: Record<
    'your_turn' | 'other_active' | 'waiting' | 'completed' | 'cancelled' | 'voided',
    number
  >;
  totals: {
    category: 'online';
    played: number;
    wins: number;
    losses: number;
    draws: number;
    abandoned_games: number;
  };
  statistics: PersonalStatistics;
};
/**
 * One applied rating-ledger event for the signed-in account in one pool.
 * `rating_after` is authoritative; it is never derived from a game's start
 * snapshot. `recorded_at` is null for an explicit legacy timestamp gap, and
 * `sequence` still orders the entry.
 */
export type RatingHistoryEntry = {
  sequence: number;
  game_id: string;
  recorded_at: string | null;
  rating_after: number;
  delta: number;
  result: 'win' | 'loss' | 'draw';
  opponent_display_name: string | null;
  opponent_username?: string;
  corrected: boolean;
};
export type RatingHistory = Paging & {
  ruleset: string;
  policy_version: number;
  current_rating: number | null;
  as_of: string;
  /** True when an applied entry has no trusted completion stamp. */
  legacy_timestamp_gaps: boolean;
  items: RatingHistoryEntry[];
};
export type RatedStreak = {
  ruleset: string;
  rated_win_current: number;
  rated_win_best: number;
  rated_games: number;
};
/** Only rated online games count; there is no server-side activity streak. */
export type PersonalProgress = {
  as_of: string;
  streaks: {
    rated_win_current: number;
    rated_win_best: number;
    by_ruleset: RatedStreak[];
  };
};
export type LocalGameSummary = {
  id: string;
  category: 'local';
  ruleset: string;
  created_at: string;
  ended_at: string | null;
  status: 'in_progress' | 'completed';
  winner: Side | 'draw' | null;
  plies: number;
  players: Record<Side, { name: string; kind: 'human' }>;
};
/** Owner-scoped local 1v1 records; both seats belong to the same account. */
export type LocalHistory = Paging & {
  category: 'local';
  games: LocalGameSummary[];
  totals: {
    total_games: number;
    completed_games: number;
    in_progress: number;
    attackers_wins: number;
    defenders_wins: number;
    draws: number;
    average_game_length: number;
  };
};
export type AccountReviewData = {
  game: AccountSummary;
  game_id: string;
  version: number;
  validated: boolean;
  cursor: number;
  moves: Move[];
  states: OnlinePosition[];
};
