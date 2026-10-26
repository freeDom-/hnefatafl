import type { AIProfile, Config } from './types';

/** Named opponents have stable presets; custom opponents keep their own budgets. */
export function profileConfiguration(profile: AIProfile): Config {
  if (profile.id === 'shield_maiden')
    return { algorithm: 'shield_maiden', time_limit: 1, heuristic_version: 1 };
  if (profile.id === 'baseline') return { algorithm: 'baseline', time_limit: 1 };
  if (profile.id === 'alpha_beta') return { algorithm: 'alpha_beta', depth: 5, time_limit: 2 };
  if (profile.id === 'mcts')
    return {
      algorithm: 'mcts',
      iterations: 5000,
      exploration: 1.4,
      temperature: 0,
      rollout_depth: 8,
      time_limit: 2,
    };
  return { ...profile.parameters, algorithm: profile.algorithm };
}
