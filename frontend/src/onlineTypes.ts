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
  rated_intent: false;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  deadline: string | null;
  completed_at: string | null;
  participants: Record<Side, { account_id: string; name: string } | null>;
  position: OnlinePosition;
  moves: (Move & { ply: number; accepted_at: string })[];
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
    rated_eligible: false;
    rated_reason: 'policy_not_approved';
  };
};
export type OnlineListing = { games: OnlineGame[]; limit: number; offset: number };
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
};
