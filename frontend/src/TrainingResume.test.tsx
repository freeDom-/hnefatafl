import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import Training from './Training';
import type { Job, Model, TrainingWorker } from './types';
import { setLanguage } from './i18n';
import { trainingConfig } from './trainingPresets';

const job: Job = {
  id: 'source-job',
  kind: 'train',
  status: 'completed',
  model_id: 'model-one',
  checkpoint: 'source-job:last.pt',
  config: { model_name: 'Freydis', iterations: 10 },
  progress: { iteration: 5 },
};
const info: ResumeCheckpoint = {
  job_id: job.id,
  checkpoint: job.checkpoint,
  completed_iteration: 5,
  ruleset: 'brandubh',
  channels: 32,
  blocks: 2,
  optimizer: 'adamw',
  learning_rate: 0.0007,
  weight_decay: 0.002,
  momentum: 0.9,
  model_name: 'Freydis',
  can_resume: true,
};
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
const worker = (id: string, capabilities: TrainingWorker['capabilities']): TrainingWorker => ({
  id,
  name: `${id} raven`,
  capacity: 1,
  status: 'online',
  last_seen: '2026-10-22T08:00:00Z',
  active_jobs: 0,
  capabilities,
  compatibility: {
    eligible: true,
    code: 'compatible',
    reason: 'Compatible',
    mismatches: [],
    protocol_compatible: true,
    override_enabled: false,
    override_active: false,
    worker_version: '0.28.0',
    server_version: '0.28.0',
    worker_git_revision: null,
    server_git_revision: null,
  },
});
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});

it('reviews the actual checkpoint and sends only a valid total target to the job resume route', async () => {
  const next = { ...job, id: 'new-job', status: 'running' };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? next : info),
  );
  vi.stubGlobal('fetch', fetcher);
  const onStarted = vi.fn();
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={onStarted} />);
  const target = await screen.findByRole('spinbutton', { name: 'Target total iterations' });
  expect(screen.getByText('32 channels · 2 blocks')).toBeInTheDocument();
  expect(target).toHaveValue(10);
  expect(target).toHaveAttribute('min', '6');
  expect(screen.getByLabelText('Learning rate')).toHaveValue(0.0007);
  expect(screen.getByLabelText('Weight decay')).toHaveValue(0.002);
  expect(screen.getByLabelText('Momentum')).toHaveValue(0.9);
  expect(screen.getByLabelText('Momentum')).toBeDisabled();
  expect(
    screen.getByText('Momentum applies to SGD only. Adam and AdamW keep their saved moments.'),
  ).toBeInTheDocument();
  fireEvent.change(target, { target: { value: '5' } });
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
  fireEvent.change(target, { target: { value: '12' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalledWith(next, 5, 12));
  const sent = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(sent[0]).toBe('/api/training/jobs/source-job/resume');
  expect(JSON.parse(String(sent[1]?.body))).toEqual({ iterations: 12 });
});

it('prefills SGD settings from the checkpoint and submits changed optimizer hyperparameters', async () => {
  const saved = { ...info, optimizer: 'sgd', momentum: 0.65 };
  const next = { ...job, id: 'resumed-sgd', status: 'running' };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? next : saved),
  );
  const onStarted = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={onStarted} />);
  expect(await screen.findByLabelText('Learning rate')).toHaveValue(0.0007);
  expect(screen.getByLabelText('Weight decay')).toHaveValue(0.002);
  expect(screen.getByLabelText('Momentum')).toBeEnabled();
  expect(screen.getByLabelText('Momentum')).toHaveValue(0.65);
  fireEvent.change(screen.getByLabelText('Learning rate'), { target: { value: '0.00005' } });
  fireEvent.change(screen.getByLabelText('Weight decay'), { target: { value: '0.01' } });
  fireEvent.change(screen.getByLabelText('Momentum'), { target: { value: '0.8' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalledWith(next, 5, 10));
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
    iterations: 10,
    learning_rate: 0.00005,
    weight_decay: 0.01,
    momentum: 0.8,
  });
});

