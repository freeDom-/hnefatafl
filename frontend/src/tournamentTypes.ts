import type { Config, Model, Move, Side } from './types';

export interface TournamentPlayer {
  name: string;
  rune?: string;
  agent_rune?: string;
  profile_id?: string;
  profile_name?: string;
  model_name?: string;
  model?: Model;
  variant_id?: string;
  config: Config;
}
export interface TournamentConfig {
  players: TournamentPlayer[];
  rulesets: string[];
  games: number;
  max_plies: number;
  seed: number;
}
export interface TournamentGame {
  ruleset: string;
  winner: Side | 'draw' | null;
  outcome: 'completed' | 'truncated';
  reason: string;
  plies: number;
  moves: Move[];
  attacker_player: number;
  defender_player?: number;
  average_decision_time: number;
  total_decision_time: number;
}
export interface TournamentResult {
  scores: number[];
  standings: {
    player: number;
    wins: number;
    losses: number;
    draws: number;
    truncated: number;
    games: number;
    score: number;
  }[];
  by_ruleset: Record<string, { wins: number[]; draws: number; truncated: number; games: number }>;
  games: TournamentGame[];
  truncated: number;
  average_game_length: number;
  average_decision_time: number;
}
export interface Tournament {
  id: string;
  name: string;
  status: 'running' | 'completed' | 'cancelled' | 'failed';
  created_at: string;
  ended_at?: string;
  config: TournamentConfig;
  progress: { completed_games: number; total_games: number };
  result?: TournamentResult;
  error?: string;
}
export interface TournamentReplay {
  validated: boolean;
  states: {
    ruleset: string;
    board: string[];
    turn: Side;
    ply: number;
    winner: Side | 'draw' | null;
    reason: string | null;
  }[];
}
