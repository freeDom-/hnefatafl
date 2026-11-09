import type { Config, Model, Move, Side } from './types';

export type TournamentMode = 'round_robin' | 'fixed_roles';

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
  mode?: TournamentMode;
  randomize_start_positions?: boolean;
}
export interface TournamentDiagnostics {
  decisions: number;
  mean_completed_depth?: number | null;
  mean_decision_time?: number | null;
  mean_nodes?: number | null;
  mean_evaluations?: number | null;
  mean_simulations?: number | null;
  total_cutoffs?: number;
  transposition_cutoffs?: number;
  symmetry_moves_skipped?: number;
  depth_cap_reached_fraction?: number | null;
  /** Effective MCTS budget recorded by the participant's search settings. */
  configured_simulations?: number | null;
  /** Decisions where an MCTS simulation budget applied. */
  simulation_decisions?: number;
  /** Share of eligible decisions that completed the configured budget. */
  budget_reached_fraction?: number | null;
  /** Decisions stopped by the MCTS deadline, never inferred from a low count. */
  deadline_cutoffs?: number;
  deadline_cutoff_fraction?: number | null;
  /** Root moves that returned an immediate tactical win before searching. */
  immediate_win_shortcuts?: number;
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
  diagnostics?: { attackers?: TournamentDiagnostics; defenders?: TournamentDiagnostics };
  agent_plies?: number;
  opening_seed?: number;
  opening_version?: number;
  opening_plies?: number;
  opening_unique?: boolean;
}
export interface TournamentPerformance {
  wins: number;
  losses: number;
  draws: number;
  truncated: number;
  games: number;
  completed?: number;
  win_rate?: number | null;
  score_rate?: number | null;
}
export interface TournamentStanding extends TournamentPerformance {
  player: number;
  score: number;
  roles?: Record<Side, TournamentPerformance>;
  diagnostics?: TournamentDiagnostics;
}
export interface TournamentResult {
  scores: number[];
  standings: TournamentStanding[];
  by_ruleset: Record<
    string,
    {
      wins: number[];
      draws: number;
      truncated: number;
      games: number;
      standings?: TournamentStanding[];
    }
  >;
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
  /** Present on the collection endpoint; the match history is fetched separately. */
  summary?: boolean;
  mode?: TournamentMode;
  result?: TournamentResult;
  error?: string;
}
export interface TournamentReplay {
  validated: boolean;
  opening_plies?: number;
  opening_seed?: number | null;
  states: {
    ruleset: string;
    board: string[];
    turn: Side;
    ply: number;
    winner: Side | 'draw' | null;
    reason: string | null;
  }[];
}
