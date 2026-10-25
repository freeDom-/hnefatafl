import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingWorkers from './TrainingWorkers';
import TrainingResumeDialog from './TrainingResumeDialog';
import { setLanguage } from './i18n';
import type { Job, TrainingWorker } from './types';

const worker: TrainingWorker = {
  id: 'cloud',
  name: 'Cloud raven',
  capacity: 2,
  status: 'online',
  last_seen: '2026-10-22T08:00:00Z',
  active_jobs: 0,
  capabilities: { cuda_available: true, cuda_devices: ['Cloud GPU'] },
  compatibility: {
    eligible: true,
    code: 'compatible',
    reason: 'Compatible',
    mismatches: [],
    protocol_compatible: true,
    override_enabled: false,
    override_active: false,
    worker_version: '0.26.0',
    server_version: '0.26.0',
    worker_git_revision: null,
    server_git_revision: null,
  },
};
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
const rulesets = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const first: Job = {
  id: 'one',
  status: 'running',
  execution: 'remote',
  worker_name: worker.name,
  config: { model_name: 'Cloud Ulf' },
  progress: { history: [{ iteration: 1, policy_loss: 1.5, value_loss: 0.5 }] },
};
const second: Job = {
  ...first,
  id: 'two',
  config: { model_name: 'Cloud Freydis' },
  progress: {
    history: [
      { iteration: 1, policy_loss: 2, value_loss: 1 },
      { iteration: 2, policy_loss: 1, value_loss: 0.4 },
    ],
  },
};

beforeEach(() => {
  setLanguage('en', false);
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
  vi.clearAllMocks();
  setLanguage('en', false);
});

it('shows a new worker credential once without putting its token in commands or browser storage', async () => {
  const token = 'private-worker-credential';
  const fetcher = vi.fn(() => json({ worker, token }));
  vi.stubGlobal('fetch', fetcher);
  const changed = vi.fn();
  const { container } = render(<TrainingWorkers workers={[]} onChanged={changed} />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect a worker' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Connect a remote worker' }));
  fireEvent.change(dialog.getByLabelText('Worker name'), { target: { value: 'Cloud raven' } });
  fireEvent.change(dialog.getByLabelText('Parallel jobs'), { target: { value: '2' } });
  fireEvent.click(dialog.getByRole('button', { name: 'Create worker' }));
  const credential = await dialog.findByLabelText('Worker token');
  expect(credential).toHaveValue(token);
  expect(credential).toHaveAttribute('type', 'password');
  expect(container.querySelector('.worker-command')).toHaveTextContent('--concurrency 2');
  expect(container.querySelector('.worker-command')?.textContent).toBe(
    `read -r -s HNEFATAFL_WORKER_TOKEN\nexport HNEFATAFL_WORKER_TOKEN\n.venv/bin/python -m hnefatafl.cli worker --server ${window.location.origin} --work-dir ./data/remote-worker --concurrency 2`,
  );
  expect(container.querySelector('.worker-command')).not.toHaveTextContent(token);
  expect(JSON.stringify(localStorage)).not.toContain(token);
  expect(changed).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledWith(
    '/api/training/workers',
    expect.objectContaining({ body: JSON.stringify({ name: 'Cloud raven', capacity: 2 }) }),
  );
  fireEvent.click(dialog.getByRole('button', { name: 'Close worker setup' }));
  expect(screen.queryByLabelText('Worker token')).not.toBeInTheDocument();
});

it('requires confirmation before revoking a worker and keeps credentials out of the list', async () => {
  const fetcher = vi.fn(() => json({ revoked: true }));
  vi.stubGlobal('fetch', fetcher);
  const changed = vi.fn();
  render(<TrainingWorkers workers={[worker]} onChanged={changed} />);
  fireEvent.click(screen.getByRole('button', { name: 'Revoke worker Cloud raven' }));
  let dialog = within(screen.getByRole('dialog', { name: 'Revoke worker Cloud raven?' }));
  fireEvent.click(dialog.getByRole('button', { name: 'Keep it' }));
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Revoke worker Cloud raven' }));
  dialog = within(screen.getByRole('dialog', { name: 'Revoke worker Cloud raven?' }));
  fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(changed).toHaveBeenCalledWith(worker.id);
  expect(screen.queryByRole('heading', { name: worker.name })).not.toBeInTheDocument();
  expect(screen.getByText(/No workers connected/)).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith(
    '/api/training/workers/cloud',
    expect.objectContaining({ method: 'DELETE' }),
  );
  expect(screen.queryByLabelText('Worker token')).not.toBeInTheDocument();
});

