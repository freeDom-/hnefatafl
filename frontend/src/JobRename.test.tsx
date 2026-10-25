import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Training from './Training';
import type { Job, Ruleset } from './types';
import { notify } from './notifications';

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];

const job: Job = {
  id: 'a'.repeat(32),
  status: 'completed',
  kind: 'train',
  execution: 'local',
  config: { ruleset: 'brandubh', model_name: 'Original name', iterations: 5 },
  progress: {},
  created_at: '2026-10-21T08:00:00Z',
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
  vi.restoreAllMocks();
});

function show(fetcher: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetcher);
  return render(
    <Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />,
  );
}

const openRename = async () => {
  const button = await screen.findByRole('button', { name: 'Rename job' });
  fireEvent.click(button);
  return within(screen.getByRole('dialog', { name: 'Rename training job' }));
};

describe('training job rename', () => {
  it('prepopulates the current name and persists an edit without a page reload', async () => {
    let current = job;
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (path === `/api/training/jobs/${job.id}` && init?.method === 'PATCH') {
        expect(JSON.parse(String(init.body))).toEqual({ name: 'Raven campaign' });
        current = { ...job, name: 'Raven campaign' };
        return json(current);
      }
      if (path === '/api/training/jobs') return json([current]);
      return json([]);
    });
    show(fetcher);
    const dialog = await openRename();
    const input = dialog.getByLabelText('Job name') as HTMLInputElement;
    expect(input.value).toBe('Original name');
    fireEvent.change(input, { target: { value: '  Raven campaign  ' } });
    fireEvent.click(dialog.getByRole('button', { name: 'Save name' }));
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        `/api/training/jobs/${job.id}`,
        expect.objectContaining({ method: 'PATCH' }),
      ),
    );
    // The dashboard heading refreshes from the response without remounting the app.
    await waitFor(() => expect(screen.getAllByText('Raven campaign').length).toBeGreaterThan(0));
    expect(screen.queryByRole('dialog', { name: 'Rename training job' })).not.toBeInTheDocument();
  });

  it('cancels without sending a request', async () => {
    const fetcher = vi.fn((path: string, _init?: RequestInit) =>
      path === '/api/training/jobs' ? json([job]) : json([]),
    );
    show(fetcher);
    const dialog = await openRename();
    fireEvent.change(dialog.getByLabelText('Job name'), { target: { value: 'Discarded' } });
    fireEvent.click(dialog.getByRole('button', { name: 'Cancel' }));
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  });

  it('keeps the entered text and reports a failure through the shared notification layer', async () => {
    const error = vi.spyOn(notify, 'error');
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (path === `/api/training/jobs/${job.id}` && init?.method === 'PATCH')
        return json({ detail: 'Training job names must contain 1–100 characters.' }, false);
      if (path === '/api/training/jobs') return json([job]);
      return json([]);
    });
    show(fetcher);
    const dialog = await openRename();
    const input = dialog.getByLabelText('Job name') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Will fail' } });
    fireEvent.click(dialog.getByRole('button', { name: 'Save name' }));
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(input.value).toBe('Will fail');
    expect(screen.getByRole('dialog', { name: 'Rename training job' })).toBeInTheDocument();
  });
});
