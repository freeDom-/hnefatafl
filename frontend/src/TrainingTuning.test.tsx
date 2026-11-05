import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TrainingTuningView } from './TrainingAnalysisViews';
import { setLanguage } from './i18n';
import type { Job } from './types';

beforeEach(() => setLanguage('en', false));
afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

const tuningJob = (overrides: Partial<Job> = {}): Job => ({
  id: 'tuningjob12345678',
  kind: 'tune',
  status: 'completed',
  config: { study_name: 'brandubh-search', trials: 3 },
  result: {
    study_name: 'brandubh-search',
    completed_trials: 3,
    failed_trials: 1,
    best_trial: 2,
    best_score: 0.75,
    best_iteration: 4,
    sampler_seed: 7,
    source_seed: 11,
    best_opponents: {
      shield_bearer: { score: 1, wins: 2, losses: 0, draws: 0, truncated: 0 },
      jarl: { score: 0.5, wins: 1, losses: 1, draws: 0, truncated: 0 },
    },
    verification_result: {
      opponent: 'jarl',
      score: 0.625,
      games: 8,
      seed: 1234,
      wins: 5,
      losses: 3,
      draws: 0,
      truncated: 0,
    },
    best_configuration: {
      learning_rate: 0.002,
      channels: 16,
      iterations: 5,
    },
    trials: [
      {
        number: 0,
        state: 'COMPLETE',
        score: 0.5,
        best_score: 0.6,
        final_score: 0.5,
        best_iteration: 3,
        parameters: { learning_rate: 0.001 },
      },
      {
        number: 1,
        state: 'FAIL',
        score: null,
        error: 'training failed',
        parameters: { learning_rate: 0.003 },
      },
      {
        number: 2,
        state: 'COMPLETE',
        score: 0.75,
        best_score: 0.75,
        final_score: 0.7,
        best_iteration: 4,
        parameters: { learning_rate: 0.002 },
      },
    ],
  },
  ...overrides,
});

it('shows the best-checkpoint objective, verification and weighted aggregate', () => {
  render(
    <TrainingTuningView
      job={tuningJob()}
      progress={(tuningJob().result ?? {}) as never}
      trainingJobs={[]}
      busy={false}
      onUseBestParameters={vi.fn()}
      onContinueTrial={vi.fn()}
      onApplyToJob={vi.fn()}
      onDownloadBestConfig={vi.fn()}
    />,
  );
  expect(screen.getByText('Best trial score')).toBeInTheDocument();
  expect(screen.getByText('Held-out verification')).toBeInTheDocument();
  expect(screen.getByText('Weighted opponent aggregate')).toBeInTheDocument();
  // The trial table keeps best and final scores distinct.
  const disclosure = screen.getByText('Trial history').closest('details')!;
  disclosure.open = true;
  fireEvent(disclosure, new Event('toggle'));
  expect(screen.getByRole('table', { name: 'Trial history' })).toBeInTheDocument();
  // The column header and the sort option both carry this label.
  expect(screen.getAllByText('Best in-training benchmark score').length).toBeGreaterThan(0);
});

it('invokes the apply actions with the configured additional iterations', () => {
  const onUseBestParameters = vi.fn();
  const onContinueTrial = vi.fn();
  const onDownloadBestConfig = vi.fn();
  render(
    <TrainingTuningView
      job={tuningJob()}
      progress={(tuningJob().result ?? {}) as never}
      trainingJobs={[]}
      busy={false}
      onUseBestParameters={onUseBestParameters}
      onContinueTrial={onContinueTrial}
      onApplyToJob={vi.fn()}
      onDownloadBestConfig={onDownloadBestConfig}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Train with best parameters' }));
  expect(onUseBestParameters).toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Additional iterations'), { target: { value: '9' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue winning trial' }));
  expect(onContinueTrial).toHaveBeenCalledWith(9);
  fireEvent.click(screen.getByRole('button', { name: 'Download best configuration' }));
  expect(onDownloadBestConfig).toHaveBeenCalled();
});

it('applies the winning settings to a selected resumable training job', () => {
  const onApplyToJob = vi.fn();
  const target: Job = {
    id: 'trainjob12345678',
    kind: 'train',
    status: 'completed',
    checkpoint: 'last.pt',
    resumable: true,
    config: {},
  };
  render(
    <TrainingTuningView
      job={tuningJob()}
      progress={(tuningJob().result ?? {}) as never}
      trainingJobs={[target]}
      busy={false}
      onUseBestParameters={vi.fn()}
      onContinueTrial={vi.fn()}
      onApplyToJob={onApplyToJob}
      onDownloadBestConfig={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText('Apply to existing training'), {
    target: { value: target.id },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Apply best parameters' }));
  expect(onApplyToJob).toHaveBeenCalledWith(target.id, 5);
});

it('ranks weighted objectives, bounds thousands of rows and locates the persisted winner', () => {
  const trials = Array.from({ length: 1005 }, (_, number) => ({
    number,
    state: 'COMPLETE',
    score: number === 3 ? 0.99 : number / 2000,
    best_score: 1 - number / 2000,
    final_score: 0.2,
    aggregate_score: number === 3 ? 0.99 : number / 2000,
    duration: number + 1,
    parameters: { learning_rate: (number + 1) / 100000 },
  }));
  const job = tuningJob({ result: { ...tuningJob().result, trials, best_trial: 3 } });
  render(
    <TrainingTuningView
      job={job}
      progress={job.result!}
      trainingJobs={[]}
      busy={false}
      onUseBestParameters={vi.fn()}
      onContinueTrial={vi.fn()}
      onApplyToJob={vi.fn()}
      onDownloadBestConfig={vi.fn()}
    />,
  );
  const disclosure = screen.getByText('Trial history').closest('details')!;
  disclosure.open = true;
  fireEvent(disclosure, new Event('toggle'));
  const table = screen.getByRole('table', { name: 'Trial history' });
  expect(table.querySelectorAll('tbody tr')).toHaveLength(50);
  expect(screen.getByRole('columnheader', { name: 'Optuna objective score' })).toBeInTheDocument();
  expect(
    screen.getByRole('columnheader', { name: 'Best in-training benchmark score' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('columnheader', { name: 'Weighted aggregate score' }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(screen.getByText(/Page 2/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Locate winning trial' }));
  const first = table.querySelector('tbody tr')!;
  expect(first).toHaveClass('winning-trial');
  expect(first.querySelector('th')).toHaveTextContent('3');
  expect(first.querySelectorAll('td')[1]).toHaveTextContent('0.99');
  const charts = screen.getByText('Tuning charts').closest('details')!;
  charts.open = true;
  fireEvent(charts, new Event('toggle'));
  expect(screen.getByRole('img', { name: 'Trial objective scores' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Best objective so far' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Trial runtime comparisons' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Parameter versus objective' })).toBeInTheDocument();
  expect(screen.getByText(/Importance requires at least 20/)).toBeInTheDocument();
  expect(document.querySelectorAll('svg circle').length).toBeLessThan(2000);
});
