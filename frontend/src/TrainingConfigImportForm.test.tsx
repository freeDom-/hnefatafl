import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import { setLanguage } from './i18n';
import { trainingConfig } from './trainingPresets';
import type { Job, Ruleset } from './types';

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
];
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
const file = (value: unknown) =>
  new File([JSON.stringify(value)], 'config.json', { type: 'application/json' });
const input = () => screen.getByLabelText('Import config.json') as HTMLInputElement;

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
  vi.restoreAllMocks();
  setLanguage('en', false);
});

it('imports an exporter-compatible train config into the new-job form without starting it', async () => {
  const created = { id: 'new-job', status: 'running', config: {} };
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/training/device') return json({});
    if (path === '/api/training/workers') return json([]);
    if (path === '/api/training/jobs') return json(init?.method === 'POST' ? created : []);
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  await screen.findByText('Your next opponent starts here.');
  const exported = {
    ...trainingConfig('brandubh', 'smoke'),
    model_name: 'Imported raven',
    iterations: 9,
    batch_size: 17,
    save_all_best: false,
    dirichlet_fraction: 0,
    seed: 0,
  };
  fireEvent.change(input(), { target: { files: [file(exported)] } });
  expect(await screen.findByText(/Configuration imported/)).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Model name' })).toHaveValue('Imported raven');
  expect(screen.getByRole('spinbutton', { name: 'Training iterations' })).toHaveValue(9);
  expect(screen.getByRole('spinbutton', { name: 'Batch size' })).toHaveValue(17);
  expect(screen.getByRole('checkbox', { name: 'Save every new best model' })).not.toBeChecked();
  expect(screen.getByRole('spinbutton', { name: 'Random seed' })).toHaveValue(0);
  expect(
    fetcher.mock.calls.filter(
      ([path, init]) => path === '/api/training/jobs' && init?.method === 'POST',
    ),
  ).toHaveLength(0);
});

it('localizes the config import action in German', async () => {
  setLanguage('de', false);
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(path === '/api/training/jobs' ? [] : path === '/api/training/workers' ? [] : {}),
    ),
  );
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  expect(await screen.findByLabelText('config.json importieren')).toBeInTheDocument();
});

it('imports an optimization config and fills the tuning fields', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(path === '/api/training/workers' ? [] : path === '/api/training/jobs' ? [] : {}),
    ),
  );
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  await screen.findByText('Your next opponent starts here.');
  const search = { learning_rate: { type: 'float', low: 0.0001, high: 0.01, log: true } };
  fireEvent.change(input(), {
    target: {
      files: [
        file({
          trials: 6,
          study_name: 'raven-search',
          seed: 3,
          search_space: search,
          training: { ...trainingConfig('brandubh', 'smoke'), batch_size: 12 },
          output_dir: '/srv/jobs/abc',
        }),
      ],
    },
  });
  expect(await screen.findByText(/Configuration imported/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Tune parameters' })).toHaveClass('active');
  expect(screen.getByRole('spinbutton', { name: 'Trial budget' })).toHaveValue(6);
  expect(screen.getByRole('textbox', { name: 'Study name' })).toHaveValue('raven-search');
  expect(screen.getByRole('spinbutton', { name: 'Study random seed' })).toHaveValue(3);
  expect(screen.getByRole('textbox', { name: 'Parameter search space (JSON)' })).toHaveValue(
    JSON.stringify(search, null, 2),
  );
  expect(screen.getByRole('spinbutton', { name: 'Batch size' })).toHaveValue(12);
  expect(screen.getByText(/output_dir/)).toBeInTheDocument();
});

it('reports malformed and invalid imports and leaves the form untouched', async () => {
  const error = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(path === '/api/training/jobs' ? [] : path === '/api/training/workers' ? [] : {}),
    ),
  );
  render(<Training rulesets={rules} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  await screen.findByText('Your next opponent starts here.');
  const name = screen.getByRole('textbox', { name: 'Model name' });
  fireEvent.change(name, { target: { value: 'Manual name' } });
  fireEvent.change(input(), {
    target: { files: [new File(['{bad'], 'config.json', { type: 'application/json' })] },
  });
  await waitFor(() => expect(error).toHaveBeenCalledWith('The configuration is not valid JSON.'));
  expect(name).toHaveValue('Manual name');
  fireEvent.change(input(), { target: { files: [file({ channels: 9999 })] } });
  await waitFor(() =>
    expect(error).toHaveBeenCalledWith('Invalid configuration values: Network channels.'),
  );
  expect(name).toHaveValue('Manual name');
});

