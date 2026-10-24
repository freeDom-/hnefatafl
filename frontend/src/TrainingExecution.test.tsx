import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingResumeDialog from './TrainingResumeDialog';
import TrainingPerformance from './TrainingPerformance';
import {
  changeTrainingPlan,
  recordedTrainingConfig,
  trainingConfig,
  type TrainingConfig,
} from './trainingPresets';
import { setLanguage } from './i18n';
import type { Job, Ruleset } from './types';

const rulesets: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);
beforeEach(() => {
  setLanguage('en', false);
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('submits separate self-play execution and optimization batching settings when starting', async () => {
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? { id: 'new', status: 'queued' } : []),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rulesets} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  expect(screen.getByLabelText('Self-play execution')).toHaveValue('auto');
  expect(screen.getByLabelText('Concurrent self-play games')).toHaveValue(16);
  expect(screen.getByLabelText('Inference batch size')).toHaveValue(32);
  fireEvent.change(screen.getByLabelText('Self-play execution'), { target: { value: 'batched' } });
  fireEvent.change(screen.getByLabelText('Concurrent self-play games'), { target: { value: '6' } });
  fireEvent.change(screen.getByLabelText('Inference batch size'), { target: { value: '12' } });
  fireEvent.change(screen.getByLabelText('Batch size'), { target: { value: '4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  );
  const request = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({
    self_play_mode: 'batched',
    self_play_concurrency: 6,
    inference_batch_size: 12,
    batch_size: 4,
  });
});

it('resumes with saved execution settings and only reviewed changes while preserving checkpoint identity', async () => {
  const config: TrainingConfig = {
    ...trainingConfig('brandubh', 'smoke'),
    self_play_mode: 'sequential',
    self_play_concurrency: 5,
    inference_batch_size: 9,
    iterations: 4,
  };
  const job: Job = { id: 'saved', status: 'completed', kind: 'train', config };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(
      init?.method === 'POST'
        ? { ...job, id: 'next' }
        : {
            job_id: 'saved',
            completed_iteration: 2,
            can_resume: true,
            model_name: config.model_name,
            ruleset: 'brandubh',
            channels: 4,
            blocks: 0,
            seed: 42,
            optimizer: 'adamw',
            config,
          },
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByLabelText('Self-play execution')).toHaveValue('sequential');
  expect(screen.getByLabelText('Concurrent self-play games')).toHaveValue(5);
  expect(screen.getByLabelText('Inference batch size')).toHaveValue(9);
  fireEvent.change(screen.getByLabelText('Concurrent self-play games'), { target: { value: '0' } });
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Concurrent self-play games'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Self-play execution'), { target: { value: 'batched' } });
  fireEvent.change(screen.getByLabelText('Inference batch size'), { target: { value: '6' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
    iterations: 4,
    self_play_mode: 'batched',
    self_play_concurrency: 3,
    inference_batch_size: 6,
  });
});

it('retains runtime execution settings across plan changes and restores recorded settings', () => {
  const previous = {
    ...trainingConfig('brandubh', 'smoke'),
    self_play_mode: 'batched',
    self_play_concurrency: 3,
    inference_batch_size: 7,
  };
  expect(changeTrainingPlan(previous, 'tablut', 'balanced')).toMatchObject({
    self_play_mode: 'batched',
    self_play_concurrency: 3,
    inference_batch_size: 7,
  });
  expect(
    recordedTrainingConfig(trainingConfig('brandubh', 'smoke'), {
      name: 'Eir',
      ruleset: 'brandubh',
      training_config: previous,
    }),
  ).toMatchObject({ self_play_mode: 'batched', self_play_concurrency: 3, inference_batch_size: 7 });
  expect(
    recordedTrainingConfig(previous, {
      name: 'Legacy',
      ruleset: 'brandubh',
      training_config: { batch_size: 4 },
    }),
  ).toMatchObject({ self_play_mode: 'batched', self_play_concurrency: 3, inference_batch_size: 7 });
});

const performance = {
  self_play_mode: 'batched',
  games_per_hour: 360,
  positions_per_second: 12.5,
  simulations_per_second: 30,
  mcts_neural_requests: 100,
  mcts_node_prediction_reuses: 40,
  neural_evaluations_per_second: 25,
  average_game_length: 8,
  completed_games: 1,
  drawn_games: 1,
  truncated_games: 2,
  inference_batches: 4,
  inference_batch_size_average: 2.5,
  inference_batch_size_min: 1,
  inference_batch_size_max: 3,
  inference_batch_latency_seconds: 0.02,
  self_play_wall_seconds: 10,
  cpu_search_seconds: 3,
  inference_time_seconds: 2,
  optimization_seconds: 1,
  evaluation_seconds: 0.5,
  iteration_wall_seconds: 12,
};

it('displays persisted performance when inspecting a completed training job', async () => {
  const completed: Job = {
    id: 'finished',
    status: 'completed',
    config: { model_name: 'Batch Eir' },
    progress: {
      metrics: { performance },
      history: [{ iteration: 1, policy_loss: 0.8, value_loss: 0.2, performance }],
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(
        path === '/api/training/jobs'
          ? [completed]
          : path === '/api/training/jobs/finished'
            ? completed
            : path === '/api/training/device'
              ? { default_device: 'cpu' }
              : [],
      ),
    ),
  );
  render(<Training rulesets={rulesets} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: /Batch Eir/ }));
  expect(screen.getByRole('img', { name: 'Loss by iteration' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Performance' }));
  expect(await screen.findByLabelText('Training performance')).toBeInTheDocument();
  expect(screen.getByText('Throughput history')).toBeInTheDocument();
});

it('shows aggregate performance and persisted completed-iteration throughput history without changing loss graphs', async () => {
  const view = render(<TrainingPerformance progress={{}} />);
  expect(screen.queryByLabelText('Training performance')).not.toBeInTheDocument();
  view.rerender(
    <TrainingPerformance
      progress={{
        metrics: { performance },
        history: [
          { iteration: 1, performance },
          { iteration: 2, performance: { ...performance, games_per_hour: 720 } },
        ],
      }}
    />,
  );
  expect(screen.getByText('2.5 / 1 / 3')).toBeInTheDocument();
  expect(screen.getByText('CPU search activity (seconds)')).toBeInTheDocument();
  expect(screen.getByText('MCTS neural requests').nextElementSibling).toHaveTextContent('100');
  expect(screen.getByText('MCTS node predictions reused').nextElementSibling).toHaveTextContent(
    '40',
  );
  expect(screen.getAllByText('Iteration wall time (seconds)').length).toBeGreaterThan(0);
  expect(screen.getByText('Peak GPU allocated memory (MiB)').nextElementSibling).toHaveTextContent(
    '—',
  );
  fireEvent.click(screen.getByText('Throughput history'));
  const table = await screen.findByRole('table', { name: 'Throughput history' });
  expect(within(table).getAllByRole('row', { hidden: true })).toHaveLength(3);
  expect(within(table).getByText('720')).toBeInTheDocument();
  view.rerender(
    <TrainingPerformance
      progress={{
        performance: { ...performance, gpu_peak_allocated_bytes: 1024 ** 2 },
        history: [null, { iteration: 1, performance: null }],
      }}
    />,
  );
  expect(screen.getByText('Peak GPU allocated memory (MiB)')).toBeInTheDocument();
});
