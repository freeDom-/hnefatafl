import { expect, it } from 'vitest';
import { filterJobs, jobProgress, mergeJobSummary } from './trainingDashboardData';
import type { Job, Model } from './types';

function complete(status: string): Job {
  const history = Array.from({ length: 1200 }, (_, i) => ({ iteration: i + 1, loss: i / 1200 }));
  return {
    id: 'imported',
    status,
    summary: false,
    kind: 'tune',
    config: {
      study_name: 'Complete study',
      training: { model_name: 'Retained config', opponents: ['jarl'] },
    },
    progress: {
      iteration: 1200,
      history,
      metrics: {
        history,
        evaluation: { score: 0.8, roles: { attacker: 0.7, defender: 0.9 }, games: history },
        performance: { history, rates: { cpu: 10, gpu: 20 } },
      },
      trial_progress: {
        trial: 4,
        iteration: 1200,
        history,
        performance: { rates: { cpu: 10, gpu: 20 } },
      },
      best_models: [{ model_id: 'best', iteration: 1000, score: 0.8 }],
      experiment_identity: { version: 2, source: { weights: 'immutable', seed: 17 } },
      source_configuration: { training: { learning_rate: 0.001, opponents: ['jarl'] } },
      best_configuration: { learning_rate: 0.0005 },
      parameter_importance: { learning_rate: 0.9 },
      trials: [{ number: 4, history }],
    },
    metrics: {
      history,
      evaluation: { score: 0.8, roles: { attacker: 0.7, defender: 0.9 }, games: history },
      performance: { history, rates: { cpu: 10, gpu: 20 } },
    },
    result: { history, winner: { model: 'best', diagnostics: history } },
    software: {
      worker: { package_version: '0.36.0', git_revision: 'saved' },
      server: { package_version: '0.36.0', git_revision: null },
      version_override_active: false,
    },
  };
}

it.each(['completed', 'cancelled', 'failed', 'running', 'cancelling', 'pending'])(
  'preserves complete %s data across repeated projected updates',
  (status) => {
    const full = complete(status);
    let merged = full;
    for (let i = 1; i <= 3; i++) {
      merged = mergeJobSummary(merged, {
        id: full.id,
        status: i === 3 ? 'completed' : status,
        summary: true,
        name: `Rename ${i}`,
        last_reported_at: `2026-11-07T00:00:0${i}Z`,
        config: { training: { model_name: 'Current config' } },
        progress: {
          iteration: 1200 + i,
          metrics: { evaluation: { score: i }, performance: { rates: { cpu: i } } },
          trial_progress: { trial: 4, iteration: 1200 + i, performance: { rates: { cpu: i } } },
          best_models: [{ model_id: 'best' }, { model_id: 'new' }],
        },
        metrics: { evaluation: { score: i }, performance: { rates: { cpu: i } } },
      });
      expect(merged.summary).toBe(false);
      expect(merged.progress?.history).toBe(full.progress?.history);
      expect(merged.metrics?.history).toBe(full.metrics?.history);
      expect(merged.result).toBe(full.result);
      expect(merged.progress?.metrics).toMatchObject({
        evaluation: { score: i, roles: { attacker: 0.7 }, games: full.progress?.history },
        performance: { history: full.progress?.history, rates: { cpu: i, gpu: 20 } },
      });
      expect(merged.progress?.trial_progress).toMatchObject({
        trial: 4,
        iteration: 1200 + i,
        history: full.progress?.history,
        performance: { rates: { cpu: i, gpu: 20 } },
      });
      expect(merged.metrics).toMatchObject({
        evaluation: { score: i, roles: { defender: 0.9 }, games: full.metrics?.history },
        performance: { history: full.metrics?.history, rates: { cpu: i, gpu: 20 } },
      });
      expect(merged.config).toMatchObject({
        study_name: 'Complete study',
        training: { model_name: 'Current config', opponents: ['jarl'] },
      });
      expect(merged.progress?.best_models).toEqual([
        { model_id: 'best', iteration: 1000, score: 0.8 },
        { model_id: 'new' },
      ]);
      for (const key of [
        'experiment_identity',
        'source_configuration',
        'best_configuration',
        'parameter_importance',
        'trials',
      ])
        expect(merged.progress?.[key]).toBe(full.progress?.[key]);
      expect(merged.name).toBe(`Rename ${i}`);
      expect(merged.progress?.iteration).toBe(1200 + i);
      expect(merged.last_reported_at).toBe(`2026-11-07T00:00:0${i}Z`);
      expect(merged.status).toBe(i === 3 ? 'completed' : status);
    }
    expect(full.progress?.iteration).toBe(1200);
  },
);

