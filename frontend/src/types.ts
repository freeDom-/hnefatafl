import type { Advisor, AnalyticsSettings, PositionAnalysis } from './analysisTypes';

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
  rune?: string;
  agent_rune?: string;
  name: string;
  kind: 'human' | 'ai';
  profile_id?: string;
  config: Config;
  player_id?: string;
  profile_name?: string;
  model_name?: string;
}
export interface Game {
  id: string;
  advisor?: Advisor | null;
  analysis_enabled?: boolean;
  analysis?: PositionAnalysis | null;
  analytics?: AnalyticsSettings;
  seed?: number;
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
  description?: string;
  rune?: string;
  id: string;
  name: string;
  algorithm: string;
  difficulty: string;
  parameters: Config;
  supported_rulesets: string[];
}
export interface Player {
  rune?: string;
  id: string;
  name: string;
}
export interface Model {
  self_play_games?: {
    version: number;
    job: number | null;
    cumulative: number | null;
    baseline: number | null;
  };
  rune?: string;
  id: string;
  name: string;
  ruleset: string;
  version?: string | number;
  iteration?: number;
  architecture?: { name?: string; channels?: number; blocks?: number };
  created_at?: string;
  metrics?: Record<string, number>;
  training_config?: Record<string, unknown>;
  evaluation?: {
    wins?: number;
    losses?: number;
    draws?: number;
    truncated?: number;
    score?: number;
    opponent?: string;
    seed?: number;
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
  resumable?: boolean;
  checkpoint_path?: string;
  created_at?: string;
  result?: Record<string, unknown>;
  execution?: 'local' | 'remote';
  target_worker_id?: string | null;
  worker_id?: string | null;
  worker_name?: string | null;
  lease_expires_at?: string | null;
  last_reported_at?: string | null;
  software?: {
    worker: { package_version: string | null; git_revision: string | null };
    server: { package_version: string; git_revision: string | null };
    version_override_active: boolean;
  };
}
export interface WorkerCompatibility {
  eligible: boolean;
  code: string;
  reason: string;
  mismatches: string[];
  protocol_compatible: boolean;
  override_enabled: boolean;
  override_active: boolean;
  worker_version: string | null;
  server_version: string;
  worker_git_revision: string | null;
  server_git_revision: string | null;
}
export interface TrainingWorker {
  id: string;
  name: string;
  capacity: number;
  status: 'online' | 'offline' | 'revoked';
  last_seen: string | null;
  created_at?: string;
  capabilities: DeviceInfo;
  active_jobs: number;
  compatibility?: WorkerCompatibility;
  version_override_enabled?: boolean;
}
export interface DeviceInfo {
  package_version?: string;
  git_revision?: string | null;
  protocol_version?: number;
  inference_backends?: string[];
  inference_cache_modes?: string[];
  training_symmetry_modes?: string[];
  openvino_version?: string;
  openvino_error?: string;
  openvino_devices?: { id: string; name: string; available: boolean; error?: string }[];
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
  rune?: string;
  seed_count?: number;
  name?: string;
  id?: string;
  ruleset?: string;
  side?: string;
  kind?: string;
  opponent_name?: string;
  config?: Config;
  variant_id?: string;
  profile_id?: string;
  profile_name?: string;
  model_id?: string;
  model_name?: string;
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