it.each([
  ['Learning rate', '0'],
  ['Learning rate', '1.1'],
  ['Learning rate', ''],
  ['Weight decay', '-0.1'],
  ['Weight decay', '1.1'],
  ['Weight decay', ''],
  ['Momentum', '-0.1'],
  ['Momentum', '1.1'],
  ['Momentum', ''],
])('does not resume with an invalid %s value (%s)', async (label, value) => {
  const fetcher = vi.fn(() => json({ ...info, optimizer: 'sgd' }));
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  const input = await screen.findByLabelText(label);
  fireEvent.change(input, { target: { value } });
  const button = screen.getByRole('button', { name: 'Continue training' });
  expect(button).toBeDisabled();
  fireEvent.submit(button.closest('form')!);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('disables unavailable accelerators for the server and enables all options for any remote worker', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json({ ...info, config: { device: 'cpu' } })),
  );
  render(
    <TrainingResumeDialog
      job={job}
      localDevice={{ cuda_available: false, mps_available: false }}
      onClose={vi.fn()}
      onStarted={vi.fn()}
    />,
  );
  const device = await screen.findByLabelText('Training device');
  expect(within(device).getByRole('option', { name: 'NVIDIA CUDA (unavailable)' })).toBeDisabled();
  expect(within(device).getByRole('option', { name: 'Apple Metal (unavailable)' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Run training on'), { target: { value: 'remote' } });
  expect(within(device).getByRole('option', { name: 'NVIDIA CUDA' })).toBeEnabled();
  expect(within(device).getByRole('option', { name: 'Apple Metal' })).toBeEnabled();
});

it('reflects the selected remote worker capabilities for the device options', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json({ ...info, config: { device: 'cpu' } })),
  );
  render(
    <TrainingResumeDialog
      job={job}
      localDevice={{ cuda_available: true, mps_available: true }}
      workers={[worker('cpu-node', { cuda_available: false, mps_available: false })]}
      onClose={vi.fn()}
      onStarted={vi.fn()}
    />,
  );
  const device = await screen.findByLabelText('Training device');
  expect(within(device).getByRole('option', { name: 'NVIDIA CUDA' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Run training on'), {
    target: { value: 'worker:cpu-node' },
  });
  expect(within(device).getByRole('option', { name: 'NVIDIA CUDA (unavailable)' })).toBeDisabled();
  expect(within(device).getByRole('option', { name: 'Apple Metal (unavailable)' })).toBeDisabled();
});

it('localizes the editable resume settings and optimizer explanation in German', async () => {
  setLanguage('de', false);
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(info)),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByLabelText('Lernrate')).toHaveValue(0.0007);
  expect(screen.getByLabelText('Gewichtsverfall')).toHaveValue(0.002);
  expect(
    screen.getByText(
      'Momentum gilt nur für SGD. Adam und AdamW behalten ihre gespeicherten Momente.',
    ),
  ).toBeInTheDocument();
});

it('explains a missing full checkpoint and never offers inference weights as resumable state', async () => {
  const fetcher = vi.fn(() =>
    json({ ...info, can_resume: false, reason: 'The saved checkpoint was not found.' }),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByRole('alert');
  expect(screen.getByRole('alert')).toHaveTextContent('The saved checkpoint was not found.');
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
  expect(screen.queryByRole('spinbutton')).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('opens checkpoint resumption from the linked model card and keeps an imported model inference-only', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/training/jobs') return json([job]);
      if (path.endsWith('/resume')) return json(info);
      return json([]);
    }),
  );
  const models: Model[] = [
    { id: 'model-one', name: 'Freydis', ruleset: 'brandubh' },
    { id: 'imported-model', name: 'Imported learner', ruleset: 'brandubh' },
  ];
  render(<Training rulesets={[]} models={models} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  await screen.findByRole('tab', { name: 'Overview' });
  fireEvent.click(screen.getByRole('button', { name: /Model collection/ }));
  const button = await screen.findByRole('button', { name: 'Resume training for Freydis' });
  expect(screen.queryByRole('button', { name: 'Resume training for Imported learner' })).toBeNull();
  fireEvent.click(button);
  expect(await screen.findByRole('dialog', { name: 'Resume training' })).toBeInTheDocument();
  expect(await screen.findByRole('spinbutton', { name: 'Target total iterations' })).toHaveValue(
    10,
  );
});