it('does not inherit another job’s detail or mark an unloaded summary as complete', () => {
  const summary: Job = {
    id: 'other',
    status: 'completed',
    summary: true,
    progress: { iteration: 5 },
  };
  expect(mergeJobSummary(complete('completed'), summary)).toBe(summary);
  expect(mergeJobSummary(null, summary)).toBe(summary);
  expect(mergeJobSummary(summary, { ...summary, progress: { iteration: 6 } }).summary).toBe(true);
  const detail = complete('completed');
  expect(mergeJobSummary(summary, detail)).toBe(detail);
});

it('keeps loaded arrays when a projection contains an empty or shorter array', () => {
  const full = complete('completed');
  const merged = mergeJobSummary(full, {
    id: full.id,
    status: 'completed',
    summary: true,
    progress: { history: [], metrics: { evaluation: { games: [] } } },
    metrics: { history: [{ iteration: 1200 }] },
    result: { history: [] },
  });
  expect(merged.progress?.history).toBe(full.progress?.history);
  expect(merged.metrics?.history).toBe(full.metrics?.history);
  expect(merged.result?.history).toBe(full.result?.history);
  expect((merged.progress?.metrics as Record<string, unknown>).evaluation).toEqual(
    (full.progress?.metrics as Record<string, unknown>).evaluation,
  );
});

it('retains complete software and deleted-model metadata missing from a summary', () => {
  const full = complete('completed');
  const deleted = { model_id: null, iteration: 900, score: 0.6 };
  full.progress!.best_models = [...(full.progress!.best_models as unknown[]), deleted];
  const projected = JSON.parse(
    JSON.stringify({
      id: full.id,
      status: 'completed',
      summary: true,
      software: { worker: { package_version: '0.36.1' } },
      progress: { best_models: [{ model_id: 'new' }] },
    }),
  ) as Job;
  const merged = mergeJobSummary(full, projected);
  expect(merged.software).toEqual({
    ...full.software,
    worker: { ...full.software!.worker, package_version: '0.36.1' },
  });
  expect(merged.progress!.best_models).toEqual([
    { model_id: 'best', iteration: 1000, score: 0.8 },
    deleted,
    { model_id: 'new' },
  ]);
});

it('does not carry one tuning trial’s history into its successor', () => {
  const full = complete('running');
  const merged = mergeJobSummary(full, {
    id: full.id,
    status: 'running',
    summary: true,
    progress: { trial_progress: { trial: 5, iteration: 1, stage: 'self_play' } },
  });
  expect(merged.progress?.trial_progress).toEqual({ trial: 5, iteration: 1, stage: 'self_play' });
  expect(merged.progress?.history).toBe(full.progress?.history);
});

it('replaces authoritative live phase counters while retaining analysis detail', () => {
  const full = complete('running');
  full.progress!.phase_progress = { game: 3, ply: 20 };
  let merged = mergeJobSummary(full, {
    id: full.id,
    status: 'running',
    summary: true,
    progress: { stage: 'optimization', phase_progress: { epoch: 1, batch: 2 } },
  });
  expect(merged.progress?.phase_progress).toEqual({ epoch: 1, batch: 2 });
  merged = mergeJobSummary(merged, {
    id: full.id,
    status: 'running',
    summary: true,
    progress: { stage: 'evaluation', phase_progress: {} },
  });
  expect(merged.progress?.phase_progress).toEqual({});
  expect(merged.progress?.history).toBe(full.progress?.history);
  expect(merged.metrics?.history).toBe(full.metrics?.history);
  const completed = { ...full, status: 'completed' };
  const retained = mergeJobSummary(completed, {
    id: full.id,
    status: 'completed',
    summary: true,
    progress: { phase_progress: {} },
  });
  expect(retained.progress?.phase_progress).toEqual({ game: 3, ply: 20 });
});

