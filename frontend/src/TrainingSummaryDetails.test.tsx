import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Training from './Training';
import type { Job } from './types';
import { setLanguage } from './i18n';

// Observe the actual selected payload; chart rendering has its own component tests.
vi.mock('./TrainingJobDashboard', () => ({
  default: ({ job, onRefresh }: { job: Job; onRefresh: () => void }) => (
    <>
      <output data-testid="selected-detail">{JSON.stringify(job)}</output>
      <button onClick={onRefresh}>Refresh selected job</button>
    </>
  ),
}));

const json = (value: unknown) => ({ ok: true, json: async () => value }) as Response;
function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function full(id: string, status = 'completed'): Job {
  const history = Array.from({ length: 1200 }, (_, i) => ({ iteration: i + 1, id }));
  return {
    id,
    name: `Job ${id}`,
    status,
    kind: 'train',
    config: { preset: 'smoke', ruleset: 'brandubh' },
    progress: {
      iteration: 1200,
      history,
      metrics: {
        evaluation: { score: 0.8, games: history },
        performance: { history, rates: { cpu: 10, gpu: 20 } },
      },
    },
    metrics: { history, evaluation: { score: 0.8, games: history } },
    result: { history, diagnostics: { id } },
  };
}
const selected = (): Job => JSON.parse(screen.getByTestId('selected-detail').textContent!);
async function settle() {
  await vi.advanceTimersByTimeAsync(0);
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each(['completed', 'cancelled', 'failed', 'running'])(
  'retains %s analysis with detail completing before or after navigator refreshes',
  async (status) => {
    vi.useFakeTimers();
    setLanguage('en', false);
    const original = full('A', status);
    const detail = deferred();
    let lists = 0;
    const fetcher = vi.fn((path: string) => {
      if (path === '/api/training/jobs/A') return detail.promise;
      if (path.startsWith('/api/training/jobs?'))
        return Promise.resolve(
          json([
            {
              id: 'A',
              name: 'Job A',
              status,
              summary: true,
              progress: {
                iteration: 1200 + lists++,
                metrics: { evaluation: { score: 0.9 }, performance: { rates: { cpu: lists } } },
              },
              metrics: { evaluation: { score: 0.9 } },
            },
          ]),
        );
      return Promise.resolve(json(path.endsWith('/device') ? { cpu_available: true } : []));
    });
    vi.stubGlobal('fetch', fetcher);
    const error = vi.fn();
    await act(async () => {
      render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
      await settle();
    });
    for (let i = 0; i < 3; i++)
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
        await settle();
      });
    expect(fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs/A')).toHaveLength(1);
    await act(async () => {
      detail.resolve(json(original));
      await settle();
    });
    for (let i = 0; i < 3; i++) {
      const loaded = selected();
      expect(loaded.summary).toBe(false);
      expect(loaded.progress?.iteration).toBe(1200 + lists - 1);
      expect(loaded.progress?.history).toEqual(original.progress?.history);
      expect(loaded.metrics?.history).toEqual(original.metrics?.history);
      expect(loaded.metrics?.evaluation).toMatchObject({
        score: 0.9,
        games: original.metrics?.history,
      });
      expect(loaded.progress?.metrics).toMatchObject({
        evaluation: { games: original.progress?.history },
        performance: { history: original.progress?.history, rates: { gpu: 20 } },
      });
      expect(loaded.result).toEqual(original.result);
      // Active polling needs fresh detail; terminal refreshes reuse the loaded analysis.
      if (status !== 'running')
        await act(async () => {
          fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
          await settle();
        });
    }
    expect(fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs/A')).toHaveLength(1);
    expect(error).not.toHaveBeenCalled();
  },
);

it('ignores a stale detail after switching jobs and retains the new selection on refresh', async () => {
  vi.useFakeTimers();
  setLanguage('en', false);
  const first = deferred(),
    second = deferred();
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/training/jobs/A') return first.promise;
      if (path === '/api/training/jobs/B') return second.promise;
      return Promise.resolve(
        json(
          path.startsWith('/api/training/jobs?')
            ? ['A', 'B'].map((id) => ({
                id,
                name: `Job ${id}`,
                status: 'completed',
                summary: true,
                progress: { iteration: 1200 },
              }))
            : path.endsWith('/device')
              ? { cpu_available: true }
              : [],
        ),
      );
    }),
  );
  await act(async () => {
    render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
    await settle();
  });
  expect(selected().id).toBe('A');
  await act(async () => {
    fireEvent.change(screen.getByLabelText('Selected job'), { target: { value: 'B' } });
    await settle();
  });
  expect(selected().id).toBe('B');
  expect(selected().progress?.history).toBeUndefined();
  await act(async () => {
    second.resolve(json(full('B')));
    await settle();
  });
  await act(async () => {
    first.resolve(json(full('A')));
    await settle();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
    await settle();
  });
  expect(selected().id).toBe('B');
  expect(selected().progress?.history).toEqual(full('B').progress?.history);
  expect(selected().metrics).toEqual(full('B').metrics);
});
