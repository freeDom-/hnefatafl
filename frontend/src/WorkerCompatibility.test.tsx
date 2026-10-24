import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingWorkers from './TrainingWorkers';
import TrainingTarget from './TrainingTarget';
import { setLanguage } from './i18n';
import type { TrainingWorker } from './types';

const auth = vi.hoisted(() => ({ role: 'ADMIN' }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: { role: auth.role } }) }));
const compatible: TrainingWorker = {
  id: 'cloud',
  name: 'Cloud raven',
  capacity: 1,
  status: 'online',
  last_seen: null,
  active_jobs: 0,
  capabilities: { cuda_available: true },
  version_override_enabled: false,
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
const mismatch: TrainingWorker = {
  ...compatible,
  compatibility: {
    ...compatible.compatibility!,
    eligible: false,
    code: 'application_version_mismatch',
    reason: 'Application version mismatch',
    worker_version: '0.25.0',
    mismatches: ['application_version_mismatch'],
  },
};
beforeEach(() => {
  auth.role = 'ADMIN';
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
  setLanguage('en', false);
});

it('shows connectivity separately from application compatibility and versions', () => {
  const { container, rerender } = render(
    <TrainingWorkers workers={[mismatch]} onChanged={vi.fn()} />,
  );
  expect(screen.getByText('online')).toBeInTheDocument();
  expect(screen.getByText('Application version mismatch')).toHaveClass(
    'application_version_mismatch',
  );
  expect(screen.getByText(/Worker version: 0.25.0/)).toHaveTextContent('Server version: 0.26.0');
  expect(screen.getByText('NVIDIA CUDA')).toBeInTheDocument();
  rerender(
    <TrainingWorkers workers={[{ ...compatible, status: 'offline' }]} onChanged={vi.fn()} />,
  );
  expect(screen.getByText('offline')).toBeInTheDocument();
  expect(container.querySelector('.worker-compatibility.compatible')).toHaveTextContent(
    'Compatible',
  );
  rerender(
    <TrainingWorkers workers={[{ ...compatible, compatibility: undefined }]} onChanged={vi.fn()} />,
  );
  expect(container.querySelector('.unknown_application_version')).toHaveTextContent(
    'Missing or unknown application version',
  );
});

it('requires the override warning before an administrator mutation and supports disabling', async () => {
  const fetcher = vi.fn(() => Promise.resolve({ ok: true, json: async () => ({}) } as Response));
  vi.stubGlobal('fetch', fetcher);
  const changed = vi.fn();
  const { rerender } = render(<TrainingWorkers workers={[mismatch]} onChanged={changed} />);
  fireEvent.click(screen.getByRole('button', { name: 'Enable version override for Cloud raven' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Enable version override' }));
  expect(dialog.getByText(/invalid benchmark comparisons/)).toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(dialog.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(fetcher).toHaveBeenCalledWith(
    '/api/training/workers/cloud/version-override',
    expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ enabled: true }) }),
  );
  rerender(
    <TrainingWorkers
      workers={[
        {
          ...mismatch,
          version_override_enabled: true,
          compatibility: {
            ...mismatch.compatibility!,
            eligible: true,
            code: 'override_active',
            reason: 'Administrator override active',
            override_active: true,
          },
        },
      ]}
      onChanged={changed}
    />,
  );
  expect(screen.getByText(/Unsupported version combination/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Disable version override for Cloud raven' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
  expect(fetcher).toHaveBeenLastCalledWith(
    '/api/training/workers/cloud/version-override',
    expect.objectContaining({ body: JSON.stringify({ enabled: false }) }),
  );
});

it('hides override controls from non-administrators', () => {
  auth.role = 'USER';
  render(<TrainingWorkers workers={[mismatch]} onChanged={vi.fn()} />);
  expect(screen.queryByRole('button', { name: /version override/ })).not.toBeInTheDocument();
});

it('disables incompatible and unknown worker selections but allows a server-approved override', () => {
  const { rerender } = render(
    <TrainingTarget value="worker:cloud" workers={[mismatch]} onChange={vi.fn()} />,
  );
  expect(screen.getByRole('option', { name: /Cloud raven/ })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('Application version mismatch');
  rerender(
    <TrainingTarget
      value="worker:cloud"
      workers={[{ ...compatible, compatibility: undefined }]}
      onChange={vi.fn()}
    />,
  );
  expect(screen.getByRole('option', { name: /Cloud raven/ })).toBeDisabled();
  rerender(
    <TrainingTarget
      value="worker:cloud"
      workers={[{ ...compatible, version_override_enabled: true }]}
      onChange={vi.fn()}
    />,
  );
  expect(screen.getByRole('option', { name: /Cloud raven/ })).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent('Unsupported version combination');
});

it('localizes compatibility, version and override labels in German', () => {
  setLanguage('de', false);
  render(<TrainingWorkers workers={[mismatch]} onChanged={vi.fn()} />);
  expect(screen.getByText('Anwendungsversion stimmt nicht überein')).toBeInTheDocument();
  expect(screen.getByText(/Worker-Version: 0.25.0/)).toHaveTextContent('Server-Version: 0.26.0');
  expect(
    screen.getByRole('button', { name: 'Versionsausnahme für Cloud raven aktivieren' }),
  ).toBeInTheDocument();
});