it('clears an inactive trial’s live timers while retaining its complete analysis', () => {
  const full = complete('running');
  full.progress!.trial_progress = {
    ...(full.progress!.trial_progress as Record<string, unknown>),
    stage: 'self_play',
    duration: 90,
    active_iteration: 1200,
    phase_elapsed: 4,
    phase_progress: { game: 3, ply: 20 },
    iteration_started_at: '2026-11-07T00:00:00Z',
    iteration_elapsed: 10,
  };
  const summary: Job = {
    id: full.id,
    status: 'running',
    summary: true,
    progress: {
      stage: 'between_trials',
      duration: 222,
      trial_progress: { trial: 4, iteration: 1200 },
    },
  };
  const merged = mergeJobSummary(full, summary);
  const trial = merged.progress!.trial_progress as Record<string, unknown>;
  expect(jobProgress(merged).stage).toBe('between_trials');
  expect(jobProgress(merged).duration).toBe(222);
  for (const key of [
    'stage',
    'duration',
    'active_iteration',
    'phase_elapsed',
    'phase_progress',
    'iteration_started_at',
    'iteration_elapsed',
  ])
    expect(trial).not.toHaveProperty(key);
  expect(trial.history).toBe(full.progress?.history);
  expect(trial.performance).toEqual({ rates: { cpu: 10, gpu: 20 } });
  const terminal = mergeJobSummary(
    { ...full, status: 'completed' },
    { ...summary, status: 'completed' },
  );
  expect((terminal.progress!.trial_progress as Record<string, unknown>).duration).toBe(90);
});

it('searches ordinary, resumed, remote and imported jobs by their associated catalog names', () => {
  const models: Model[] = [
    { id: 'inference', name: 'Current raven', ruleset: 'tablut' },
    { id: 'best', name: 'Retained champion', ruleset: 'tablut' },
    { id: 'tar-model', name: 'Migrated guardian', ruleset: 'tablut' },
    { id: 'zip-model', name: 'Legacy warden', ruleset: 'tablut' },
    { id: 'deduplicated', name: 'Deduplicated stag', ruleset: 'tablut' },
    { id: 'unrelated', name: 'Unrelated fox', ruleset: 'tablut' },
  ];
  const ordinary: Job = {
    id: 'ordinary',
    status: 'completed',
    model_id: 'inference',
    progress: { best_models: [{ model_id: 'best' }, { model_id: null }] },
  };
  const jobs: Job[] = [
    ordinary,
    { ...ordinary, id: 'resumed', config: { resume: 'source:last.pt' } },
    { ...ordinary, id: 'remote', status: 'running', execution: 'remote', worker_id: 'worker' },
    {
      id: 'tar-imported',
      status: 'completed',
      summary: true,
      model_id: 'tar-model',
      progress: { best_models: [{ model_id: 'deduplicated' }] },
    },
    {
      id: 'zip-imported',
      status: 'failed',
      summary: true,
      model_id: 'zip-model',
      progress: { best_models: [{ model_id: 'best' }, { model_id: 'deleted' }] },
    },
    { id: 'reused', status: 'cancelled', summary: true, model_id: 'deduplicated' },
  ];
  const matching = (query: string) =>
    filterJobs(jobs, query, 'all', 'active', models)
      .map((job) => job.id)
      .sort();
  expect(matching('Current raven')).toEqual(['ordinary', 'remote', 'resumed']);
  expect(matching('Retained champion')).toEqual(['ordinary', 'remote', 'resumed', 'zip-imported']);
  expect(matching('Migrated guardian')).toEqual(['tar-imported']);
  expect(matching('Legacy warden')).toEqual(['zip-imported']);
  expect(matching('Deduplicated stag')).toEqual(['reused', 'tar-imported']);
  expect(matching('Unrelated fox')).toEqual([]);
});
