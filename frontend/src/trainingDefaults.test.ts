import { expect, it } from 'vitest';
import { changeTrainingPlan, trainingConfig, type TrainingPreset } from './trainingPresets';
import { evaluationDefaults } from './EvaluationSettings';
import { profileConfiguration } from './gameSetupPreference';
import type { AIProfile } from './types';

const rulesets = ['brandubh', 'tablut', 'copenhagen'];
const presets: TrainingPreset[] = ['recommended', 'smoke', 'beginner', 'balanced'];

it('uses random D4 and a ten-second MCTS budget as the new-job defaults', () => {
  for (const ruleset of rulesets) {
    for (const preset of presets) {
      const config = trainingConfig(ruleset, preset);
      expect(config.training_symmetry_mode).toBe('random_d4');
      if (preset === 'smoke') {
        // Quick check keeps its explicit small budget for fast real updates.
        expect(config.mcts_time_limit).toBe(0.1);
        expect(config.evaluation_time_limit).toBe(0.1);
      } else {
        expect(config.mcts_time_limit).toBe(10);
        expect(config.evaluation_time_limit).toBe(10);
      }
    }
  }
});

it('preserves an explicit symmetry choice when changing the training plan', () => {
  const off = { ...trainingConfig('brandubh', 'recommended'), training_symmetry_mode: 'off' };
  expect(changeTrainingPlan(off, 'tablut', 'recommended').training_symmetry_mode).toBe('off');
  const on = { ...trainingConfig('brandubh', 'recommended'), training_symmetry_mode: 'random_d4' };
  expect(changeTrainingPlan(on, 'copenhagen', 'balanced').training_symmetry_mode).toBe('random_d4');
});

it('keeps the ten-second MCTS default in evaluation and named tournament presets', () => {
  expect(evaluationDefaults.evaluation_time_limit).toBe(10);
  // Jarl uses alpha-beta; its own deadline is not an MCTS default.
  expect(evaluationDefaults.evaluation_jarl_time_limit).toBe(2);
  const profile: AIProfile = {
    id: 'mcts',
    name: 'Seer',
    algorithm: 'mcts',
    difficulty: 'Advanced',
    parameters: {},
    supported_rulesets: rulesets,
  };
  const config = profileConfiguration(profile);
  expect(config.time_limit).toBe(10);
  // The named tournament preset matches the base training self-play budget.
  expect(config.iterations).toBe(24);
});