it('keeps an unavailable imported target and lets the user adjust it before starting', async () => {
  const error = vi.fn();
  const created = { id: 'new-job', status: 'running', config: {} };
  let posts = 0;
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/training/device')
      return json({ cuda_available: false, mps_available: false });
    if (path === '/api/training/workers') return json([]);
    if (path === '/api/training/jobs') {
      if (init?.method === 'POST') {
        posts += 1;
        return posts === 1
          ? json({ detail: 'CUDA is not available on this server.' }, false)
          : json(created);
      }
      return json([]);
    }
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(input(), {
    target: { files: [file({ ...trainingConfig('brandubh', 'smoke'), device: 'cuda' })] },
  });
  expect(await screen.findByText(/Configuration imported/)).toBeInTheDocument();
  const device = screen.getByRole('combobox', { name: 'Training device' });
  expect(device).toHaveValue('cuda');
  expect(screen.getByRole('option', { name: 'NVIDIA CUDA (unavailable)' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(error).toHaveBeenCalledWith('CUDA is not available on this server.'));
  fireEvent.change(device, { target: { value: 'cpu' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(posts).toBe(2));
  const bodies = fetcher.mock.calls
    .filter(([path, init]) => path === '/api/training/jobs' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)));
  expect(bodies[0]).toMatchObject({ device: 'cuda' });
  expect(bodies[1]).toMatchObject({ device: 'cpu' });
});

it('edits an imported value and submits the resulting payload', async () => {
  const created = { id: 'new-job', status: 'running', config: {} };
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/training/device') return json({});
    if (path === '/api/training/workers') return json([]);
    if (path === '/api/training/jobs') return json(init?.method === 'POST' ? created : []);
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(input(), {
    target: {
      files: [
        file({
          ...trainingConfig('brandubh', 'smoke'),
          model_name: 'Imported raven',
          iterations: 9,
          batch_size: 17,
        }),
      ],
    },
  });
  await screen.findByText(/Configuration imported/);
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Batch size' }), {
    target: { value: '23' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(
        ([path, init]) => path === '/api/training/jobs' && init?.method === 'POST',
      ),
    ).toBe(true),
  );
  const post = fetcher.mock.calls.find(
    ([path, init]) => path === '/api/training/jobs' && init?.method === 'POST',
  )!;
  expect(JSON.parse(String(post[1]?.body))).toMatchObject({
    kind: 'train',
    model_name: 'Imported raven',
    iterations: 9,
    batch_size: 23,
  });
});

const sourceJob: Job = {
  id: 'source-job',
  kind: 'train',
  status: 'completed',
  checkpoint: 'source-job:last.pt',
  config: { model_name: 'Freydis', iterations: 10 },
  progress: { iteration: 5 },
};
const resumeInfo: ResumeCheckpoint = {
  job_id: sourceJob.id,
  checkpoint: sourceJob.checkpoint,
  completed_iteration: 5,
  ruleset: 'brandubh',
  channels: 32,
  blocks: 2,
  optimizer: 'adamw',
  seed: 918,
  learning_rate: 0.0007,
  weight_decay: 0.002,
  momentum: 0.9,
  model_name: 'Freydis',
  can_resume: true,
  config: {
    ...trainingConfig('brandubh', 'smoke'),
    channels: 32,
    blocks: 2,
    seed: 918,
    iterations: 10,
    batch_size: 8,
    model_name: 'Freydis',
    learning_rate: 0.0007,
    weight_decay: 0.002,
    momentum: 0.9,
  },
};

it('applies resume overrides while keeping checkpoint identity and source intact', async () => {
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? { ...sourceJob, id: 'resumed' } : resumeInfo),
  );
  vi.stubGlobal('fetch', fetcher);
  const onStarted = vi.fn();
  render(<TrainingResumeDialog job={sourceJob} onClose={vi.fn()} onStarted={onStarted} />);
  await screen.findByLabelText('Target total iterations');
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  fireEvent.change(input(), {
    target: {
      files: [
        file({
          ...resumeInfo.config,
          channels: 12,
          blocks: 3,
          seed: 5,
          optimizer: 'sgd',
          batch_size: 17,
          iterations: 12,
        }),
      ],
    },
  });
  expect(await screen.findByText(/Configuration imported/)).toBeInTheDocument();
  expect(
    screen.getByText(
      /Checkpoint settings kept: Network channels, Residual blocks, Optimizer, Random seed/,
    ),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Batch size')).toHaveValue(17);
  expect(screen.getByLabelText('Target total iterations')).toHaveValue(12);
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  const post = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(post[0]).toBe('/api/training/jobs/source-job/resume');
  expect(JSON.parse(String(post[1]?.body))).toEqual({ iterations: 12, batch_size: 17 });
});

it('keeps resume form state when an imported config is malformed or invalid', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(resumeInfo)),
  );
  render(<TrainingResumeDialog job={sourceJob} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByLabelText('Batch size');
  fireEvent.change(screen.getByLabelText('Batch size'), { target: { value: '21' } });
  fireEvent.change(input(), {
    target: { files: [new File(['nope'], 'config.json', { type: 'application/json' })] },
  });
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'The configuration is not valid JSON.',
  );
  expect(screen.getByLabelText('Batch size')).toHaveValue(21);
  fireEvent.change(input(), { target: { files: [file({ ruleset: 'atlantis' })] } });
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Invalid configuration values: Battlefield.',
  );
  expect(screen.getByLabelText('Batch size')).toHaveValue(21);
});

it('uses nested training from an optimization config and ignores its study fields on resume', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(resumeInfo)),
  );
  render(<TrainingResumeDialog job={sourceJob} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByLabelText('Batch size');
  fireEvent.change(input(), {
    target: {
      files: [
        file({
          trials: 9,
          study_name: 'other-study',
          seed: 123,
          search_space: { epochs: { type: 'int', low: 1, high: 2 } },
          training: { ...resumeInfo.config, batch_size: 14 },
        }),
      ],
    },
  });
  expect(await screen.findByText(/Configuration imported/)).toBeInTheDocument();
  expect(screen.getByLabelText('Batch size')).toHaveValue(14);
  expect(screen.getByText(/ignored: trials, study_name, seed, search_space/)).toBeInTheDocument();
});
