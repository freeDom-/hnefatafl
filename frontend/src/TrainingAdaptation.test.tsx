import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import { TrainingLearningView } from './TrainingAnalysisViews';
import { setLanguage, translate } from './i18n';
import { adaptationConfigError, trainingConfig } from './trainingPresets';
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

it('reports the cross-field controller constraints directly', () => {
  const base = trainingConfig('brandubh', 'recommended');
  expect(adaptationConfigError(base)).toBeNull();
  expect(
    adaptationConfigError({
      ...base,
      adaptation: 'hypercontroller',
      evaluation_opponent: 'champion',
    }),
  ).toMatch(/fixed evaluation opponent/);
  expect(
    adaptationConfigError({ ...base, adaptation: 'hypercontroller', evaluation_games: 2 }),
  ).toMatch(/at least 4 evaluation games/);
  expect(
    adaptationConfigError({
      ...base,
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.002,
    }),
  ).toMatch(/must be one of the adaptation candidates/);
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
  ]) {
    expect(translate(source, {}, 'de'), `untranslated ${source}`).not.toBe(source);
  }
});
