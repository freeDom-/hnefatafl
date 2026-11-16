import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useOnlinePoll } from './useOnlinePoll';
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
it('invalidates the existing read cycle on profile changes without another timer or listener after unmount', async () => {
  const load = vi.fn().mockResolvedValue('Thor');
  const { result, unmount } = renderHook(() => useOnlinePoll('identities', load));
  await act(async () => {
    await Promise.resolve();
  });
  expect(result.current.data).toBe('Thor');
  load.mockResolvedValue('Freya');
  await act(async () => {
    window.dispatchEvent(new Event('profile-changed'));
  });
  expect(result.current.data).toBe('Freya');
  expect(load).toHaveBeenCalledTimes(2);
  unmount();
  window.dispatchEvent(new Event('profile-changed'));
  expect(load).toHaveBeenCalledTimes(2);
});
it('coalesces focus refreshes without overlapping reads and ignores late data after a mutation', async () => {
  vi.useFakeTimers();
  const first = deferred<number>();
  const second = deferred<number>();
  const load = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const { result, unmount } = renderHook(() => useOnlinePoll('game', load));
  act(() => {
    window.dispatchEvent(new Event('focus'));
    window.dispatchEvent(new Event('online'));
  });
  expect(load).toHaveBeenCalledTimes(1);
  act(() => {
    result.current.hold();
    result.current.accept(9);
    void result.current.resume();
  });
  await act(async () => {
    first.resolve(1);
    await first.promise;
  });
  expect(result.current.data).toBe(9);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => {
    second.resolve(10);
    await second.promise;
  });
  expect(result.current.data).toBe(10);
  const signal = load.mock.calls[1][0] as AbortSignal;
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(load).toHaveBeenCalledTimes(2);
});
it('backs off failures to a bounded delay and refreshes immediately on reconnect', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockRejectedValue(new Error('offline'));
  const { result } = renderHook(() => useOnlinePoll('lobby', load));
  await act(async () => {
    await Promise.resolve();
  });
  expect(result.current.phase).toBe('stale');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(9999);
  });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(19999);
  });
  expect(load).toHaveBeenCalledTimes(2);
  load.mockResolvedValue(7);
  await act(async () => {
    window.dispatchEvent(new Event('online'));
  });
  expect(result.current.data).toBe(7);
  expect(result.current.phase).toBe('ready');
});
it('clears protected state and aborts old-account reads on a resource change', async () => {
  const old = deferred<string>();
  const next = deferred<string>();
  const load = vi.fn((signal: AbortSignal) => {
    void signal;
    return load.mock.calls.length === 1 ? old.promise : next.promise;
  });
  const { result, rerender } = renderHook(({ account }) => useOnlinePoll(account, load), {
    initialProps: { account: 'first' },
  });
  const signal = load.mock.calls[0][0];
  rerender({ account: 'second' });
  expect(signal.aborted).toBe(true);
  expect(result.current.data).toBeNull();
  await act(async () => {
    old.resolve('late');
    await old.promise;
  });
  expect(result.current.key).toBe('second');
  expect(result.current.data).toBeNull();
  await act(async () => {
    next.resolve('current account');
    await next.promise;
  });
  expect(result.current.data).toBe('current account');
});

it('resynchronizes immediately when a backgrounded tab becomes visible and removes the listener', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockResolvedValue(1);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  const { result, unmount } = renderHook(() => useOnlinePoll('visible-lobby', load));
  await act(async () => {
    await Promise.resolve();
  });
  load.mockResolvedValue(2);
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(load).toHaveBeenCalledTimes(1);
  visibility.mockReturnValue('visible');
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(result.current.data).toBe(2);
  expect(load).toHaveBeenCalledTimes(2);
  unmount();
  document.dispatchEvent(new Event('visibilitychange'));
  expect(load).toHaveBeenCalledTimes(2);
  visibility.mockRestore();
});

it('loads once under an intervalMs of 0 and reloads only on an explicit signal', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockResolvedValue('ledger');
  const { result, unmount } = renderHook(() => useOnlinePoll('history', load, { intervalMs: 0 }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.data).toBe('ledger');
  expect(load).toHaveBeenCalledTimes(1);
  // No recurring timer: two minutes of the old cadence issues nothing.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(load).toHaveBeenCalledTimes(1);
  load.mockResolvedValue('refreshed');
  await act(async () => {
    await result.current.refresh();
  });
  expect(result.current.data).toBe('refreshed');
  expect(load).toHaveBeenCalledTimes(2);
  load.mockResolvedValue('focused');
  await act(async () => {
    window.dispatchEvent(new Event('focus'));
  });
  expect(result.current.data).toBe('focused');
  expect(load).toHaveBeenCalledTimes(3);
  unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(load).toHaveBeenCalledTimes(3);
});

it('aborts the in-flight read and stops under an intervalMs of 0 when unmounted', async () => {
  vi.useFakeTimers();
  const pending = deferred<string>();
  const load = vi.fn().mockReturnValue(pending.promise);
  const { unmount } = renderHook(() => useOnlinePoll('history', load, { intervalMs: 0 }));
  const signal = load.mock.calls[0][0] as AbortSignal;
  expect(signal.aborted).toBe(false);
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(load).toHaveBeenCalledTimes(1);
});

