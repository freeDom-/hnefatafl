import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingTuneDialog from './TrainingTuneDialog';
import { setLanguage } from './i18n';
import type { Job } from './types';

const job: Job = {
  id: 'trainjob12345678',
  kind: 'train',
  status: 'completed',
  checkpoint: 'last.pt',
  resumable: true,
  config: {
    preset: 'smoke',
    ruleset: 'brandubh',
    model_name: 'Raven',
    seed: 5,
    channels: 4,
    blocks: 0,
    optimizer: 'adamw',
    iterations: 10,
  },
};

const resumeInfo = {
  job_id: job.id,
  completed_iteration: 10,
  ruleset: 'brandubh',
  channels: 4,
  blocks: 0,
  optimizer: 'adamw',
  seed: 5,
  model_name: 'Raven',
  can_resume: true,
  config: { ...job.config },
};

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

it('reviews the source checkpoint and targets completed plus additional iterations', async () => {
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    if (String(path).endsWith('/resume')) return json(resumeInfo);
    if (init?.method === 'POST') return json({ id: 'new', status: 'running' });
    return json({});
  });
  vi.stubGlobal('fetch', fetcher);
  const onStarted = vi.fn();
  render(<TrainingTuneDialog job={job} onClose={vi.fn()} onStarted={onStarted} />);
  await waitFor(() =>
    expect(screen.getByLabelText('Additional tuning iterations per trial')).toBeVisible(),
  );
  expect(screen.getByText(/10 already completed/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Additional tuning iterations per trial'), {
    target: { value: '4' },
  });
  fireEvent.change(screen.getByLabelText('Trial budget'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start tuning' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  const post = fetcher.mock.calls.find(([, init]) => init?.method === 'POST');
  const body = JSON.parse(String(post![1]?.body));
  expect(body.kind).toBe('tune');
  expect(body.trials).toBe(2);
  // Every trial targets the checkpoint's completed iteration plus the extra budget.
  expect(body.training.iterations).toBe(14);
  // The source is a portable token, not a filesystem path.
  expect(body.source_checkpoint).toBe(`${job.id}:last.pt`);
  expect(body.training.resume).toBeUndefined();
  expect(body.training.ruleset).toBe('brandubh');
  expect(body.training.channels).toBe(4);
});

it('refuses to start when the checkpoint is not resumable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      json({ ...resumeInfo, can_resume: false, reason: 'The saved checkpoint was not found.' }),
    ),
  );
  render(<TrainingTuneDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await waitFor(() =>
    expect(screen.getByText('The saved checkpoint was not found.')).toBeVisible(),
  );
  expect(screen.getByRole('button', { name: 'Start tuning' })).toBeDisabled();
});

it('selects a compatible frozen neural opponent and submits its immutable registry ID', async () => {
  const fetcher = vi.fn((path: string, init?: RequestInit) =>
    json(path.endsWith('/resume') ? resumeInfo : init?.method === 'POST' ? { id: 'new' } : []),
  );
  vi.stubGlobal('fetch', fetcher);
  render(
    <TrainingTuneDialog
      job={job}
      models={[
        {
          id: 'a'.repeat(24),
          name: 'Larger fixed raven',
          ruleset: 'brandubh',
          training_config: { channels: 16, blocks: 2 },
        },
        { id: 'b'.repeat(24), name: 'Incompatible Tablut model', ruleset: 'tablut' },
      ]}
      onClose={vi.fn()}
      onStarted={vi.fn()}
    />,
  );
  await screen.findByLabelText('Additional tuning iterations per trial');
  expect(screen.getByRole('option', { name: 'Champion' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Add fixed opponent' }));
  fireEvent.change(screen.getByLabelText('Fixed opponent 1'), {
    target: { value: 'a'.repeat(24) },
  });
  expect(screen.queryByRole('option', { name: /Incompatible Tablut/ })).toBeNull();
  fireEvent.change(screen.getByLabelText('Opponent weight 1'), { target: { value: '0.75' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start tuning' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  );
  const sent = JSON.parse(
    String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]?.body),
  );
  expect(sent.opponents).toEqual([
    { opponent: 'frozen_model', model_id: 'a'.repeat(24), weight: 0.75 },
  ]);
  expect(sent.source_checkpoint).toBe(`${job.id}:last.pt`);
});

it('requires a fixed benchmark when the source trained against Champion', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      json({ ...resumeInfo, config: { ...resumeInfo.config, evaluation_opponent: 'champion' } }),
    ),
  );
  render(<TrainingTuneDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByText(
    'Choose a fixed benchmark before starting tuning. Champion changes during training.',
  );
  expect(screen.getByRole('button', { name: 'Start tuning' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Evaluation opponent'), {
    target: { value: 'shield_bearer' },
  });
  expect(screen.getByRole('button', { name: 'Start tuning' })).toBeEnabled();
});
