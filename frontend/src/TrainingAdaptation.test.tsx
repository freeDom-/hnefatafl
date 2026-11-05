import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import { TrainingLearningView } from './TrainingAnalysisViews';
import { setLanguage, translate } from './i18n';
import { adaptationConfigError, trainingConfig, type TrainingConfig } from './trainingPresets';
import type { Job, Ruleset } from './types';

/**
 * The optional learning-rate controller is off by default, incompatible with
 * tuning, and its bounded decision history must render unambiguously. These are
 * behaviour tests over the real form and the real learning view.
 */

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
];
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);

function harness() {
  const posts: Record<string, unknown>[] = [];
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    const base = path.split('?')[0];
    if (base === '/api/training/jobs') {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)));
        return json({ id: 'new-job', status: 'running', config: {} });
      }
      return json([]);
    }
    if (path === '/api/training/workers') return json([]);
    if (path === '/api/training/device') return json({});
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  return { posts };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setLanguage('en', false);
});

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});

it('leaves automatic hyperparameter updates off by default', async () => {
  harness();
  await screen.findByText('Your next opponent starts here.');
  const select = screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' });
  expect(select).toHaveValue('off');
  // The candidate and schedule controls only appear once it is enabled.
  expect(screen.queryByLabelText('Learning rate candidates')).toBeNull();
});

it('submits an enabled controller with its candidates', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  fireEvent.change(screen.getByLabelText('Learning rate candidates'), {
    target: { value: '0.0003,0.0007,0.002' },
  });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), {
    target: { value: '6' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    adaptation: 'hypercontroller',
    adaptation_lr_candidates: '0.0003,0.0007,0.002',
    evaluation_games: 6,
  });
});

it('disables and omits the controller while tuning', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  const select = screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' });
  expect(select).toBeDisabled();
  expect(select).toHaveValue('off');
  expect(
    screen.getByText(/Unavailable while tuning: the study varies the learning rate/),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Start tuning' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((posts[0].training as Record<string, unknown>).adaptation).toBe('off');
});

