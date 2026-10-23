import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Training from './Training';
import { setLanguage } from './i18n';
import type { Job } from './types';

const job: Job = {
  id: 'imported-job',
  kind: 'train',
  status: 'completed',
  resumable: true,
  checkpoint: 'imported-job:last.pt',
  config: { model_name: 'Migrated raven', preset: 'smoke', games: 7 },
  progress: { iteration: 1 },
};
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setLanguage('en', false);
});

it('uploads a job with a loading state, refreshes models and selects its history', async () => {
  let finish!: (response: Response) => void;
  let imported = false;
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/training/jobs/import') {
      expect(init?.body).toBeInstanceOf(FormData);
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }
    return json(path === '/api/training/jobs' ? (imported ? [job] : []) : []);
  });
  vi.stubGlobal('fetch', fetcher);
  const changed = vi.fn();
  render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={changed} />);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByLabelText('Import job'), {
    target: { files: [new File(['zip'], 'job.zip')] },
  });
  expect(screen.getByText('Importing job…')).toBeInTheDocument();
  expect(screen.getByLabelText('Import job')).toBeDisabled();
  imported = true;
  await act(async () => finish(await json(job)));
  expect(
    await screen.findByText('Job imported. Review its history or resume from its full checkpoint.'),
  ).toBeInTheDocument();
  expect(screen.getByText('Training progress')).toBeInTheDocument();
  expect(screen.getByText('Training configuration')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Resume from checkpoint' })).toBeEnabled();
  expect(changed).toHaveBeenCalled();
  expect(
    fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs').length,
  ).toBeGreaterThan(1);
});

it('shows archive validation errors without reporting success', async () => {
  const error = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(
        path.endsWith('/import') ? { detail: 'Job archive checksum mismatch: last.pt.' } : [],
        !path.endsWith('/import'),
      ),
    ),
  );
  render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Import job'), {
    target: { files: [new File(['bad'], 'bad.zip')] },
  });
  await waitFor(() =>
    expect(error).toHaveBeenCalledWith('Job archive checksum mismatch: last.pt.'),
  );
  expect(
    screen.queryByText('Job imported. Review its history or resume from its full checkpoint.'),
  ).toBeNull();
  expect(screen.getByLabelText('Import job')).toBeEnabled();
});

it.each(['completed', 'running'])('only offers export for an inactive job (%s)', async (status) => {
  const saved = { ...job, status };
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => json(path === '/api/training/jobs' ? [saved] : [])),
  );
  render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: new RegExp('Migrated raven') }));
  if (status === 'completed')
    expect(screen.getByRole('button', { name: 'Export job' })).toBeEnabled();
  else expect(screen.queryByRole('button', { name: 'Export job' })).toBeNull();
});

it('localizes transfer actions and disables resume for an inference-only imported job', async () => {
  setLanguage('de', false);
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(path === '/api/training/jobs' ? [{ ...job, resumable: false }] : []),
    ),
  );
  render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  expect(screen.getByLabelText('Auftrag importieren')).toHaveAttribute(
    'accept',
    '.zip,application/zip',
  );
  fireEvent.click(await screen.findByRole('button', { name: new RegExp('Migrated raven') }));
  expect(screen.getByRole('button', { name: 'Auftrag exportieren' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Kontrollpunkt/ })).toBeDisabled();
});

it('reports export validation errors', async () => {
  const error = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(
        path.endsWith('/export')
          ? { detail: 'Stop this training job before exporting its data.' }
          : path === '/api/training/jobs'
            ? [job]
            : [],
        !path.endsWith('/export'),
      ),
    ),
  );
  render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: new RegExp('Migrated raven') }));
  fireEvent.click(screen.getByRole('button', { name: 'Export job' }));
  await waitFor(() =>
    expect(error).toHaveBeenCalledWith('Stop this training job before exporting its data.'),
  );
});
