import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import { setLanguage } from './i18n';
import type { Job } from './types';

const auth = vi.hoisted(() => ({ admin: true }));
vi.mock('./Auth', () => ({
  useAuth: () => ({ account: { permissions: auth.admin ? ['training'] : [] } }),
}));

const owner: Job = {
  id: '2'.repeat(32),
  kind: 'tune',
  status: 'completed',
  name: 'Owner job',
  config: { study_name: 'Shared search', training: { ruleset: 'brandubh' } },
  progress: {},
};
const contributor: Job = { ...owner, id: '3'.repeat(32), name: 'Later contributor' };
const source: Job = {
  id: '1'.repeat(32),
  kind: 'train',
  status: 'completed',
  name: 'Source training',
  config: { ruleset: 'brandubh' },
};
const report = () => ({
  study_name: 'Shared search',
  study_identity: 'a'.repeat(64),
  owner_job_id: owner.id,
  jobs: [owner, contributor].map((job) => ({
    id: job.id,
    name: job.name,
    status: job.status,
    directory: `jobs/${job.id}/`,
    trial_directories: ['trial-0/'],
    worker_alive: false,
  })),
  preserved_source_job_ids: [source.id],
  external_dependencies: [] as { id: string; name: string; kind: string; status: string }[],
  blocked_reasons: [] as string[],
  can_delete: true,
  confirmation_token: 'b'.repeat(64),
});
const response = (value: unknown, ok = true) =>
  Promise.resolve({ ok, status: ok ? 200 : 409, json: async () => value } as Response);

beforeEach(() => {
  auth.admin = true;
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
  vi.restoreAllMocks();
  setLanguage('en', false);
});

function show(
  options: {
    preview?: () => Promise<Response>;
    remove?: (path: string) => Promise<Response>;
    jobs?: Job[];
  } = {},
) {
  let jobs = options.jobs ?? [owner, contributor, source];
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    if (path.endsWith('/study-deletion-preview')) return options.preview?.() ?? response(report());
    if (path.includes('/study?') && init?.method === 'DELETE') {
      if (options.remove) return options.remove(path);
      jobs = [source];
      return response({
        deleted: true,
        job_ids: [owner.id, contributor.id],
        cleanup_pending: false,
      });
    }
    if (path.split('?')[0] === '/api/training/jobs') return response(jobs);
    return response([]);
  });
  vi.stubGlobal('fetch', fetcher);
  const modelsChanged = vi.fn();
  render(<Training rulesets={[]} models={[]} onModelsChanged={modelsChanged} onError={vi.fn()} />);
  return { fetcher, modelsChanged };
}
async function open(language = 'en') {
  fireEvent.click(await screen.findByRole('button', { name: /Owner job/ }));
  fireEvent.click(
    screen.getByRole('button', {
      name: language === 'de' ? 'Gesamte Optimierungsstudie löschen' : 'Delete entire tuning study',
    }),
  );
  return within(
    screen.getByRole('dialog', {
      name:
        language === 'de' ? 'Gesamte Optimierungsstudie löschen?' : 'Delete entire tuning study?',
    }),
  );
}

