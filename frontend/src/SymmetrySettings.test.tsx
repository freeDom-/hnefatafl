import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import SymmetrySettings, { symmetryTargetError } from './SymmetrySettings';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import { changeTrainingPlan, recordedTrainingConfig, trainingConfig } from './trainingPresets';
import { recordedJobParameters } from './trainingReuse';
import { setLanguage } from './i18n';
import type { Job } from './types';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});

it('defaults to disabled, explains the sample-only scope and reports the chosen mode', () => {
  const update = vi.fn();
  render(<SymmetrySettings config={trainingConfig('brandubh', 'smoke')} update={update} />);
  const select = screen.getByLabelText(/Training symmetry augmentation/);
  expect(select).toHaveValue('off');
  expect(screen.getByRole('option', { name: 'Disabled' })).toBeInTheDocument();
  expect(
    screen.getByText(/Randomly rotate or reflect training positions and their corresponding move/),
  ).toBeInTheDocument();
  expect(screen.getByText(/not the game rules or MCTS search budget/)).toBeInTheDocument();
  fireEvent.change(select, { target: { value: 'random_d4' } });
  expect(update).toHaveBeenCalledWith('training_symmetry_mode', 'random_d4');
});

it('is localized in German', () => {
  setLanguage('de', false);
  render(<SymmetrySettings config={trainingConfig('brandubh', 'smoke')} update={vi.fn()} />);
  expect(screen.getByLabelText(/Symmetrie-Datenaugmentation/)).toBeInTheDocument();
  expect(screen.getByText(/zufällig gedreht oder gespiegelt/)).toBeInTheDocument();
});

it('flags only workers that cannot run augmentation', () => {
  const on = { ...trainingConfig('brandubh', 'smoke'), training_symmetry_mode: 'random_d4' };
  expect(symmetryTargetError(on, { training_symmetry_modes: ['off', 'random_d4'] })).toBe('');
  expect(symmetryTargetError(on, {})).not.toBe('');
  expect(symmetryTargetError(on)).toBe('');
  expect(symmetryTargetError(trainingConfig('brandubh', 'smoke'), {})).toBe('');
  render(<SymmetrySettings config={on} update={vi.fn()} capabilities={{}} />);
  expect(screen.getByRole('alert')).toHaveTextContent(/does not support/);
});

it('is retained across plans and reused from recorded models and jobs; legacy values read off', () => {
  const selected = { ...trainingConfig('brandubh', 'smoke'), training_symmetry_mode: 'random_d4' };
  expect(changeTrainingPlan(selected, 'copenhagen', 'recommended').training_symmetry_mode).toBe(
    'random_d4',
  );
  expect(
    recordedTrainingConfig(selected, {
      name: 'x',
      ruleset: 'tablut',
      training_config: { channels: 48, training_symmetry_mode: 'random_d4' },
    })?.training_symmetry_mode,
  ).toBe('random_d4');
  // A legacy record has no setting: reuse reads Disabled instead of inheriting the form's value.
  expect(
    recordedTrainingConfig(selected, {
      name: 'old',
      ruleset: 'tablut',
      training_config: { channels: 48 },
    })?.training_symmetry_mode,
  ).toBe('off');
  const invalid = recordedTrainingConfig(selected, {
    name: 'bad',
    ruleset: 'tablut',
    training_config: { channels: 48, training_symmetry_mode: 'all_d4' },
  });
  expect(invalid?.training_symmetry_mode).toBe('off');
  const job: Job = {
    id: 'j',
    kind: 'train',
    status: 'completed',
    config: { ruleset: 'brandubh', channels: 8, training_symmetry_mode: 'random_d4' },
    progress: {},
  };
  expect(recordedJobParameters(trainingConfig('tablut', 'smoke'), job)?.config).toMatchObject({
    training_symmetry_mode: 'random_d4',
  });
});

const job: Job = {
  id: 'source-job',
  kind: 'train',
  status: 'completed',
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
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});

it('resume shows legacy checkpoints as disabled and sends only a deliberate change', async () => {
  const next = { ...job, id: 'new', status: 'running' };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? next : info),
  );
  vi.stubGlobal('fetch', fetcher);
  const onStarted = vi.fn();
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={onStarted} />);
  const select = await screen.findByLabelText(/Training symmetry augmentation/);
  expect(select).toHaveValue('off');
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ iterations: 10 });
  fetcher.mockClear();
  fireEvent.change(select, { target: { value: 'random_d4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
    iterations: 10,
    training_symmetry_mode: 'random_d4',
  });
});

it('resume restores a saved Random D4 setting from the checkpoint configuration', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json({ ...info, config: { training_symmetry_mode: 'random_d4' } })),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByLabelText(/Training symmetry augmentation/)).toHaveValue('random_d4');
});
