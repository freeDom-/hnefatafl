import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingResumeDialog from './TrainingResumeDialog';
import TrainingHistoryChart from './TrainingHistoryChart';
import TrainingEvaluationStatus from './TrainingEvaluationStatus';
import { trainingConfig } from './trainingPresets';
import { setLanguage } from './i18n';
import type { Job, Ruleset } from './types';

const rules: Ruleset[] = [
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
  setLanguage('en', false);
});

it('starts with exactly one selected opponent and effective Jarl controls', async () => {
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? { id: 'new', status: 'running' } : []),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Evaluation opponent'), { target: { value: 'jarl' } });
  fireEvent.change(screen.getByLabelText('Jarl evaluation depth'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Jarl search time limit (seconds)'), {
    target: { value: '.2' },
  });
  fireEvent.change(screen.getByLabelText('Evaluate every (iterations)'), {
    target: { value: '5' },
  });
  expect(screen.queryByLabelText('Champion confidence level')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  );
  expect(
    JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]?.body)),
  ).toMatchObject({
    evaluation_opponent: 'jarl',
    evaluation_jarl_depth: 3,
    evaluation_jarl_time_limit: 0.2,
    evaluation_frequency: 5,
  });
});

it('resumes iteration 100 with Champion settings and keeps saved identity and learning configuration', async () => {
  const config = {
    ...trainingConfig('brandubh', 'smoke'),
    iterations: 100,
    channels: 12,
    blocks: 3,
    seed: 918,
    optimizer: 'sgd',
    games: 7,
  };
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(
      init?.method === 'POST'
        ? { ...job, id: 'continued' }
        : {
            job_id: job.id,
            completed_iteration: 100,
            can_resume: true,
            model_name: 'Eir',
            ruleset: 'brandubh',
            channels: 12,
            blocks: 3,
            optimizer: 'sgd',
            seed: 918,
            config,
          },
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  const opponent = await screen.findByLabelText('Evaluation opponent');
  expect(screen.queryByLabelText('Training preset')).not.toBeInTheDocument();
  fireEvent.change(opponent, { target: { value: 'champion' } });
  fireEvent.change(screen.getByLabelText('Evaluation execution'), { target: { value: 'batched' } });
  fireEvent.change(screen.getByLabelText('Evaluation inference backend'), {
    target: { value: 'training' },
  });
  expect(screen.queryByLabelText('Jarl evaluation depth')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), { target: { value: '48' } });
  fireEvent.change(screen.getByLabelText('Evaluate every (iterations)'), {
    target: { value: '10' },
  });
  fireEvent.change(screen.getByLabelText('Champion confidence level'), {
    target: { value: '.99' },
  });
  expect(screen.getByLabelText('Self-play games per iteration')).toHaveValue(7);
  expect(screen.getByText('12 channels · 3 blocks')).toBeInTheDocument();
  expect(screen.getByText('918')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
    iterations: 101,
    evaluation_opponent: 'champion',
    evaluation_mode: 'batched',
    evaluation_inference_backend: 'training',
    evaluation_games: 48,
    evaluation_frequency: 10,
    champion_confidence: 0.99,
  });
});

it('localizes conditional evaluation and promotion settings in German', async () => {
  setLanguage('de', false);
  const config = trainingConfig('brandubh', 'smoke');
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      json({
        job_id: 'de',
        completed_iteration: 1,
        can_resume: true,
        ruleset: 'brandubh',
        channels: 4,
        blocks: 0,
        optimizer: 'adamw',
        model_name: 'Eir',
        config,
      }),
    ),
  );
  render(
    <TrainingResumeDialog
      job={{ id: 'de', status: 'completed', config }}
      onClose={vi.fn()}
      onStarted={vi.fn()}
    />,
  );
  fireEvent.change(await screen.findByLabelText('Auswertungsgegner'), {
    target: { value: 'champion' },
  });
  expect(screen.getByLabelText('Konfidenzniveau für den Champion')).toHaveValue(0.95);
  expect(screen.getByLabelText('Mindestanzahl vollständiger Auswertungspaare')).toHaveValue(12);
  expect(screen.getByRole('status')).toHaveTextContent('Dieses Partienbudget');
});

it('keeps benchmark curves separate, shows side bias and promotion events, and leaves gaps missing', async () => {
  const point = (iteration: number, opponent: string, identity: string, promoted?: boolean) => ({
    iteration,
    policy_loss: 1,
    value_loss: 0.2,
    evaluation_score: 0.5,
    evaluation_truncated: 0,
    evaluation_draws: 0,
    opponent_identity: identity,
    evaluation: {
      opponent,
      score: 0.5,
      candidate_as_attacker: { score: 0 },
      candidate_as_defender: { score: 1 },
      duration: 0.2,
      promoted,
    },
  });
  render(
    <TrainingHistoryChart
      history={[
        point(1, 'shield_bearer', 'shield_bearer:one'),
        { iteration: 2, policy_loss: 0.9, value_loss: 0.1 },
        point(3, 'jarl', 'jarl:two'),
        point(4, 'champion', 'champion:three:incumbent-1', true),
      ]}
    />,
  );
  expect(screen.getAllByRole('img')).toHaveLength(4);
  const evaluationCharts = [...document.querySelectorAll('figure')].slice(1);
  for (const chart of evaluationCharts) {
    for (const line of chart.querySelectorAll('polyline'))
      expect(line.getAttribute('points')!.split(' ')).toHaveLength(1);
  }
  fireEvent.click(screen.getByText('View graph data'));
  expect(await screen.findByText('Promoted')).toBeInTheDocument();
  expect(screen.getAllByText('Attacker-side score').length).toBeGreaterThan(1);
  expect(
    screen.getByText(
      'Evaluation series use separate opponent configurations and champion incumbents. Missing observations are not zero scores.',
    ),
  ).toBeVisible();
});

it('shows accepted champion, latest update and benchmark saturation independently', () => {
  render(
    <TrainingEvaluationStatus
      progress={{
        iteration: 110,
        evaluation_selection: {
          opponent: 'champion',
          champion_iteration: 80,
          champion_provisional: true,
          last_evaluation: {
            iteration: 100,
            opponent: 'shield_bearer',
            score: 1,
            benchmark_saturated: true,
          },
        },
      }}
    />,
  );
  expect(screen.getByText('110')).toBeInTheDocument();
  expect(screen.getByText(/80/)).toHaveTextContent('Provisional incumbent');
  expect(screen.getByRole('status')).toHaveTextContent('near saturation');
});

it('selects the localized Shield maiden evaluation benchmark', async () => {
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? { id: 'maiden', status: 'running' } : []),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Evaluation opponent'), {
    target: { value: 'shield_maiden' },
  });
  expect(
    screen.getByText(
      'Shield maiden uses a stronger tactical heuristic, designed for low evaluation overhead.',
    ),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  );
  expect(
    JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]?.body))
      .evaluation_opponent,
  ).toBe('shield_maiden');
});