it('loads a bounded preview before enabling explicit whole-study confirmation and permits cancellation', async () => {
  let finish!: (value: Response) => void;
  const { fetcher } = show({
    preview: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  });
  const dialog = await open();
  expect(dialog.getByRole('button', { name: 'Delete entire study' })).toBeDisabled();
  expect(dialog.getByText('Loading study deletion preview…')).toBeInTheDocument();
  await act(async () => finish(await response(report())));
  expect(dialog.getByText('Later contributor · completed')).toBeInTheDocument();
  expect(dialog.getByText(`jobs/${owner.id}/`)).toBeInTheDocument();
  expect(dialog.getByText(/This cannot be undone/)).toBeInTheDocument();
  expect(dialog.getByRole('button', { name: 'Delete entire study' })).toBeDisabled();
  fireEvent.click(dialog.getByRole('button', { name: 'Keep it' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
});

it('submits the actual confirmed token and refreshes every removed job and active selection', async () => {
  const { fetcher, modelsChanged } = show();
  const dialog = await open();
  fireEvent.click(
    await dialog.findByRole('checkbox', {
      name: 'I confirm deletion of the entire study and all listed tuning jobs.',
    }),
  );
  fireEvent.click(dialog.getByRole('button', { name: 'Delete entire study' }));
  await screen.findByText(
    'Study and contributing tuning jobs deleted. Source jobs, registered models and saved game history have been kept.',
  );
  expect(fetcher).toHaveBeenCalledWith(
    `/api/training/jobs/${owner.id}/study?confirmation_token=${report().confirmation_token}`,
    expect.objectContaining({ method: 'DELETE' }),
  );
  await waitFor(() => expect(screen.queryByRole('button', { name: /Owner job/ })).toBeNull());
  expect(screen.queryByRole('button', { name: /Later contributor/ })).toBeNull();
  expect(screen.getByRole('button', { name: /Source training/ })).toBeInTheDocument();
  expect(
    fetcher.mock.calls.filter(([path]) => path.split('?')[0] === '/api/training/jobs').length,
  ).toBeGreaterThan(1);
  expect(modelsChanged).not.toHaveBeenCalled();
});

it.each(['external', 'worker'])(
  'shows blocking dependencies and disables deletion (%s)',
  async (blocker) => {
    const next = report();
    next.can_delete = false;
    next.blocked_reasons = ['Blocked'];
    if (blocker === 'external')
      next.external_dependencies = [
        {
          id: 'outside-job',
          name: 'Winning checkpoint continuation',
          kind: 'train',
          status: 'completed',
        },
      ];
    else next.jobs[1].worker_alive = true;
    const { fetcher } = show({ preview: () => response(next) });
    const dialog = await open();
    const checkbox = await dialog.findByRole('checkbox');
    expect(checkbox).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Delete entire study' })).toBeDisabled();
    expect(dialog.getByText(/Stop participating jobs/)).toBeInTheDocument();
    if (blocker === 'external')
      expect(dialog.getByText(/Winning checkpoint continuation/)).toBeInTheDocument();
    else
      expect(dialog.getByText('Worker still active; stop it before deletion.')).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  },
);

it('retains a failed preview dialog and allows a new check', async () => {
  let calls = 0;
  show({
    preview: () =>
      ++calls === 1
        ? response({ detail: 'Study database is unreadable.' }, false)
        : response(report()),
  });
  const dialog = await open();
  expect(await dialog.findByRole('alert')).toHaveTextContent('Study database is unreadable.');
  expect(dialog.getByRole('button', { name: 'Delete entire study' })).toBeDisabled();
  fireEvent.click(dialog.getByRole('button', { name: 'Check again' }));
  expect(await dialog.findByRole('checkbox')).not.toBeChecked();
});

it('keeps the confirmation open and prevents duplicate requests while deletion is pending', async () => {
  let finish!: (value: Response) => void;
  const { fetcher } = show({
    remove: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  });
  const dialog = await open();
  fireEvent.click(await dialog.findByRole('checkbox'));
  fireEvent.click(dialog.getByRole('button', { name: 'Delete entire study' }));
  expect(dialog.getByRole('button', { name: 'Deleting…' })).toBeDisabled();
  expect(dialog.getByRole('button', { name: 'Keep it' })).toBeDisabled();
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(1);
  await act(async () => finish(await response({ detail: 'Filesystem access failed.' }, false)));
  expect(await dialog.findByRole('alert')).toHaveTextContent('Filesystem access failed.');
  expect(await dialog.findByRole('checkbox')).not.toBeChecked();
});

it('reloads after API rejection and requires new consent for the changed scope', async () => {
  let previews = 0;
  const next = report();
  next.confirmation_token = 'c'.repeat(64);
  next.jobs.push({
    ...next.jobs[1],
    id: '4'.repeat(32),
    name: 'New contributor',
    directory: `jobs/${'4'.repeat(32)}/`,
  });
  const { fetcher } = show({
    preview: () => response(++previews === 1 ? report() : next),
    remove: () => response({ detail: 'The study deletion scope changed. Review it again.' }, false),
  });
  const dialog = await open();
  fireEvent.click(await dialog.findByRole('checkbox'));
  fireEvent.click(dialog.getByRole('button', { name: 'Delete entire study' }));
  expect(await dialog.findByRole('alert')).toHaveTextContent('scope changed');
  await dialog.findByText('New contributor · completed');
  expect(dialog.getByRole('checkbox')).not.toBeChecked();
  expect(dialog.getByRole('button', { name: 'Delete entire study' })).toBeDisabled();
  fireEvent.click(dialog.getByRole('checkbox'));
  fireEvent.click(dialog.getByRole('button', { name: 'Delete entire study' }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      expect.stringContaining(`confirmation_token=${next.confirmation_token}`),
      expect.objectContaining({ method: 'DELETE' }),
    ),
  );
});

it('translates the complete review and confirmation into German', async () => {
  setLanguage('de', false);
  show();
  const dialog = await open('de');
  expect(
    await dialog.findByRole('checkbox', {
      name: 'Ich bestätige das Löschen der gesamten Studie und aller aufgeführten Optimierungsaufträge.',
    }),
  ).toBeEnabled();
  expect(dialog.getByText(/Dies kann nicht rückgängig gemacht werden/)).toBeInTheDocument();
  expect(dialog.getByText(/gespeicherte Spiele und Turniere bleiben erhalten/)).toBeInTheDocument();
  expect(dialog.getByRole('button', { name: 'Gesamte Studie löschen' })).toBeDisabled();
});

it.each(['non-admin', 'running'])(
  'does not offer whole-study deletion to %s',
  async (condition) => {
    auth.admin = condition !== 'non-admin';
    show({ jobs: [{ ...owner, status: condition === 'running' ? 'running' : 'completed' }] });
    fireEvent.click(await screen.findByRole('button', { name: /Owner job/ }));
    expect(screen.queryByRole('button', { name: 'Delete entire tuning study' })).toBeNull();
  },
);
