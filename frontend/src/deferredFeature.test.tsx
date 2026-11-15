import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useEffect, useState } from 'react';
import { deferredFeature } from './deferredFeature';
import { setLanguage } from './i18n';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  setLanguage('en', false);
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { resolve, promise };
};

it('does not load code until mounted and coalesces concurrent code loads without sharing component state', async () => {
  function Counter({ name }: { name: string }) {
    const [count, setCount] = useState(0);
    return (
      <button onClick={() => setCount(count + 1)}>
        {name}:{count}
      </button>
    );
  }
  const pending = deferred<{ default: typeof Counter }>();
  const load = vi.fn(() => pending.promise);
  const Feature = deferredFeature(load);
  expect(load).not.toHaveBeenCalled();
  render(
    <>
      <Feature name="A" />
      <Feature name="B" />
    </>,
  );
  expect(load).toHaveBeenCalledTimes(1);
  expect(screen.getAllByRole('status')).toHaveLength(2);
  await act(async () => {
    pending.resolve({ default: Counter });
    await pending.promise;
  });
  fireEvent.click(screen.getByRole('button', { name: 'A:0' }));
  expect(screen.getByRole('button', { name: 'A:1' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'B:0' })).toBeVisible();
});

it('reveals a resolved feature with timers frozen, without advancing the advisor clock', async () => {
  vi.useFakeTimers();
  const pending = deferred<{ default: () => React.ReactNode }>();
  const Feature = deferredFeature(() => pending.promise);
  render(<Feature />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading…');
  await act(async () => {
    pending.resolve({ default: () => <p>Ready while paused</p> });
    await pending.promise;
  });
  expect(screen.getByText('Ready while paused')).toBeVisible();
});

it('retries an import failure and displays accessible German feedback', async () => {
  setLanguage('de', false);
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error('network failed'))
    .mockResolvedValueOnce({ default: () => <p>Recovered feature</p> });
  const Feature = deferredFeature(load);
  render(<Feature />);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Diese Seite konnte nicht geladen werden.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }));
  expect(await screen.findByText('Recovered feature')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(2);
});

it('catches a feature render failure and allows remounting after recovery', async () => {
  let broken = true;
  function FeatureBody() {
    if (broken) throw new Error('render failed');
    return <p>Recovered render</p>;
  }
  const reported = vi.spyOn(console, 'error').mockImplementation(() => {});
  const Feature = deferredFeature(async () => ({ default: FeatureBody }));
  render(<Feature />);
  expect(await screen.findByRole('alert')).toHaveTextContent('This page could not be loaded.');
  expect(reported).toHaveBeenCalled();
  broken = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('Recovered render')).toBeVisible();
});

it('never mounts an obsolete account after an import finishes and remounts private feature state per account', async () => {
  const mounted = vi.fn();
  function PrivateFeature({ account }: { account: string }) {
    const [remembered] = useState(account);
    useEffect(() => {
      mounted(account);
    }, [account]);
    return <p>Private state for {remembered}</p>;
  }
  const pending = deferred<{ default: typeof PrivateFeature }>();
  const Feature = deferredFeature(() => pending.promise);
  const view = render(<Feature key="A" account="A" />);
  view.rerender(<Feature key="B" account="B" />);
  await act(async () => {
    pending.resolve({ default: PrivateFeature });
    await pending.promise;
  });
  expect(screen.getByText('Private state for B')).toBeVisible();
  expect(mounted).toHaveBeenCalledTimes(1);
  expect(mounted).toHaveBeenCalledWith('B');
  view.rerender(<Feature key="C" account="C" />);
  expect(screen.getByText('Private state for C')).toBeVisible();
  expect(screen.queryByText('Private state for B')).toBeNull();
});
