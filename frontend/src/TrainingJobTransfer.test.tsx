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
    return json(path.split('?')[0] === '/api/training/jobs' ? (imported ? [job] : []) : []);
  });
  vi.stubGlobal('fetch', fetcher);
  const changed = vi.fn();
  render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={changed} />);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByLabelText('Import job'), {
    target: { files: [new File(['gzip'], 'job.tar.gz')] },
  });
  expect(screen.getByText('Importing job…')).toBeInTheDocument();
  expect(screen.getByLabelText('Import job')).toBeDisabled();
  imported = true;
  await act(async () => finish(await json(job)));
  expect(
    await screen.findByText('Job imported. Review its history or resume from its full checkpoint.'),
  ).toBeInTheDocument();
  expect(screen.getByText('Training progress')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Details' }));
  expect(screen.getByText('Training configuration')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Resume from checkpoint' })).toBeEnabled();
  expect(changed).toHaveBeenCalled();
  expect(
    fetcher.mock.calls.filter(([path]) => path.split('?')[0] === '/api/training/jobs').length,
  ).toBeGreaterThan(1);
});

it.each(['en', 'de'])(
  'loads imported history once from a compact response with a visible loading state (%s)',
  async (language) => {
    setLanguage(language as 'en' | 'de', false);
    const summary: Job = { ...job, summary: true };
    const full: Job = {
      ...job,
      progress: {
        iteration: 1000,
        history: Array.from({ length: 1000 }, (_, i) => ({ iteration: i + 1, policy_loss: 1 })),
      },
    };
    let finish!: (response: Response) => void;
    let imported = false;
    const fetcher = vi.fn((path: string) => {
      if (path === '/api/training/jobs/import') {
        imported = true;
        return json(summary);
      }
      if (path === '/api/training/jobs/imported-job')
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return json(path.split('?')[0] === '/api/training/jobs' && imported ? [summary] : []);
    });
    vi.stubGlobal('fetch', fetcher);
    const error = vi.fn();
    render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
    fireEvent.change(
      screen.getByLabelText(language === 'de' ? 'Auftrag importieren' : 'Import job'),
      {
        target: { files: [new File(['gzip'], 'job.tar.gz')] },
      },
    );
    const loading = language === 'de' ? 'Verlauf wird geladen…' : 'Loading history…';
    expect(await screen.findByText(loading)).toHaveAttribute('role', 'status');
    expect(document.querySelector('.job-row.selected')).toHaveTextContent('Migrated raven');
    expect(
      fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs/imported-job'),
    ).toHaveLength(1);
    await act(async () => finish(await json(full)));
    await waitFor(() => expect(screen.queryByText(loading)).not.toBeInTheDocument());
    expect(document.querySelector('.dashboard-progress strong')).toHaveTextContent('1000');
    fireEvent.click(
      screen.getByRole('button', {
        name: language === 'de' ? 'Auftrag aktualisieren' : 'Refresh job',
      }),
    );
    await waitFor(() =>
      expect(
        fetcher.mock.calls.filter(([path]) => path.startsWith('/api/training/jobs?')).length,
      ).toBeGreaterThan(2),
    );
    expect(
      fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs/imported-job'),
    ).toHaveLength(1);
    expect(error).not.toHaveBeenCalled();
  },
);

it('retains loaded imported detail when the navigator refresh completes afterward', async () => {
  let imported = false;
  let finishList!: (response: Response) => void;
  const summary = { ...job, summary: true };
  const fetcher = vi.fn((path: string) => {
    if (path === '/api/training/jobs/import') {
      imported = true;
      return json(summary);
    }
    if (path === '/api/training/jobs/imported-job') return json(job);
    if (path.startsWith('/api/training/jobs?') && imported)
      return new Promise<Response>((resolve) => {
        finishList = resolve;
      });
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByLabelText('Import job'), {
    target: { files: [new File(['gzip'], 'job.tar.gz')] },
  });
  await waitFor(() =>
    expect(
      fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs/imported-job'),
    ).toHaveLength(1),
  );
  await waitFor(() => expect(screen.queryByText('Loading history…')).not.toBeInTheDocument());
  await act(async () => finishList(await json([summary])));
  expect(screen.getByLabelText('Import job')).toBeEnabled();
  expect(screen.queryByText('Loading history…')).not.toBeInTheDocument();
  expect(
    fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs/imported-job'),
  ).toHaveLength(1);
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
    vi.fn((path: string) => json(path.split('?')[0] === '/api/training/jobs' ? [saved] : [])),
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
      json(path.split('?')[0] === '/api/training/jobs' ? [{ ...job, resumable: false }] : []),
    ),
  );
  render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  expect(screen.getByLabelText('Auftrag importieren')).toHaveAttribute(
    'accept',
    '.tar.gz,application/gzip,.zip,application/zip',
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
          : path.split('?')[0] === '/api/training/jobs'
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

it('downloads a TAR.GZ job and clears the loading state', async () => {
  const error = vi.fn();
  const createURL = vi.fn(() => 'blob:job-export');
  vi.stubGlobal('URL', { createObjectURL: createURL, revokeObjectURL: vi.fn() });
  let downloaded = '';
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    downloaded = this.download;
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      path.endsWith('/export')
        ? Promise.resolve({ ok: true, blob: async () => new Blob(['gzip']) } as Response)
        : json(path.split('?')[0] === '/api/training/jobs' ? [job] : []),
    ),
  );
  render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: new RegExp('Migrated raven') }));
  fireEvent.click(screen.getByRole('button', { name: 'Export job' }));
  await waitFor(() => expect(downloaded).toBe('hnefatafl-job-imported-job.tar.gz'));
  expect(createURL).toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Export job' })).toBeEnabled();
  expect(error).not.toHaveBeenCalled();
});

it('shows a busy archive retry error and restores the import control', async () => {
  const error = vi.fn();
  const detail = 'Another job archive transfer is in progress. Retry when it finishes.';
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(path.endsWith('/import') ? { detail } : [], !path.endsWith('/import')),
    ),
  );
  render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Import job'), {
    target: { files: [new File(['gzip'], 'job.tar.gz')] },
  });
  await waitFor(() => expect(error).toHaveBeenCalledWith(detail));
  expect(screen.getByLabelText('Import job')).toBeEnabled();
  expect(
    screen.queryByText('Job imported. Review its history or resume from its full checkpoint.'),
  ).toBeNull();
});