it('shows online and disconnected workers while hiding revoked records on mount and refresh', () => {
  const offline = {
    ...worker,
    id: 'offline',
    name: 'Disconnected raven',
    status: 'offline' as const,
  };
  const revoked = { ...worker, id: 'revoked', name: 'Revoked raven', status: 'revoked' as const };
  const workers = [worker, offline, revoked];
  const { rerender } = render(<TrainingWorkers workers={workers} onChanged={vi.fn()} />);
  expect(screen.getByRole('heading', { name: worker.name })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: offline.name })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: revoked.name })).not.toBeInTheDocument();
  rerender(<TrainingWorkers workers={[revoked]} onChanged={vi.fn()} />);
  expect(screen.getByText(/No workers connected/)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: revoked.name })).not.toBeInTheDocument();
});

it('keeps a worker visible when revocation fails', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json({ detail: 'Revocation failed' }, false)),
  );
  const changed = vi.fn();
  render(<TrainingWorkers workers={[worker]} onChanged={changed} />);
  fireEvent.click(screen.getByRole('button', { name: 'Revoke worker Cloud raven' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Revoke worker Cloud raven?' }));
  fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
  expect(await dialog.findByText('Revocation failed')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: worker.name })).toBeInTheDocument();
  expect(changed).not.toHaveBeenCalled();
});

