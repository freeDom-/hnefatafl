import type { Config, Move, Side } from './types';

export interface Advisor {
  rune?: string;
  name: string;
  profile_id?: string;
  profile_name?: string;
  model_name?: string;
  config: Config;
}

/** Per-game analysis preferences; changing these never advances the game. */
export interface AnalyticsSettings {
  advisor: Advisor | null;
  time_limit: number;
  show_win_chances: boolean;
  revision: number;
}

export const defaultAnalyticsSettings: AnalyticsSettings = {
  advisor: null,
  time_limit: 5,
  show_win_chances: true,
  revision: 0,
};

export interface AgentEvaluation {
  rune?: string;
  agent_id: Side | 'advisor';
  name: string;
  role: Side | 'advisor';
  profile_id?: string;
  profile_name?: string;
  model_name?: string;
  config: Config;
  source:
    | 'heuristic'
    | 'mcts_rollout'
    | 'neural_value'
    | 'neural_search'
    | 'terminal'
    | 'unavailable';
  win_probability: number | null;
  estimated: boolean;
  calibrated: boolean;
  value?: number;
  best_move?: Move | null;
  completed_depth?: number;
  simulations?: number;
  searched_moves?: number;
  nodes?: number;
  evaluations?: number;
  cutoffs?: number;
  transposition_hits?: number;
  symmetry_moves_skipped?: number;
  search_seed?: number;
  unavailable_reason?: 'model_unavailable' | 'evaluation_failed';
}

export interface PositionAnalysis {
  cursor: number;
  ply: number;
  side_to_move: Side;
  position_key: string;
  created_at: string | null;
  evaluations: AgentEvaluation[];
  hint: { agent_id: 'advisor'; move: Move } | null;
  unavailable_reason?: 'not_recorded';
  status?: 'pending' | 'complete';
  advisor_revision?: number;
}
