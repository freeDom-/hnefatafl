import { recordedTrainingParameters, type TrainingConfig } from './trainingPresets';
import { object, jobRuleset } from './trainingDashboardData';
import type { DeviceInfo, Job } from './types';
import { validTrainingNumbers } from './TrainingFields';

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
  if (
    search &&
    (!Object.keys(search).length ||
      Object.entries(search).some(([key, value]) => {
        if (
          ![
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
          ].includes(key)
        )
          return true;
        const distribution = object(value);
        if (!distribution) return true;
        const type = distribution.type ?? 'float';
        if (!['categorical', 'int', 'float'].includes(String(type))) return true;
        if (
          Object.keys(distribution).some(
            (key) =>
              !(
                type === 'categorical'
                  ? ['type', 'choices']
                  : type === 'int'
                    ? ['type', 'low', 'high', 'step', 'log']
                    : ['type', 'low', 'high', 'log']
              ).includes(key),
          )
        )
          return true;
        const choices =
          type === 'categorical' ? distribution.choices : [distribution.low, distribution.high];
        if (
          !Array.isArray(choices) ||
          !choices.length ||
          choices.some(
            (value) => typeof value !== 'number' || !validTrainingNumbers({ [key]: value }),
          )
        )
          return true;
        if (type !== 'categorical' && Number(distribution.low) > Number(distribution.high))
          return true;
        if (distribution.log !== undefined && typeof distribution.log !== 'boolean') return true;
        if (distribution.log && Number(distribution.low) <= 0) return true;
        if (
          type === 'int' &&
          (choices.some((value) => !Number.isInteger(value)) ||
            (distribution.step !== undefined &&
              (!Number.isInteger(distribution.step) || Number(distribution.step) < 1)) ||
            (distribution.log && Number(distribution.step ?? 1) !== 1))
        )
          return true;
        return false;
      }))
  ) {
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
