import { expect, it } from 'vitest';
import { mergeJobSummary } from './trainingDashboardData';
import type { Job } from './types';

const history = [{ iteration: 1, identity: 'old' }];
const full = (run?: string | null): Job => ({
  id: 'job',
  kind: 'tune',
  status: 'running',
  summary: false,
  progress: {
    run,
    history,
    trial_progress: { trial: 4, run, history, metrics: { history, loss: 0.5 } },
  },
  metrics: { history },
  result: { history },
});

it.each([
  ['run-A', 'run-A', true],
  ['run-A', 'run-B', false],
  [undefined, 'run-A', false],
  ['run-A', undefined, false],
  [null, 'run-A', false],
  ['run-A', null, false],
  [undefined, undefined, true],
  [null, undefined, true],
  [null, null, true],
] as const)('merges trial run identities %s → %s safely', (oldRun, newRun, compatible) => {
  const previous = full(oldRun);
  const update: Job = {
    id: previous.id,
    status: 'running',
    summary: true,
    progress: {
      run: newRun,
      iteration: 2,
      trial_progress: { run: newRun, trial: 4, iteration: 2 },
    },
  };
  const merged = mergeJobSummary(previous, update);
  const trial = merged.progress!.trial_progress as Record<string, unknown>;
  expect(trial.history).toBe(compatible ? history : undefined);
  expect(trial.metrics).toEqual(compatible ? { history, loss: 0.5 } : undefined);
  expect(merged.progress!.history).toBe(history);
  expect(merged.metrics!.history).toBe(history);
  expect(merged.result!.history).toBe(history);
  expect(trial.iteration).toBe(2);
});

it('uses a new trial’s own history without merging the previous trial’s metrics', () => {
  const previous = full('run-A');
  const own = [{ iteration: 1, identity: 'new' }];
  const merged = mergeJobSummary(previous, {
    id: previous.id,
    status: 'completed',
    summary: true,
    progress: { run: 'run-B', trial_progress: { run: 'run-B', trial: 5, history: own } },
  });
  expect((merged.progress!.trial_progress as Record<string, unknown>).history).toBe(own);
  expect((merged.progress!.trial_progress as Record<string, unknown>).metrics).toBeUndefined();
  expect(merged.progress!.history).toBe(history);
});

it('uses the parent trial number for legacy trial reports without run or nested trial IDs', () => {
  const previous: Job = {
    ...full(),
    progress: { trial: 4, history, trial_progress: { history, metrics: { history } } },
  };
  const nextHistory = [{ iteration: 1, identity: 'new' }];
  const merged = mergeJobSummary(previous, {
    id: previous.id,
    status: 'running',
    summary: true,
    progress: { trial: 5, trial_progress: { history: nextHistory } },
  });
  expect(merged.progress!.history).toBe(history);
  expect((merged.progress!.trial_progress as Record<string, unknown>).history).toBe(nextHistory);
  expect((merged.progress!.trial_progress as Record<string, unknown>).metrics).toBeUndefined();
});

it('keeps an identified completed trial while the study prepares the next trial', () => {
  const previous = full('run-A');
  previous.progress!.trial = 4;
  const merged = mergeJobSummary(previous, {
    id: previous.id,
    kind: 'tune',
    status: 'running',
    summary: true,
    progress: {
      trial: 5,
      run: 'study-run',
      stage: 'preparing_trial',
      trial_progress: { run: 'run-A', trial: 4 },
    },
  });
  expect(merged.summary).toBe(false);
  expect((merged.progress!.trial_progress as Record<string, unknown>).history).toBe(history);
  expect(merged.progress!.history).toBe(history);
});

it('treats a newly introduced identified report as partial detail', () => {
  const previous: Job = { id: 'job', kind: 'train', status: 'completed', result: { history } };
  const merged = mergeJobSummary(previous, {
    id: previous.id,
    status: 'running',
    summary: true,
    progress: { run: 'new' },
  });
  expect(merged.summary).toBe(true);
  expect(merged.result).toBeUndefined();
});

it.each([
  ['execution-A', 'execution-B'],
  [undefined, 'execution-B'],
  ['execution-A', undefined],
] as const)('does not present old execution reports after %s → %s', (oldRun, newRun) => {
  const previous: Job = { ...full(oldRun), kind: 'train', logs: 'old execution log' };
  const merged = mergeJobSummary(previous, {
    id: previous.id,
    kind: 'train',
    status: 'completed',
    summary: true,
    progress: { run: newRun, iteration: 2 },
  });
  expect(merged.progress!.history).toBeUndefined();
  expect(merged.metrics?.history).toBeUndefined();
  expect(merged.result?.history).toBeUndefined();
  expect(merged.logs).toBeUndefined();
  expect(merged.summary).toBe(true);
});

it.each(['same', 'new', 'missing', 'null'] as const)(
  'keeps model attributes scoped to their ID (%s)',
  (mode) => {
    const previous: Job = {
      ...full('run-A'),
      progress: {
        run: 'run-A',
        history,
        model: {
          id: 'model-A',
          name: 'Old model',
          path: '/owned/model-A.pt',
          architecture: { channels: 4 },
        },
      },
    };
    const model =
      mode === 'null'
        ? null
        : mode === 'missing'
          ? {}
          : { id: mode === 'same' ? 'model-A' : 'model-B' };
    let merged = previous;
    for (let i = 0; i < 3; i++)
      merged = mergeJobSummary(merged, {
        id: previous.id,
        status: 'completed',
        summary: true,
        progress: { run: 'run-A', iteration: i + 2, model },
      });
    expect(merged.progress!.model).toEqual(mode === 'same' ? previous.progress!.model : model);
    expect(merged.progress!.history).toBe(history);
  },
);

it('replaces unknown or explicitly removed model identities without borrowing attributes', () => {
  const previous: Job = {
    ...full('run-A'),
    model_id: 'model-A',
    progress: { run: 'run-A', history, model: { name: 'Unknown old model', path: '/old.pt' } },
  };
  const changed = mergeJobSummary(previous, {
    id: previous.id,
    status: 'completed',
    summary: true,
    model_id: 'model-B',
    progress: { run: 'run-A', model: { id: 'model-B' } },
  });
  expect(changed.progress!.model).toEqual({ id: 'model-B' });
  const removed = mergeJobSummary(changed, {
    id: previous.id,
    status: 'completed',
    summary: true,
    model_id: null,
    progress: { run: 'run-A', model: { id: null } },
  });
  expect(removed.model_id).toBeNull();
  expect(removed.progress!.model).toEqual({ id: null });
  expect(removed.progress!.history).toBe(history);
});