it('retains a 60-second historical cadence instead of accelerating to the old 30-second cap', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockResolvedValue('history');
  renderHook(() => useOnlinePoll('slow-history', load, { intervalMs: 60000 }));
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(59999);
  });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(load).toHaveBeenCalledTimes(2);
});

it('backs a failing 60-second poll off to a bounded 120-second retry and resets after recovery', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockRejectedValue(new Error('offline'));
  const { result, unmount } = renderHook(() =>
    useOnlinePoll('slow-history', load, { intervalMs: 60000 }),
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(load).toHaveBeenCalledTimes(1);
  expect(result.current.phase).toBe('stale');
  // A failed 60-second read is never retried sooner than its own cadence.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(59999);
  });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60001);
  });
  expect(load).toHaveBeenCalledTimes(2);
  // The second failure keeps the bounded 120-second retry, not the old flat 60.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(119999);
  });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(load).toHaveBeenCalledTimes(3);
  // A successful read resets the backoff to the configured cadence.
  load.mockResolvedValue('history');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  expect(load).toHaveBeenCalledTimes(4);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(59999);
  });
  expect(load).toHaveBeenCalledTimes(4);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(load).toHaveBeenCalledTimes(5);
  // Cleanup clears the long retry timer instead of leaving a pending read.
  unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(600000);
  });
  expect(load).toHaveBeenCalledTimes(5);
});

it('retains the bounded 30-second ceiling for a failing 10-second poll', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockRejectedValue(new Error('offline'));
  renderHook(() => useOnlinePoll('medium-history', load, { intervalMs: 10000 }));
  await act(async () => {
    await Promise.resolve();
  });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(19999);
  });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(load).toHaveBeenCalledTimes(2); // first failure doubles to 20 s
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(load).toHaveBeenCalledTimes(3); // second failure caps at 30 s
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(load).toHaveBeenCalledTimes(4);
});

it('pauses a 60-second historical poll while hidden and reads once on return', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockResolvedValue('history');
  const { result } = renderHook(() => useOnlinePoll('slow-history', load, { intervalMs: 60000 }));
  await act(async () => {
    await Promise.resolve();
  });
  expect(load).toHaveBeenCalledTimes(1);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  try {
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600000);
    });
    expect(load).toHaveBeenCalledTimes(1);
    load.mockResolvedValue('returned');
    visibility.mockReturnValue('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(load).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe('returned');
  } finally {
    visibility.mockRestore();
  }
});

it('aborts and invalidates a hidden read, sends no periodic or focus traffic while hidden, then wakes fresh', async () => {
  vi.useFakeTimers();
  const first = deferred<number>();
  const load = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(2);
  const { result, unmount } = renderHook(() => useOnlinePoll('visibility-budget', load));
  const signal = load.mock.calls[0][0] as AbortSignal;
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  try {
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    expect(signal.aborted).toBe(true);
    act(() => {
      window.dispatchEvent(new Event('focus'));
      window.dispatchEvent(new Event('online'));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120000);
      first.resolve(1);
      await first.promise;
    });
    expect(load).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBeNull();
    visibility.mockReturnValue('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(load).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe(2);
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120000);
    });
    expect(load).toHaveBeenCalledTimes(2);
  } finally {
    visibility.mockRestore();
  }
});

it('keeps accepted mutation state and hold/resume semantics across a hidden pause', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockResolvedValue(1);
  const { result } = renderHook(() => useOnlinePoll('held-hidden-game', load));
  await act(async () => {
    await Promise.resolve();
  });
  act(() => {
    result.current.hold();
    result.current.accept(9);
  });
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  try {
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    visibility.mockReturnValue('visible');
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    expect(load).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe(9);
    load.mockResolvedValue(10);
    await act(async () => {
      await result.current.resume();
    });
    expect(result.current.data).toBe(10);
    expect(load).toHaveBeenCalledTimes(2);
  } finally {
    visibility.mockRestore();
  }
});

it('preserves the participant-game five-second freshness and clears every timer on unmount', async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockResolvedValue('active');
  const { unmount } = renderHook(() => useOnlinePoll('participant-game', load));
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(load).toHaveBeenCalledTimes(13); // Initial plus twelve live reads.
  unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(load).toHaveBeenCalledTimes(13);
});

it('isolates account data and cancelled reads when the account changes while hidden', async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const load = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const { result, rerender } = renderHook(({ id }) => useOnlinePoll(id, load), {
    initialProps: { id: 'A' },
  });
  const firstSignal = load.mock.calls[0][0] as AbortSignal;
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  try {
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    rerender({ id: 'B' });
    expect(firstSignal.aborted).toBe(true);
    expect(result.current.key).toBe('B');
    expect(result.current.data).toBeNull();
    await act(async () => {
      first.resolve('private A');
      second.resolve('private B');
      await Promise.all([first.promise, second.promise]);
    });
    expect(result.current.data).toBe('private B');
  } finally {
    visibility.mockRestore();
  }
});
