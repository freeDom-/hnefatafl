import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import Training from './Training';
import type { Job, Model } from './types';
import { setLanguage } from './i18n';

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
  const button = await screen.findByRole('button', { name: 'Resume training for Freydis' });
  expect(screen.queryByRole('button', { name: 'Resume training for Imported learner' })).toBeNull();
  fireEvent.click(button);
  expect(await screen.findByRole('dialog', { name: 'Resume training' })).toBeInTheDocument();
  expect(await screen.findByRole('spinbutton', { name: 'Target total iterations' })).toHaveValue(
    10,
  );
});
