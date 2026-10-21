import type { Config, Move, Side } from './types';

export interface Advisor {
  name: string;
  profile_id?: string;
  profile_name?: string;
  model_name?: string;
  config: Config;
}

export interface AgentEvaluation {
  agent_id: Side | 'advisor';
  name: string;
  role: Side | 'advisor';
  profile_id?: string;
  profile_name?: string;
  model_name?: string;
  config: Config;
  source: 'heuristic' | 'mcts_rollout' | 'neural_value' | 'terminal' | 'unavailable';
  win_probability: number | null;
  estimated: boolean;
  calibrated: boolean;
  value?: number;
  best_move?: Move | null;
  completed_depth?: number;
  simulations?: number;
  searched_moves?: number;
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
}
