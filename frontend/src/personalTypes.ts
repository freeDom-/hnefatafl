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
  players: Record<Side, { name: string; kind: 'human' | 'ai' }>;
};
export type Paging = { limit: number; matching: number; next_cursor: string | null };
export type PersonalStatisticGroup = {
  ruleset: string;
  mode: string;
  played: number;
  wins: number | null;
  losses: number | null;
  draws: number | null;
  timed_out?: number;
  win_rate: number | null;
  win_rate_attackers: number | null;
  win_rate_defenders: number | null;
  average_game_length: number | null;
  average_game_length_unit: string;
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
export type AccountReviewData = {
  game: AccountSummary;
  game_id: string;
  version: number;
  validated: boolean;
  cursor: number;
  moves: Move[];
  states: OnlinePosition[];
};
