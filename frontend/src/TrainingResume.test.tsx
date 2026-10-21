import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import Training from './Training';
import type { Job, Model } from './types';

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
  model_name: 'Freydis',
  can_resume: true,
};
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
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
  fireEvent.change(target, { target: { value: '5' } });
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
  fireEvent.change(target, { target: { value: '12' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalledWith(next, 5, 12));
  const sent = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(sent[0]).toBe('/api/training/jobs/source-job/resume');
  expect(JSON.parse(String(sent[1]?.body))).toEqual({ iterations: 12 });
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
  const button = await screen.findByRole('button', { name: 'Resume training for Freydis' });
  expect(screen.queryByRole('button', { name: 'Resume training for Imported learner' })).toBeNull();
  fireEvent.click(button);
  expect(await screen.findByRole('dialog', { name: 'Resume training' })).toBeInTheDocument();
  expect(await screen.findByRole('spinbutton', { name: 'Target total iterations' })).toHaveValue(
    10,
  );
});
