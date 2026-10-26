import { profileConfiguration } from './gameSetupPreference';
import {
  DEFAULT_ALPHA_BETA_SEARCH,
  TABLE_CAPACITY_MAX,
  TABLE_CAPACITY_MIN,
  type AlphaBetaSearchSettings,
} from './alphaBetaSearch';
import type { AIProfile, Config, Model } from './types';
import type { TournamentConfig, TournamentPlayer } from './tournamentTypes';

/**
 * One portable, reviewable tournament-configuration document. This mirrors the
 * request body of `POST /api/tournaments` and deliberately excludes every
 * server-managed value: IDs, status, results, progress, ownership, timestamps
 * and worker paths are never imported.
 *
 * ```json
 * {
 *   "name": "Spring round robin",
 *   "rulesets": ["brandubh"],
 *   "players": [
 *     { "profile_id": "baseline", "config": { "algorithm": "baseline", "time_limit": 1 } },
 *     {
 *       "profile_id": "neural",
 *       "config": {
 *         "algorithm": "neural",
 *         "model_id": "<registered model id>",
 *         "search_wrapper": "mcts",
 *         "time_limit": 10
 *       }
 *     }
 *   ],
 *   "games": 2,
 *   "max_plies": 150,
 *   "seed": 42
 * }
 * ```
 */
export const MAX_TOURNAMENT_CONFIG_BYTES = 1024 * 1024;

const RULESETS = ['brandubh', 'tablut', 'copenhagen'];
const ALGORITHMS = ['baseline', 'shield_maiden', 'alpha_beta', 'mcts', 'neural'];
const FIXED_PRESETS = ['baseline', 'shield_maiden', 'alpha_beta', 'mcts'];
const AI_BOUNDS: Record<string, [number, number, boolean]> = {
  depth: [1, 5, true],
  iterations: [1, 5000, true],
  time_limit: [0.01, 15, false],
  exploration: [0, 10, false],
  temperature: [0, 5, false],
  rollout_depth: [0, 50, true],
};

export class TournamentConfigImportError extends Error {
  fields: string[];

  constructor(message: string, fields: string[] = []) {
    super(message);
    this.name = 'TournamentConfigImportError';
    this.fields = fields;
  }
}

export interface ImportedTournamentConfig {
  name?: string;
  config: TournamentConfig;
}

const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const integer = (value: unknown, low: number, high: number): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= low && value <= high;

const SEARCH_KEYS = new Set([
  'legacy',
  'transposition',
  'move_ordering',
  'symmetry_transposition',
  'symmetry_pruning',
  'table_capacity',
]);
const SEARCH_FLAGS = [
  'legacy',
  'transposition',
  'move_ordering',
  'symmetry_transposition',
  'symmetry_pruning',
] as const;

/** Validate a nested alpha-beta search object exactly as the server will. */
function parseSearchConfig(raw: unknown, fields: string[]): AlphaBetaSearchSettings | null {
  const provided = object(raw);
  if (!provided || Object.keys(provided).some((key) => !SEARCH_KEYS.has(key))) {
    fields.push('search');
    return null;
  }
  const result = { ...DEFAULT_ALPHA_BETA_SEARCH };
  for (const key of SEARCH_FLAGS) {
    const value = provided[key];
    if (value === undefined) continue;
    if (typeof value !== 'boolean') {
      fields.push(`search.${key}`);
      return null;
    }
    result[key] = value;
  }
  if (provided.table_capacity !== undefined) {
    if (!integer(provided.table_capacity, TABLE_CAPACITY_MIN, TABLE_CAPACITY_MAX)) {
      fields.push('search.table_capacity');
      return null;
    }
    result.table_capacity = provided.table_capacity;
  }
  if (result.symmetry_transposition) result.transposition = true;
  if (result.legacy) return { ...DEFAULT_ALPHA_BETA_SEARCH, legacy: true };
  return result;
}

function parseRulesets(value: unknown, fields: string[]): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 3) {
    fields.push('rulesets');
    return [];
  }
  const rulesets = value.map(String);
  if (
    rulesets.some((ruleset) => !RULESETS.includes(ruleset)) ||
    new Set(rulesets).size !== rulesets.length
  ) {
    fields.push('rulesets');
    return [];
  }
  return rulesets;
}

