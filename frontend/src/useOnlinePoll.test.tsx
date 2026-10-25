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