it('explains an incompatible controller configuration before submitting', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.change(screen.getByRole('combobox', { name: 'Evaluation opponent' }), {
    target: { value: 'champion' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('fixed evaluation opponent');
  expect(posts).toHaveLength(0);
});

it('blocks malformed learning-rate candidates before submitting', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), {
    target: { value: '6' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  const candidates = screen.getByLabelText('Learning rate candidates');
  fireEvent.change(candidates, { target: { value: '0.001,garbage' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Learning rate candidates');
  expect(alert).toHaveTextContent(/decimal numbers/);
  expect(candidates).toHaveAttribute('aria-invalid', 'true');
  expect(posts).toHaveLength(0);
});

it('reports the cross-field controller constraints directly', () => {
  const base = trainingConfig('brandubh', 'recommended');
  expect(adaptationConfigError(base)).toBeNull();
  expect(
    adaptationConfigError({
      ...base,
      adaptation: 'hypercontroller',
      evaluation_opponent: 'champion',
    })?.message,
  ).toMatch(/fixed evaluation opponent/);
  expect(
    adaptationConfigError({ ...base, adaptation: 'hypercontroller', evaluation_games: 2 })?.message,
  ).toMatch(/at least 4 evaluation games/);
  expect(
    adaptationConfigError({
      ...base,
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.002,
    }),
  ).toMatchObject({ field: 'learning_rate' });
});

it('validates every learning-rate candidate like the backend', () => {
  const base = trainingConfig('brandubh', 'recommended');
  const enabled = {
    ...base,
    adaptation: 'hypercontroller',
    evaluation_opponent: 'shield_bearer',
    evaluation_games: 6,
    learning_rate: 0.001,
  };
  const error = (candidates: string, extra: Partial<TrainingConfig> = {}) =>
    adaptationConfigError({ ...enabled, adaptation_lr_candidates: candidates, ...extra });

  // Valid lists, including supported scientific notation and surrounding whitespace.
  expect(error('0.001,0.002,0.003')).toBeNull();
  expect(error(' 0.0003 , 1e-3 , 0.003 ')).toBeNull();
  // Empty entries are skipped exactly like the backend, not treated as zero.
  expect(error('0.001,,0.003')).toBeNull();

  // Trailing garbage and partial numbers are rejected, never filtered away.
  for (const candidates of ['0.001,garbage', '0.001abc,0.003', '0.001,0.002.5.5']) {
    expect(error(candidates)).toMatchObject({ field: 'adaptation_lr_candidates' });
    expect(error(candidates)?.message).toMatch(/decimal numbers/);
  }

  // Non-finite values are rejected by the numeric syntax check.
  for (const candidates of ['0.001,NaN', '0.001,Infinity', '0.001,-Infinity']) {
    expect(error(candidates)).toMatchObject({ field: 'adaptation_lr_candidates' });
    expect(error(candidates)?.message).toMatch(/decimal numbers/);
  }

  // Out-of-range values are rejected after parsing.
  for (const candidates of ['0.001,0', '0.001,2', '0.001,1e-8']) {
    expect(error(candidates)).toMatchObject({ field: 'adaptation_lr_candidates' });
    expect(error(candidates)?.message).toMatch(/between 1e-7 and 1/);
  }

  // Duplicates collapse before the count check.
  expect(error('0.001,0.001')?.message).toMatch(/between 2 and 8 distinct values/);
  expect(error('0.001,0.001,0.003')).toBeNull();
  // A single (or empty) list is insufficient, and more than eight is rejected.
  expect(error('0.001,')).toMatchObject({ field: 'adaptation_lr_candidates' });
  expect(error('0.0001,0.0002,0.0003,0.0004,0.0005,0.0006,0.0007,0.0008,0.0009')?.message).toMatch(
    /between 2 and 8 distinct values/,
  );
  // Eight candidates with lag 3 produce 8**4 contexts, above the backend limit.
  expect(
    error('0.0001,0.0002,0.0003,0.0004,0.001,0.002,0.003,0.01', { adaptation_lag: 3 })?.message,
  ).toMatch(/too many controller contexts/);

  // The configured rate must be one of the parsed candidates.
  expect(error('0.0003,0.002,0.003', { learning_rate: 0.001 })).toMatchObject({
    field: 'learning_rate',
  });
});

const eventsProgress = {
  adaptation: { effective_learning_rate: 0.003 },
  adaptation_events: [
    {
      iteration: 4,
      observation: 0.75,
      previous_learning_rate: 0.001,
      selected_learning_rate: 0.003,
      changed: true,
      skipped: null,
    },
    {
      iteration: 5,
      observation: null,
      previous_learning_rate: 0.003,
      selected_learning_rate: 0.003,
      changed: false,
      skipped: 'missing_observation',
    },
  ],
};

function renderLearning(progress: Record<string, unknown>) {
  return render(
    <TrainingLearningView
      progress={progress}
      history={[
        { iteration: 4, learning_rate: 0.001, effective_learning_rate: 0.003 },
        { iteration: 5, learning_rate: 0.003, effective_learning_rate: 0.003 },
      ]}
      config={{}}
      range={10}
    />,
  );
}

it('shows the effective rate and the used/selected decision history', async () => {
  renderLearning(eventsProgress);
  expect(screen.getByText('Effective learning rate')).toBeInTheDocument();
  expect(screen.getByText('0.003')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  expect(await screen.findByRole('table')).toBeInTheDocument();
  expect(screen.getByText('Used learning rate')).toBeInTheDocument();
  expect(screen.getByText('Selected learning rate')).toBeInTheDocument();
  expect(screen.getByText('Changed')).toBeInTheDocument();
  expect(screen.getByText('No observation')).toBeInTheDocument();
});

it('renders no decision history for a legacy job without events', () => {
  renderLearning({ adaptation: { effective_learning_rate: 0.001 } });
  expect(screen.queryByText('Automatic hyperparameter updates')).toBeNull();
  expect(screen.queryByRole('table')).toBeNull();
});

it('blocks resuming an incompatible controller configuration', async () => {
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.001,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: {
      ...trainingConfig('brandubh', 'recommended'),
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.001,
      evaluation_opponent: 'champion',
      evaluation_games: 4,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(info)),
  );
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByText(/fixed evaluation opponent/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
});

it('explains that a resume learning-rate change moves the controller rate', async () => {
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.001,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: {
      ...trainingConfig('brandubh', 'recommended'),
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.001,
      evaluation_opponent: 'shield_bearer',
      evaluation_games: 4,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(info)),
  );
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByText(/moves the controller’s effective rate/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeEnabled();
});

it('translates the new controller labels and tooltips into German', () => {
  for (const source of [
    'Effective learning rate',
    'Used learning rate',
    'Selected learning rate',
    'Observation',
    'Decision',
    'Changed',
    'Kept',
    'Warm-up',
    'Waiting for cadence',
    'Minimum dwell',
    'No observation',
    'Fallback',
    'Unavailable while tuning: the study varies the learning rate across trials.',
    'Learning rate candidates must be decimal numbers separated by commas.',
    'Learning rate candidates must be between 1e-7 and 1.',
    'Learning rate candidates must contain between 2 and 8 distinct values.',
    'Learning rate candidates and the observation history window produce too many controller contexts; use fewer candidates or a smaller window.',
  ]) {
    expect(translate(source, {}, 'de'), `untranslated ${source}`).not.toBe(source);
  }
});