/** Validate one opponent's AI search configuration with the same bounds as play. */
function parsePlayerConfig(
  raw: unknown,
  profile: AIProfile | undefined,
  rulesets: string[],
  models: Model[],
  fields: string[],
): Config {
  const provided = object(raw) ?? {};
  const algorithm = String(provided.algorithm ?? profile?.algorithm ?? 'baseline');
  if (!ALGORITHMS.includes(algorithm)) {
    fields.push('algorithm');
    return {};
  }
  const config: Config = { algorithm };
  for (const [key, [low, high, isInteger]] of Object.entries(AI_BOUNDS)) {
    const value = provided[key];
    if (value === undefined) continue;
    if (
      typeof value !== 'number' ||
      !Number.isFinite(value) ||
      value < low ||
      value > high ||
      (isInteger && !Number.isInteger(value))
    ) {
      fields.push(key);
      return {};
    }
    config[key] = value;
  }
  if (provided.evaluation !== undefined) {
    if (provided.evaluation !== 'heuristic' && provided.evaluation !== 'neural') {
      fields.push('evaluation');
      return {};
    }
    config.evaluation = provided.evaluation;
  }
  if (provided.heuristic_version !== undefined) {
    if (
      algorithm !== 'shield_maiden' ||
      provided.heuristic_version !== 1 ||
      !Number.isInteger(provided.heuristic_version)
    ) {
      fields.push('heuristic_version');
      return {};
    }
    config.heuristic_version = 1;
  }
  if (provided.heuristic !== undefined) {
    if (
      !['shield_bearer', 'shield_maiden'].includes(String(provided.heuristic)) ||
      (algorithm !== 'alpha_beta' && algorithm !== 'mcts')
    ) {
      fields.push('heuristic');
      return {};
    }
    config.heuristic = String(provided.heuristic);
  }
  if (provided.search_wrapper !== undefined) {
    if (
      algorithm !== 'neural' ||
      !['mcts', 'alpha_beta', 'none'].includes(String(provided.search_wrapper))
    ) {
      fields.push('search_wrapper');
      return {};
    }
    config.search_wrapper = String(provided.search_wrapper);
  }
  if (provided.search !== undefined) {
    const search = parseSearchConfig(provided.search, fields);
    if (!search) return {};
    const alphaBetaSearch =
      algorithm === 'alpha_beta' ||
      (algorithm === 'neural' && config.search_wrapper === 'alpha_beta');
    if (!alphaBetaSearch) {
      fields.push('search');
      return {};
    }
    if (algorithm === 'neural' && (search.symmetry_transposition || search.symmetry_pruning)) {
      fields.push('search');
      return {};
    }
    config.search = search;
  }
  const usesModel = algorithm === 'neural' || config.evaluation === 'neural';
  if (usesModel) {
    const modelId = provided.model_id;
    if (typeof modelId !== 'string' || !modelId) {
      fields.push('model_id');
      return {};
    }
    const model = models.find((candidate) => candidate.id === modelId);
    if (!model || !rulesets.every((ruleset) => ruleset === model.ruleset)) {
      fields.push('model_id');
      return {};
    }
    config.model_id = model.id;
  }
  return config;
}

function parsePlayer(
  raw: unknown,
  rulesets: string[],
  profiles: AIProfile[],
  models: Model[],
  fields: string[],
): TournamentPlayer {
  const provided = object(raw);
  if (!provided) {
    fields.push('players');
    return { name: 'Computer', config: {} };
  }
  const profileId =
    provided.profile_id === undefined
      ? undefined
      : typeof provided.profile_id === 'string'
        ? provided.profile_id
        : null;
  if (profileId === null) {
    fields.push('profile_id');
    return { name: 'Computer', config: {} };
  }
  const profile = profileId ? profiles.find((candidate) => candidate.id === profileId) : undefined;
  if (profileId && !profile) {
    fields.push('profile_id');
    return { name: 'Computer', config: {} };
  }
  // Named fixed presets are snapshotted from the catalog, exactly as the API does.
  const config =
    profile && FIXED_PRESETS.includes(profile.id)
      ? profileConfiguration(profile)
      : parsePlayerConfig(provided.config, profile, rulesets, models, fields);
  if (profile && !FIXED_PRESETS.includes(profile.id) && provided.config === undefined)
    Object.assign(config, profileConfiguration(profile));
  const model = config.model_id
    ? models.find((candidate) => candidate.id === config.model_id)
    : undefined;
  return {
    name: model?.name ?? profile?.name ?? 'Computer',
    profile_id: profile?.id,
    config,
  };
}

/**
 * Validate a parsed JSON document as a tournament configuration. Every field is
 * checked before the form is touched, so a failed import never partially
 * overwrites the setup.
 */
export function parseTournamentConfig(
  value: unknown,
  profiles: AIProfile[],
  models: Model[],
): ImportedTournamentConfig {
  const root = object(value);
  if (!root) {
    throw new TournamentConfigImportError('The configuration must be a JSON object.');
  }
  const fields: string[] = [];
  let name: string | undefined;
  if (root.name !== undefined) {
    if (typeof root.name !== 'string' || !root.name.trim() || root.name.trim().length > 80) {
      fields.push('name');
    } else {
      name = root.name.trim();
    }
  }
  const rulesets = parseRulesets(root.rulesets, fields);
  let players: TournamentPlayer[] = [];
  if (!Array.isArray(root.players) || root.players.length < 2 || root.players.length > 4) {
    fields.push('players');
  } else {
    players = root.players.map((player) => parsePlayer(player, rulesets, profiles, models, fields));
  }
  if (!integer(root.games, 2, 100) || root.games % 2) fields.push('games');
  if (!integer(root.max_plies, 1, 1000)) fields.push('max_plies');
  if (!integer(root.seed, 0, 2 ** 32 - 1)) fields.push('seed');
  if (fields.length) {
    throw new TournamentConfigImportError('Invalid tournament configuration values.', [
      ...new Set(fields),
    ]);
  }
  return {
    name,
    config: {
      players,
      rulesets,
      games: root.games as number,
      max_plies: root.max_plies as number,
      seed: root.seed as number,
    },
  };
}

function readFileText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('The file could not be read.'));
    reader.readAsText(file);
  });
}

/** Read a bounded local JSON file and validate it before touching the form. */
export async function readImportedTournamentConfig(
  file: File,
  profiles: AIProfile[],
  models: Model[],
): Promise<ImportedTournamentConfig> {
  if (file.size > MAX_TOURNAMENT_CONFIG_BYTES) {
    throw new TournamentConfigImportError('The configuration file is too large.');
  }
  let text: string;
  try {
    text = await readFileText(file);
  } catch {
    throw new TournamentConfigImportError('The configuration file could not be read.');
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new TournamentConfigImportError('The configuration is not valid JSON.');
  }
  return parseTournamentConfig(value, profiles, models);
}
