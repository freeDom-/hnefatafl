export type Side = 'attackers' | 'defenders';
export type Mode = 'human-human' | 'human-ai' | 'ai-ai';
export type Config = Record<string, string | number | boolean>;
export interface Move {
  source: number;
  target: number;
}
export interface GameMove extends Move {
  side: Side;
  captured?: { square: number; piece: string }[] | number;
  notation?: string;
}
export interface PlayerConfig {
  name: string;
  kind: 'human' | 'ai';
  profile_id?: string;
  config: Config;
  player_id?: string;
}
export interface Game {
  id: string;
  ruleset: string;
  size: number;
  board: string[];
  turn: Side;
  ply: number;
  winner: Side | 'draw' | null;
  recorded_winner?: Side | 'draw' | null;
  recorded_plies?: number;
  reason: string | null;
  moves: GameMove[];
  cursor: number;
  total_moves: number;
  legal_moves: Move[];
  mode: Mode;
  players: Record<Side, PlayerConfig>;
  version: number;
  captured: { attackers: number; defenders: number };
  created_at: string;
  ended_at?: string;
}
export interface Ruleset {
  id: string;
  name: string;
  size: number;
  description: string;
  rules_markdown: string;
}
export interface AIProfile {
  id: string;
  name: string;
  algorithm: string;
  difficulty: string;
  parameters: Config;
  supported_rulesets: string[];
}
export interface Player {
  id: string;
  name: string;
}
export interface Model {
  id: string;
  name: string;
  ruleset: string;
  version?: string;
  created_at?: string;
  metrics?: Record<string, number>;
  evaluation?: {
    wins?: number;
    losses?: number;
    draws?: number;
    truncated?: number;
    score?: number;
    opponent?: string;
  };
  path?: string;
}
export interface Job {
  id: string;
  status: string;
  kind?: string;
  ruleset?: string;
  config?: Record<string, unknown>;
  progress?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  logs?: string[] | string;
  error?: string;
  model_id?: string;
  checkpoint?: string;
  checkpoint_path?: string;
  created_at?: string;
  result?: Record<string, unknown>;
}
export interface DeviceInfo {
  device?: string;
  selected?: string;
  cuda_available?: boolean;
  mps_available?: boolean;
  cuda_devices?: string[];
  devices?: { id: string; name: string; available: boolean }[];
  default_device?: string;
  torch_version?: string;
}
export interface StatRow {
  name?: string;
  id?: string;
  ruleset?: string;
  side?: string;
  kind?: string;
  opponent_name?: string;
  win_rate?: number;
  loss_rate?: number;
  draw_rate?: number;
  attacker_win_rate?: number;
  defender_win_rate?: number;
  games?: number;
  wins?: number;
  losses?: number;
  draws?: number;
  average_length?: number;
  average_game_length?: number;
}
export interface Stats {
  total_games?: number;
  completed_games?: number;
  active_games?: number;
  attackers_wins?: number;
  defenders_wins?: number;
  draws?: number;
  average_length?: number;
  average_game_length?: number;
  attacker_win_rate?: number;
  defender_win_rate?: number;
  draw_rate?: number;
  by_ruleset?: StatRow[] | Record<string, unknown>;
  by_player?: StatRow[];
  players?: StatRow[];
  rows?: StatRow[];
}
