import type { AIProfile, Config, Mode, Model, Player, PlayerConfig, Ruleset, Side } from './types';
import { readLastGameMode, rememberGameMode } from './gameModePreference';
import { selectStrongestModel } from './modelSelection';

export const GAME_SETUP_KEY = 'hnefatafl-new-game-options';
const sides = ['attackers', 'defenders'] as const;
export interface GameSetupOptions {
  ruleset: string;
  mode: Mode;
  humanSide: Side;
  fixedSeed: boolean;
  seed: number;
  advisorId: string;
  people: Record<Side, PlayerConfig>;
  computers: Record<Side, PlayerConfig>;
}

const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const validMode = (value: unknown): value is Mode =>
  value === 'human-human' || value === 'human-ai' || value === 'ai-ai';
const validSeed = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < 2 ** 32;

export function withoutSeed(config: Config): Config {
  const next = { ...config };
  delete next.seed;
  return next;
}

/** Named opponents have stable presets; custom opponents keep their own budgets. */
export function profileConfiguration(profile: AIProfile): Config {
  if (profile.id === 'shield_maiden')
    return { algorithm: 'shield_maiden', time_limit: 1, heuristic_version: 1 };
  if (profile.id === 'baseline') return { algorithm: 'baseline', time_limit: 1 };
  if (profile.id === 'alpha_beta') return { algorithm: 'alpha_beta', depth: 5, time_limit: 2 };
  if (profile.id === 'mcts')
    return {
      algorithm: 'mcts',
      // Ruleset-independent preset: the 2-second ceiling bounds search and
      // 5,000 visits cap the upper end. Training recommended plans use
      // 128/192/256.
      iterations: 5000,
      exploration: 1.4,
      temperature: 0,
      rollout_depth: 8,
      time_limit: 2,
    };
  return { ...profile.parameters, algorithm: profile.algorithm };
}

export function defaultAI(models: Model[], ruleset: string, profiles: AIProfile[]): PlayerConfig {
  const recommendation = selectStrongestModel(models, ruleset);
  const algorithm = recommendation ? 'neural' : 'baseline';
  const profile = profiles.find((candidate) => candidate.algorithm === algorithm);
  return {
    name: recommendation?.model.name ?? profile?.name ?? 'Computer',
    kind: 'ai',
    profile_id: profile?.id ?? algorithm,
    config: withoutSeed({
      time_limit: 1,
      ...(profile ? profileConfiguration(profile) : {}),
      algorithm,
      ...(recommendation ? { model_id: recommendation.model.id } : {}),
    }),
  };
}

/** Browser data never supplies a model path or an unsupported/invalid AI setting. */
function restoredComputer(
  value: unknown,
  models: Model[],
  ruleset: string,
  profiles: AIProfile[],
): PlayerConfig | null {
  const saved = object(value);
  const raw = object(saved?.config);
  if (
    saved?.kind !== 'ai' ||
    !raw ||
    !['baseline', 'shield_maiden', 'alpha_beta', 'mcts', 'neural'].includes(String(raw.algorithm))
  )
    return null;
  const config: Config = { algorithm: String(raw.algorithm) };
  const bounds: Record<string, [number, number, boolean]> = {
    depth: [1, 5, true],
    iterations: [1, 5000, true],
    time_limit: [0.01, 15, false],
    exploration: [0, 10, false],
    temperature: [0, 5, false],
    rollout_depth: [0, 50, true],
  };
  for (const [key, [low, high, integer]] of Object.entries(bounds)) {
    const value = raw[key];
    if (value === undefined) continue;
    if (
      typeof value !== 'number' ||
      !Number.isFinite(value) ||
      value < low ||
      value > high ||
      (integer && !Number.isInteger(value))
    )
      return null;
    config[key] = value;
  }
  if (raw.evaluation === 'heuristic' || raw.evaluation === 'neural')
    config.evaluation = raw.evaluation;
  if (config.algorithm === 'neural' && raw.search_wrapper !== undefined) {
    if (!['mcts', 'alpha_beta', 'none'].includes(String(raw.search_wrapper))) return null;
    config.search_wrapper = String(raw.search_wrapper);
  }
  const usesModel = config.algorithm === 'neural' || config.evaluation === 'neural';
  const model = usesModel
    ? models.find((candidate) => candidate.id === raw.model_id && candidate.ruleset === ruleset)
    : undefined;
  if (usesModel && !model) return null;
  if (model) config.model_id = model.id;
  const profile = profiles.find(
    (candidate) =>
      candidate.id === saved.profile_id &&
      candidate.algorithm === config.algorithm &&
      candidate.supported_rulesets.includes(ruleset),
  );
  return {
    name: model?.name ?? profile?.name ?? 'Computer',
    kind: 'ai',
    profile_id: profile?.id,
    config:
      profile && ['baseline', 'shield_maiden', 'alpha_beta', 'mcts'].includes(profile.id)
        ? profileConfiguration(profile)
        : config,
  };
}

