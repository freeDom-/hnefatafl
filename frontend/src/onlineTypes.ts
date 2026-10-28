import type { Move, Side } from './types';

export type OnlineStatus = 'waiting' | 'active' | 'completed' | 'cancelled' | 'voided';
export type MatchMode = 'single' | 'simultaneous' | 'sequential';
export type SideSelection = Side | 'random';
export type OnlinePosition = {
  ruleset: string;
  board: string[];
  turn: Side;
  ply: number;
  winner: Side | 'draw' | null;
  reason: string | null;
};
export type Availability = { enabled: boolean; eligible: boolean };
export type RatingApplication = {
  account_id: string;
  rating_before: number;
  rating_after: number;
  delta: number;
  opponent_rating: number;
  expected: number;
  score: number;
  provisional: boolean;
  policy_version: number;
};
export type LeaderboardEntry = {
  account_id: string;
  name: string;
  rating: number;
  rated_games: number;
  wins: number;
  losses: number;
  draws: number;
  rank: number;
  provisional: boolean;
};
export type Leaderboard = {
  ruleset: string;
  policy: {
    version: number;
    initial_rating: number;
    k_factor: number;
    provisional_games: number;
  };
  entries: LeaderboardEntry[];
  total: number;
  limit: number;
  offset: number;
  me: LeaderboardEntry | null;
};
export type OnlineGame = {
  id: string;
  category: 'online';
  creator_id: string;
  creator_side: Side | null;
  match: {
    id: string;
    mode: MatchMode;
    side_selection: SideSelection;
    first_creator_side: Side | null;
    leg: 1 | 2;
    games: { id: string; leg: 1 | 2; status: OnlineStatus; version: number }[];
  };
  status: OnlineStatus;
  version: number;
  ruleset: string;
  ruleset_version: string;
  time_per_move: number;
  rated: boolean;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  deadline: string | null;
  completed_at: string | null;
  participants: Record<Side, { account_id: string; name: string } | null>;
  position: OnlinePosition;
  terminal_king_square: number | null;
  captured: { attackers: number; defenders: number };
  moves: (Move & { ply: number; accepted_at: string; side: Side })[];
  result: null | {
    game_id: string;
    revision: number;
    supersedes: number | null;
    category: 'online';
    ruleset: string;
    ruleset_version: string;
    attackers: string;
    defenders: string;
    started_at: string;
    completed_at: string;
    revised_at: string;
    winner: Side | 'draw' | null;
    termination_reason: string;
    move_count: number;
    competitive_eligible: boolean;
    abandoned_account_id: string | null;
    rated_eligible: boolean;
    rated_reason: string | null;
    rating: RatingApplication[] | null;
  };
};
export type OnlineListing = {
  games: OnlineGame[];
  limit: number;
  offset: number;
  section: 'all' | 'mine' | 'my_turn';
};
export type OnlineTransition = {
  game: OnlineGame;
  transition: 'created' | 'join' | 'cancel' | 'move' | 'concede' | 'timeout' | 'none' | 'void';
  action_accepted: boolean;
};
export type OnlineReplay = { game: OnlineGame; validated: boolean; states: OnlinePosition[] };
export type CreationIntent = {
  request_id: string;
  ruleset: string;
  side_selection: SideSelection;
  mode: MatchMode;
  time_per_move: number;
  rated: boolean;
};
