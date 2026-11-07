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

function execution(run: string, kind = 'train'): Job {
  const rows = [{ iteration: 3, run }];
  return {
    ...full('A'),
    kind,
    progress: {
      run,
      iteration: 3,
      history: kind === 'tune' ? [{ cumulative: true }] : rows,
      model: { id: `model-${run}`, name: `Name ${run}`, path: `/models/${run}.pt` },
      ...(kind === 'tune'
        ? { trial_progress: { run, trial: 4, history: rows, metrics: { history: rows } } }
        : {}),
    },
    metrics: { history: kind === 'tune' ? [{ cumulative: true }] : rows },
    result: { history: kind === 'tune' ? [{ cumulative: true }] : rows },
  };
}

it.each(['train', 'tune'])(
  'loads one replacement detail for an incompatible %s identity, before or after a poll',
  async (kind) => {
    vi.useFakeTimers();
    setLanguage('en', false);
    const initial = deferred(),
      replacement = deferred();
    let run = 'old',
      terminal = false,
      reads = 0;
    const fetcher = vi.fn((path: string) => {
      if (path === '/api/training/jobs/A')
        return ++reads === 1 ? initial.promise : replacement.promise;
      if (path.startsWith('/api/training/jobs?'))
        return Promise.resolve(
          json([
            {
              id: 'A',
              name: 'Job A',
              kind,
              status: terminal ? 'completed' : 'running',
              summary: true,
              progress: {
                run,
                iteration: 4,
                model: { id: `model-${run}` },
                ...(kind === 'tune' ? { trial_progress: { run, trial: 4, iteration: 4 } } : {}),
              },
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
    run = 'new';
    terminal = true;
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
      await settle();
    });
    await act(async () => {
      initial.resolve(json(execution('old', kind)));
      await settle();
    });
    expect(reads).toBe(2);
    expect(selected().summary).toBe(true);
    if (kind === 'tune') {
      expect(
        (selected().progress!.trial_progress as Record<string, unknown>).history,
      ).toBeUndefined();
      expect(selected().progress!.history).toEqual([{ cumulative: true }]);
    } else {
      expect(selected().progress!.history).toBeUndefined();
      expect(selected().metrics).toBeUndefined();
      expect(selected().result).toBeUndefined();
    }
    await act(async () => {
      replacement.resolve(json(execution('new', kind)));
      await settle();
    });
    expect(selected().summary).toBe(false);
    expect(selected().progress!.model).toEqual(execution('new', kind).progress!.model);
    const report =
      kind === 'tune'
        ? (selected().progress!.trial_progress as Record<string, unknown>)
        : selected().progress!;
    expect(report.history).toEqual([{ iteration: 3, run: 'new' }]);
    for (let i = 0; i < 3; i++)
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
        await settle();
      });
    expect(reads).toBe(2);
    expect(error).not.toHaveBeenCalled();
  },
);

it('reloads a changed completed execution after its former detail has already loaded', async () => {
  vi.useFakeTimers();
  setLanguage('en', false);
  let run = 'old',
    reads = 0;
  const next = deferred();
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/training/jobs/A')
        return ++reads === 1 ? Promise.resolve(json(execution('old'))) : next.promise;
      return Promise.resolve(
        json(
          path.startsWith('/api/training/jobs?')
            ? [{ id: 'A', status: 'completed', summary: true, progress: { run } }]
            : [],
        ),
      );
    }),
  );
  await act(async () => {
    render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
    await settle();
  });
  expect(selected().summary).toBe(false);
  run = 'new';
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
    await settle();
  });
  expect(reads).toBe(2);
  expect(selected().progress!.history).toBeUndefined();
  await act(async () => {
    next.resolve(json(execution('new')));
    await settle();
  });
  expect(selected().progress!.history).toEqual([{ iteration: 3, run: 'new' }]);
});

it.each(['train', 'tune'])(
  'rejects a stale %s detail even when the summary preceded the request',
  async (kind) => {
    vi.useFakeTimers();
    setLanguage('en', false);
    const replacement = deferred();
    let reads = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/training/jobs/A')
          return ++reads === 1
            ? Promise.resolve(json(execution('old', kind)))
            : replacement.promise;
        return Promise.resolve(
          json(
            path.startsWith('/api/training/jobs?')
              ? [
                  {
                    id: 'A',
                    kind,
                    status: 'completed',
                    summary: true,
                    progress: {
                      run: 'new',
                      iteration: 4,
                      ...(kind === 'tune' ? { trial_progress: { run: 'new', trial: 4 } } : {}),
                    },
                  },
                ]
              : [],
          ),
        );
      }),
    );
    const error = vi.fn();
    await act(async () => {
      render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
      await settle();
    });
    expect(reads).toBe(2);
    expect(selected().summary).toBe(true);
    const progress = selected().progress!;
    const report =
      kind === 'tune' ? (progress.trial_progress as Record<string, unknown>) : progress;
    expect(report.run).toBe('new');
    expect(report.history).toBeUndefined();
    expect(report.metrics).toBeUndefined();
    if (kind === 'train') {
      expect(selected().metrics).toBeUndefined();
      expect(selected().result).toBeUndefined();
    }
    await act(async () => {
      replacement.resolve(json(execution('new', kind)));
      await settle();
    });
    expect(selected().summary).toBe(false);
    expect(error).not.toHaveBeenCalled();
    expect(reads).toBe(2);
  },
);

it('replaces changed model metadata without redownloading the execution history', async () => {
  vi.useFakeTimers();
  setLanguage('en', false);
  let model = 'old',
    reads = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/training/jobs/A') {
        reads++;
        return Promise.resolve(json(execution('old')));
      }
      return Promise.resolve(
        json(
          path.startsWith('/api/training/jobs?')
            ? [
                {
                  id: 'A',
                  status: 'completed',
                  summary: true,
                  progress: { run: 'old', model: { id: `model-${model}` } },
                },
              ]
            : [],
        ),
      );
    }),
  );
  await act(async () => {
    render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
    await settle();
  });
  model = 'new';
  for (let i = 0; i < 3; i++)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
      await settle();
    });
  expect(reads).toBe(1);
  expect(selected().progress!.model).toEqual({ id: 'model-new' });
  expect(selected().progress!.history).toEqual([{ iteration: 3, run: 'old' }]);
});

it.each(['en', 'de'] as const)(
  'bounds immediate stale-identity retries and reports the existing error channel (%s)',
  async (language) => {
    vi.useFakeTimers();
    setLanguage(language, false);
    const pending = deferred();
    let run = 'old',
      reads = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/training/jobs/A')
          return ++reads === 1 ? pending.promise : Promise.resolve(json(execution('old')));
        return Promise.resolve(
          json(
            path.startsWith('/api/training/jobs?')
              ? [{ id: 'A', status: 'completed', summary: true, progress: { run } }]
              : [],
          ),
        );
      }),
    );
    const error = vi.fn();
    await act(async () => {
      render(<Training rulesets={[]} models={[]} onError={error} onModelsChanged={vi.fn()} />);
      await settle();
    });
    run = 'new';
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Refresh selected job' }));
      await settle();
    });
    await act(async () => {
      pending.resolve(json(execution('old')));
      await settle();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(reads).toBe(2);
    expect(selected().summary).toBe(true);
    expect(selected().progress!.history).toBeUndefined();
    expect(error).toHaveBeenCalledTimes(1);
    expect(error.mock.calls[0][0]).toContain(language === 'de' ? 'geändert' : 'changed');
  },
);