function restoredPerson(value: unknown, fallback: string, players: Player[]): PlayerConfig {
  const saved = object(value);
  if (typeof saved?.player_id === 'string' && saved.player_id) {
    const player = players.find((candidate) => candidate.id === saved.player_id);
    return {
      name: player?.name ?? '',
      kind: 'human',
      config: {},
      ...(player ? { player_id: player.id } : {}),
    };
  }
  const name = typeof saved?.name === 'string' && saved.name.length <= 64 ? saved.name : fallback;
  return { name, kind: 'human', config: {} };
}

export function restoreGameSetup({
  rulesets,
  profiles,
  models,
  players,
  initialRuleset,
}: {
  rulesets: Ruleset[];
  profiles: AIProfile[];
  models: Model[];
  players: Player[];
  initialRuleset?: string;
}): GameSetupOptions & { customAI: Record<Side, boolean> } {
  let raw: Record<string, unknown> | null = null;
  try {
    const candidate = object(JSON.parse(localStorage.getItem(GAME_SETUP_KEY) ?? 'null'));
    if (candidate?.version === 1) raw = candidate;
  } catch {
    /* Invalid or unavailable storage falls back to ordinary setup. */
  }
  const exists = (value: unknown): value is string =>
    typeof value === 'string' && rulesets.some((rule) => rule.id === value);
  const ruleset = exists(raw?.ruleset)
    ? raw.ruleset
    : exists(initialRuleset)
      ? initialRuleset
      : exists('tablut')
        ? 'tablut'
        : (rulesets[0]?.id ?? 'tablut');
  const people = object(raw?.people);
  const computers = object(raw?.computers);
  const restored = Object.fromEntries(
    sides.map((side) => [side, restoredComputer(computers?.[side], models, ruleset, profiles)]),
  ) as Record<Side, PlayerConfig | null>;
  const advisorId = typeof raw?.advisorId === 'string' ? raw.advisorId : '';
  const advisorExists = advisorId.startsWith('model:')
    ? models.some((model) => model.id === advisorId.slice(6) && model.ruleset === ruleset)
    : advisorId.startsWith('profile:') &&
      profiles.some(
        (profile) =>
          profile.id === advisorId.slice(8) &&
          profile.algorithm !== 'neural' &&
          profile.supported_rulesets.includes(ruleset),
      );
  return {
    ruleset,
    mode: validMode(raw?.mode) ? raw.mode : readLastGameMode(),
    humanSide: raw?.humanSide === 'attackers' ? 'attackers' : 'defenders',
    fixedSeed: raw?.fixedSeed === true && validSeed(raw.seed),
    seed: validSeed(raw?.seed) ? raw.seed : 0,
    advisorId: advisorExists ? advisorId : '',
    people: {
      attackers: restoredPerson(people?.attackers, 'Attacker', players),
      defenders: restoredPerson(people?.defenders, 'Defender', players),
    },
    computers: {
      attackers: restored.attackers ?? defaultAI(models, ruleset, profiles),
      defenders: restored.defenders ?? defaultAI(models, ruleset, profiles),
    },
    customAI: { attackers: !!restored.attackers, defenders: !!restored.defenders },
  };
}

/** Commit a complete preference snapshot only after the server creates the game. */
export function rememberGameSetup(options: GameSetupOptions): void {
  rememberGameMode(options.mode);
  try {
    const computers = Object.fromEntries(
      sides.map((side) => [
        side,
        { ...options.computers[side], config: withoutSeed(options.computers[side].config) },
      ]),
    );
    localStorage.setItem(GAME_SETUP_KEY, JSON.stringify({ version: 1, ...options, computers }));
  } catch {
    /* Starting a game must also work without writable browser storage. */
  }
}
