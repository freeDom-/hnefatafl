import { expect, it } from 'vitest';
import { changeTrainingPlan, trainingConfig, type TrainingPreset } from './trainingPresets';
import { evaluationDefaults } from './EvaluationSettings';
import { profileConfiguration } from './profileConfiguration';
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

it('keeps the ten-second MCTS training evaluation default while the Seer preset stays fast', () => {
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
  // The named tournament/play preset caps search at 5,000 visits within 2 seconds.
  expect(config.time_limit).toBe(2);
  expect(config.iterations).toBe(5000);
});
