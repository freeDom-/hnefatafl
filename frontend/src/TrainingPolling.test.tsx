import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { StrictMode } from 'react';
import Training from './Training';
import { workshopFixture } from '../tests/workshop-fixtures';
import { setLanguage } from './i18n';

const json = (value: unknown) => ({ ok: true, json: async () => value }) as Response;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function show(onError: (message: string) => void = vi.fn(), strict = false) {
  setLanguage('en', false);
  const view = <Training rulesets={[]} models={[]} onError={onError} onModelsChanged={vi.fn()} />;
  render(strict ? <StrictMode>{view}</StrictMode> : view);
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each(['device', 'workers'])(
  'loads jobs while the %s response is still pending',
  async (endpoint) => {
    vi.useFakeTimers();
    const hardware = deferred<Response>();
    const job = workshopFixture('running', 1);
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path.endsWith(`/${endpoint}`)
          ? hardware.promise
          : Promise.resolve(json(path.includes('/jobs') ? [job] : [])),
      ),
    );
    await act(async () => {
      show();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { name: 'Eir · Tablut strategist' })).toBeInTheDocument();
    hardware.resolve(json(endpoint === 'device' ? { cpu_available: true } : []));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  },
);

it('continues polling after a transient failure without losing the displayed job', async () => {
  vi.useFakeTimers();
  const onError = vi.fn();
  let calls = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path.startsWith('/api/training/jobs?')) {
        calls++;
        if (calls === 2)
          return Promise.resolve({
            ok: false,
            status: 503,
            json: async () => ({ detail: 'Temporarily unavailable' }),
          } as Response);
        const job = workshopFixture('running', calls);
        job.name = calls === 1 ? 'Initial job' : 'Recovered live job';
        return Promise.resolve(json([job]));
      }
      return Promise.resolve(json(path.endsWith('/device') ? { cpu_available: true } : []));
    }),
  );
  await act(async () => {
    show(onError);
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500);
  });
  expect(screen.getByRole('heading', { name: 'Initial job' })).toBeInTheDocument();
  expect(onError).toHaveBeenCalledWith('Temporarily unavailable');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500);
  });
  expect(screen.getByRole('heading', { name: 'Recovered live job' })).toBeInTheDocument();
});

it('restarts aborted mount requests during React strict-mode replay', async () => {
  vi.useFakeTimers();
  const onError = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      Promise.resolve(
        json(
          path.startsWith('/api/training/jobs?')
            ? [workshopFixture('running', 1)]
            : path.endsWith('/device')
              ? { cpu_available: true }
              : [],
        ),
      ),
    ),
  );
  await act(async () => {
    show(onError, true);
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByRole('heading', { name: 'Eir · Tablut strategist' })).toBeInTheDocument();
  expect(onError).not.toHaveBeenCalled();
});

it('refreshes a mutation after its obsolete in-flight poll fails', async () => {
  vi.useFakeTimers();
  const old = deferred<Response>();
  const onError = vi.fn();
  const job = workshopFixture('running', 1);
  let reads = 0;
  let cancelled = false;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path.endsWith('/cancel')) {
        cancelled = true;
        return Promise.resolve(json({ ...job, status: 'cancelled' }));
      }
      if (path.startsWith('/api/training/jobs?')) {
        reads++;
        return reads === 2
          ? old.promise
          : Promise.resolve(json([{ ...job, status: cancelled ? 'cancelled' : 'running' }]));
      }
      return Promise.resolve(json(path.endsWith('/device') ? { cpu_available: true } : []));
    }),
  );
  await act(async () => {
    show(onError);
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Stop training' }));
    await vi.advanceTimersByTimeAsync(0);
  });
  old.resolve({
    ok: false,
    status: 503,
    json: async () => ({ detail: 'Obsolete read failed' }),
  } as Response);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(reads).toBe(3);
  expect(document.querySelector('.selected-job-summary .job-status')).toHaveTextContent(
    'cancelled',
  );
  expect(onError).not.toHaveBeenCalled();
});

it('coalesces slow polls and applies their result instead of invalidating it every interval', async () => {
  vi.useFakeTimers();
  const slow = deferred<Response>();
  const initial = workshopFixture('running', 1);
  let requests = 0;
  const fetcher = vi.fn((path: string) => {
    if (path.split('?')[0] === '/api/training/jobs') {
      requests++;
      return requests === 1 ? Promise.resolve(json([initial])) : slow.promise;
    }
    return Promise.resolve(json(path.endsWith('/device') ? { cpu_available: true } : []));
  });
  vi.stubGlobal('fetch', fetcher);
  await act(async () => {
    show();
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(7500);
  });
  expect(requests).toBe(2);
  const next = workshopFixture('running', 2);
  next.name = 'Live update received';
  slow.resolve(json([next]));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByRole('heading', { name: 'Live update received' })).toBeInTheDocument();
});

it('keeps polling summaries while selected detail is slow and preserves newer progress', async () => {
  vi.useFakeTimers();
  const detail = deferred<Response>();
  let lists = 0;
  let details = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path.startsWith('/api/training/jobs/')) {
        details++;
        return detail.promise;
      }
      if (path.startsWith('/api/training/jobs?')) {
        const job = workshopFixture('running', ++lists);
        job.summary = true;
        delete job.progress!.history;
        return Promise.resolve(json([job]));
      }
      return Promise.resolve(json(path.endsWith('/device') ? { cpu_available: true } : []));
    }),
  );
  await act(async () => {
    show();
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(7500);
  });
  expect(lists).toBe(4);
  expect(details).toBe(1);
  expect(screen.getByText('Iteration 4 / 120')).toBeInTheDocument();
  detail.resolve(json(workshopFixture('running', 1)));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText('Iteration 4 / 120')).toBeInTheDocument();
  expect(document.querySelectorAll('figure')).not.toHaveLength(0);
});

it('loads details again when reselecting the same completed summary without active polling', async () => {
  vi.useFakeTimers();
  const job = workshopFixture('completed', 2, 'completed');
  const summary = { ...job, summary: true, progress: { iteration: 2 } };
  let details = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/training/jobs/completed') {
        details++;
        return Promise.resolve(json(job));
      }
      return Promise.resolve(
        json(
          path.startsWith('/api/training/jobs?')
            ? [summary]
            : path.endsWith('/device')
              ? { cpu_available: true }
              : [],
        ),
      );
    }),
  );
  await act(async () => {
    show();
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(details).toBe(1);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /Bjorn · Northern campaign/ }));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(details).toBe(2);
  expect(document.querySelectorAll('figure')).not.toHaveLength(0);
});

it('aborts pending monitoring requests and stops polling on unmount', async () => {
  vi.useFakeTimers();
  const signals: AbortSignal[] = [];
  const pending = deferred<Response>();
  const fetcher = vi.fn((_path: string, init: RequestInit) => {
    signals.push(init.signal as AbortSignal);
    return pending.promise;
  });
  vi.stubGlobal('fetch', fetcher);
  await act(async () => {
    show();
    await vi.advanceTimersByTimeAsync(0);
  });
  const count = fetcher.mock.calls.length;
  cleanup();
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  await act(async () => {
    pending.resolve(json([]));
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(fetcher).toHaveBeenCalledTimes(count);
});