it('removes a revoked worker from the workshop count and training targets before refresh finishes', async () => {
  let revoked = false;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        revoked = true;
        return json({ revoked: true });
      }
      if (revoked) return new Promise<Response>(() => {});
      return json(path.endsWith('/workers') ? [worker] : path.endsWith('/jobs') ? [] : {});
    }),
  );
  render(<Training rulesets={rulesets} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Remote workers (1)' }));
  fireEvent.change(screen.getByLabelText('Run training on'), { target: { value: 'worker:cloud' } });
  fireEvent.click(screen.getByRole('button', { name: 'Revoke worker Cloud raven' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Revoke worker Cloud raven?' }));
  fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
  expect(await screen.findByRole('button', { name: 'Remote workers (0)' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: worker.name })).not.toBeInTheDocument();
  expect(screen.queryByRole('option', { name: /Cloud raven/ })).not.toBeInTheDocument();
});

it.each(['http://127.0.0.1:8000', 'https://tafl.example.net:8443'])(
  'uses the reachable browser server origin %s in the single-slot connection command',
  async (origin) => {
    vi.stubGlobal('location', new URL(origin));
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json({ worker: { ...worker, capacity: 1 }, token: 'secret' })),
    );
    const { container } = render(<TrainingWorkers workers={[]} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect a worker' }));
    fireEvent.change(screen.getByLabelText('Worker name'), { target: { value: 'Raven' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create worker' }));
    await screen.findByLabelText('Worker token');
    expect(container.querySelector('.worker-command')?.textContent).toBe(
      `read -r -s HNEFATAFL_WORKER_TOKEN\nexport HNEFATAFL_WORKER_TOKEN\n.venv/bin/python -m hnefatafl.cli worker --server ${origin} --work-dir ./data/remote-worker`,
    );
  },
);

it('queues GPU training on a remote worker even when the server only has a CPU', async () => {
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    if (init?.method === 'POST') return json({ ...first, status: 'queued' });
    if (path.endsWith('/workers')) return json([worker]);
    if (path.endsWith('/device')) return json({ cuda_available: false, mps_available: false });
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rulesets} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  await screen.findByText('Cloud GPU');
  fireEvent.change(screen.getByLabelText('Run training on'), { target: { value: 'worker:cloud' } });
  expect(screen.getByRole('option', { name: 'NVIDIA CUDA' })).not.toBeDisabled();
  fireEvent.change(screen.getByLabelText('Training device'), { target: { value: 'cuda' } });
  fireEvent.click(screen.getByRole('button', { name: /Queue training/ }));
  await screen.findByText('Training queued. An available remote worker will pick it up.');
  const sent = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(String(sent[1]?.body))).toMatchObject({
    execution: 'remote',
    worker_id: 'cloud',
    device: 'cuda',
  });
  fireEvent.click(screen.getByRole('button', { name: /New training/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  expect(screen.getByLabelText('Run training on')).toBeDisabled();
  expect(screen.getByLabelText('Run training on')).toHaveValue('local');
  expect(
    screen.getByText(
      'Parameter tuning runs on this server. Remote workers support model training.',
    ),
  ).toBeInTheDocument();
});

it('enables every device for an unspecified remote worker and follows the chosen worker', async () => {
  const cpuWorker: TrainingWorker = {
    ...worker,
    id: 'cpu',
    name: 'CPU node',
    capabilities: { cuda_available: false, mps_available: false },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path.endsWith('/workers')) return json([worker, cpuWorker]);
      if (path.endsWith('/device')) return json({ cuda_available: false, mps_available: false });
      return json([]);
    }),
  );
  render(<Training rulesets={rulesets} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  await screen.findByRole('option', { name: /CPU node/ });
  expect(screen.getByRole('option', { name: 'NVIDIA CUDA (unavailable)' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Run training on'), { target: { value: 'remote' } });
  expect(screen.getByRole('option', { name: 'NVIDIA CUDA' })).toBeEnabled();
  expect(screen.getByRole('option', { name: 'Apple Metal' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Run training on'), { target: { value: 'worker:cpu' } });
  expect(screen.getByRole('option', { name: 'NVIDIA CUDA (unavailable)' })).toBeDisabled();
  expect(screen.getByRole('option', { name: 'Apple Metal (unavailable)' })).toBeDisabled();
});

it('refreshes completed models from another job without changing the selected progress graph', async () => {
  let jobs: Job[] = [first, second];
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      json(path.endsWith('/jobs') ? jobs : path.endsWith('/workers') ? [worker] : {}),
    ),
  );
  const changed = vi.fn();
  render(<Training rulesets={rulesets} models={[]} onModelsChanged={changed} onError={vi.fn()} />);
  fireEvent.click(
    (await screen.findByText('Cloud Ulf', { selector: 'strong' })).closest('button')!,
  );
  await waitFor(() =>
    expect(
      screen.getByRole('img', { name: 'Loss by iteration' }).querySelectorAll('circle'),
    ).toHaveLength(2),
  );
  jobs = [first, { ...second, status: 'completed', model_id: 'new-model' }];
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(
    screen.getByRole('img', { name: 'Loss by iteration' }).querySelectorAll('circle'),
  ).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: /Cloud Freydis/ }));
  await waitFor(() =>
    expect(
      screen.getByRole('img', { name: 'Loss by iteration' }).querySelectorAll('circle'),
    ).toHaveLength(4),
  );
});

it('does not replace a newly selected job with an earlier refresh response', async () => {
  const pending: ((response: Response) => void)[] = [];
  let calls = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path.endsWith('/jobs') && ++calls > 1)
        return new Promise<Response>((resolve) => pending.push(resolve));
      return json(path.endsWith('/jobs') ? [first, second] : path.endsWith('/workers') ? [] : {});
    }),
  );
  render(<Training rulesets={rulesets} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  fireEvent.click(
    (await screen.findByText('Cloud Ulf', { selector: 'strong' })).closest('button')!,
  );
  fireEvent.click(screen.getByRole('button', { name: /Cloud Freydis/ }));
  await act(async () => pending[1](await json([first, second])));
  await act(async () => pending[0](await json([first])));
  expect(
    screen.getByRole('img', { name: 'Loss by iteration' }).querySelectorAll('circle'),
  ).toHaveLength(4);
});

it('can move a checkpoint resume to another execution target without copying worker secrets', async () => {
  const source = {
    ...second,
    status: 'completed',
    target_worker_id: worker.id,
    config: { iterations: 4 },
  };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(
      init?.method === 'POST'
        ? { ...source, id: 'next' }
        : {
            completed_iteration: 2,
            can_resume: true,
            ruleset: 'brandubh',
            channels: 4,
            blocks: 0,
            optimizer: 'adamw',
            model_name: 'Cloud Freydis',
          },
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  render(
    <TrainingResumeDialog job={source} workers={[worker]} onClose={vi.fn()} onStarted={vi.fn()} />,
  );
  expect(await screen.findByLabelText('Run training on')).toHaveValue('worker:cloud');
  fireEvent.change(screen.getByLabelText('Run training on'), { target: { value: 'local' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
    iterations: 4,
    execution: 'local',
  });
});

it('localizes worker state and setup controls in German', () => {
  setLanguage('de', false);
  render(<TrainingWorkers workers={[{ ...worker, status: 'offline' }]} onChanged={vi.fn()} />);
  expect(screen.getByRole('heading', { name: 'Externe Worker' })).toBeInTheDocument();
  expect(screen.getByText('Nicht verbunden')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Worker verbinden' }));
  expect(screen.getByRole('dialog', { name: 'Externen Worker verbinden' })).toBeInTheDocument();
});