it('uses all saved training fields and submits only editable changes without replacing checkpoint identity', async () => {
  const config = {
    ...trainingConfig('brandubh', 'smoke'),
    channels: 12,
    blocks: 3,
    seed: 918,
    model_name: 'Freydis',
    iterations: 14,
    games: 7,
    epochs: 4,
    batch_size: 13,
    mcts_iterations: 11,
    mcts_time_limit: 0.35,
    exploration: 2.3,
    temperature: 0.8,
    dirichlet_alpha: 0.13,
    dirichlet_fraction: 0.17,
    replay_buffer_size: 217,
    weight_decay: 0.002,
    learning_rate: 0.0007,
    evaluation_frequency: 3,
    checkpoint_frequency: 4,
    evaluation_games: 6,
    evaluation_iterations: 9,
    max_game_plies: 31,
    evaluation_max_plies: 41,
    cpu_threads: 3,
    device: 'cpu',
    mixed_precision: false,
    save_all_best: true,
  };
  const saved = { ...info, config, channels: 12, blocks: 3, seed: 918 };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? job : saved),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByLabelText('Target total iterations')).toHaveValue(14);
  for (const [label, value] of [
    ['Self-play games per iteration', 7],
    ['Epochs per iteration', 4],
    ['Batch size', 13],
    ['MCTS iterations per move', 11],
    ['Search time limit (seconds)', 0.35],
    ['MCTS exploration', 2.3],
    ['Self-play temperature', 0.8],
    ['Exploration noise alpha', 0.13],
    ['Exploration noise fraction', 0.17],
    ['Replay buffer positions', 217],
    ['Checkpoint every (iterations)', 4],
    ['Evaluate every (iterations)', 3],
    ['Evaluation games (even)', 6],
    ['Evaluation search iterations', 9],
    ['Self-play move limit', 31],
    ['Evaluation move limit', 41],
    ['CPU threads', 3],
  ] as const)
    expect(screen.getByLabelText(label)).toHaveValue(value);
  expect(screen.getByLabelText('Training device')).toHaveValue('cpu');
  expect(screen.getByLabelText('Mixed precision when supported')).not.toBeChecked();
  expect(screen.getByLabelText('Save every new best model')).toBeChecked();
  expect(screen.getByText('918')).toBeInTheDocument();
  expect(screen.queryByRole('spinbutton', { name: 'Random seed' })).toBeNull();
  expect(screen.queryByRole('spinbutton', { name: 'Network channels' })).toBeNull();
  expect(screen.queryByRole('spinbutton', { name: 'Residual blocks' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: 'Optimizer' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: 'Battlefield' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Batch size'), { target: { value: '17' } });
  fireEvent.change(screen.getByLabelText('Epochs per iteration'), { target: { value: '5' } });
  fireEvent.change(screen.getByLabelText('MCTS exploration'), { target: { value: '3.2' } });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), { target: { value: '8' } });
  fireEvent.change(screen.getByLabelText('Training device'), { target: { value: 'auto' } });
  fireEvent.click(screen.getByLabelText('Mixed precision when supported'));
  fireEvent.click(screen.getByLabelText('Save every new best model'));
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
    iterations: 14,
    batch_size: 17,
    epochs: 5,
    exploration: 3.2,
    evaluation_games: 8,
    device: 'auto',
    mixed_precision: true,
    save_all_best: false,
  });
});

it('validates the same integer bounds and even evaluation budget as new training', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      json({ ...info, config: { ...trainingConfig('brandubh', 'smoke'), iterations: 10 } }),
    ),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  const batch = await screen.findByLabelText('Batch size');
  fireEvent.change(batch, { target: { value: '2.5' } });
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
  fireEvent.change(batch, { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), { target: { value: '3' } });
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
});

it('keeps the historical preset without exposing a selector or resetting effective values', async () => {
  const config = {
    ...trainingConfig('brandubh', 'smoke'),
    games: 7,
    batch_size: 19,
    iterations: 9,
  };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? job : { ...info, config }),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByLabelText('Target total iterations')).toHaveValue(9);
  expect(screen.queryByLabelText('Training preset')).toBeNull();
  expect(screen.getByLabelText('Self-play games per iteration')).toHaveValue(7);
  expect(screen.getByLabelText('Batch size')).toHaveValue(19);
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ iterations: 9 });
});
