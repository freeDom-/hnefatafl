import { recordedTrainingParameters, type TrainingConfig } from './trainingPresets';
import { object, jobRuleset } from './trainingDashboardData';
import type { DeviceInfo, Job } from './types';
import { validTrainingNumbers } from './TrainingFields';

const tunable = [
  'channels',
  'blocks',
  'learning_rate',
  'weight_decay',
  'exploration',
  'temperature',
  'mcts_iterations',
  'batch_size',
  'epochs',
  'dirichlet_alpha',
  'dirichlet_fraction',
];

/** One validator for an Optuna search space, shared with config.json import. */
export function validSearchSpace(search: unknown): boolean {
  const value = object(search);
  if (!value || !Object.keys(value).length) return false;
  return Object.entries(value).every(([key, item]) => {
    if (!tunable.includes(key)) return false;
    const distribution = object(item);
    if (!distribution) return false;
    const type = distribution.type ?? 'float';
    if (!['categorical', 'int', 'float'].includes(String(type))) return false;
    const allowed =
      type === 'categorical'
        ? ['type', 'choices']
        : type === 'int'
          ? ['type', 'low', 'high', 'step', 'log']
          : ['type', 'low', 'high', 'log'];
    if (Object.keys(distribution).some((option) => !allowed.includes(option))) return false;
    const choices =
      type === 'categorical' ? distribution.choices : [distribution.low, distribution.high];
    if (
      !Array.isArray(choices) ||
      !choices.length ||
      choices.some((value) => typeof value !== 'number' || !validTrainingNumbers({ [key]: value }))
    )
      return false;
    if (type !== 'categorical' && Number(distribution.low) > Number(distribution.high))
      return false;
    if (distribution.log !== undefined && typeof distribution.log !== 'boolean') return false;
    if (distribution.log && Number(distribution.low) <= 0) return false;
    return !(
      type === 'int' &&
      (choices.some((value) => !Number.isInteger(value)) ||
        (distribution.step !== undefined &&
          (!Number.isInteger(distribution.step) || Number(distribution.step) < 1)) ||
        (distribution.log && Number(distribution.step ?? 1) !== 1))
    );
  });
}

export function recordedJobParameters(previous: TrainingConfig, job: Job, device?: DeviceInfo) {
  const saved = job.config ?? {};
  const training = object(saved.training) ?? saved;
  const result = recordedTrainingParameters(
    previous,
    {
      name:
        typeof training.model_name === 'string' ? training.model_name : String(previous.model_name),
      ruleset: jobRuleset(job),
      training_config: training,
    },
    device,
  );
  if (!result) return null;
  const tuning = job.kind === 'tune';
  const trials = saved.trials;
  const seed = saved.seed;
  const study = saved.study_name;
  let search = object(saved.search_space);
  if (saved.search_space !== undefined && !search) result.warnings.push('search_space');
  if (search && !validSearchSpace(search)) {
    search = undefined;
    result.warnings.push('search_space');
  }
  if (tuning && result.config.evaluation_opponent !== 'shield_bearer') {
    result.config.evaluation_opponent = 'shield_bearer';
    result.warnings.push('evaluation_opponent');
  }
  if (tuning) {
    if (
      trials !== undefined &&
      !(typeof trials === 'number' && Number.isInteger(trials) && trials >= 1 && trials <= 10000)
    )
      result.warnings.push('trials');
    if (seed !== undefined && !(typeof seed === 'number' && validTrainingNumbers({ seed })))
      result.warnings.push('study_seed');
    if (study !== undefined && !(typeof study === 'string' && study.trim() && study.length <= 100))
      result.warnings.push('study_name');
  }
  return {
    ...result,
    kind: tuning ? ('tune' as const) : ('train' as const),
    trials:
      typeof trials === 'number' && Number.isInteger(trials) && trials >= 1 && trials <= 10000
        ? trials
        : 3,
    seed: typeof seed === 'number' && validTrainingNumbers({ seed }) ? seed : 0,
    studyName:
      typeof study === 'string' && study.trim() && study.length <= 100 ? study : 'hnefatafl-search',
    search,
  };
}
