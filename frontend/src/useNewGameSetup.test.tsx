import { act, cleanup, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, expect, it } from 'vitest';
import { useNewGameSetup } from './useNewGameSetup';

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

let setup: ReturnType<typeof useNewGameSetup>;

function Probe() {
  setup = useNewGameSetup();
  return <p>{setup.open ? 'open' : 'closed'}</p>;
}

const settle = async <T,>(value: Deferred<T>, result: T) =>
  act(async () => {
    value.resolve(result);
    await value.promise;
  });

afterEach(cleanup);

it('lets only the newest overlapping operation publish and open the dialog', async () => {
  const first = deferred<{ id: string }>();
  const second = deferred<{ id: string }>();
  const commits: string[] = [];
  render(<Probe />);
  act(() =>
    setup.start(
      () => first.promise,
      (value) => commits.push(value.id),
      () => {},
    ),
  );
  act(() =>
    setup.start(
      () => second.promise,
      (value) => commits.push(value.id),
      () => {},
    ),
  );
  await settle(second, { id: 'second' });
  expect(screen.getByText('open')).toBeVisible();
  expect(commits).toEqual(['second']);
  // The superseded operation resolving later must not publish stale options or
  // open a second dialog.
  await settle(first, { id: 'first' });
  expect(commits).toEqual(['second']);
  expect(screen.getByText('open')).toBeVisible();
  expect(screen.queryByText('closed')).toBeNull();
});

it('discards a late response after cancellation and can reopen explicitly', async () => {
  const late = deferred<{ id: string }>();
  render(<Probe />);
  act(() =>
    setup.start(
      () => late.promise,
      () => {},
      () => {},
    ),
  );
  act(() => setup.cancel());
  await settle(late, { id: 'late' });
  expect(screen.getByText('closed')).toBeVisible();
  const reopened = deferred<{ id: string }>();
  act(() =>
    setup.start(
      () => reopened.promise,
      () => {},
      () => {},
    ),
  );
  await settle(reopened, { id: 'reopened' });
  expect(screen.getByText('open')).toBeVisible();
});

it('does not report a failure from a superseded or aborted operation', async () => {
  const first = deferred<{ id: string }>();
  const second = deferred<{ id: string }>();
  const errors: unknown[] = [];
  render(<Probe />);
  act(() =>
    setup.start(
      () => first.promise,
      () => {},
      (error) => errors.push(error),
    ),
  );
  act(() =>
    setup.start(
      () => second.promise,
      () => {},
      (error) => errors.push(error),
    ),
  );
  await act(async () => {
    first.reject(new Error('stale'));
    await first.promise.catch(() => {});
  });
  expect(errors).toEqual([]);
  await act(async () => {
    second.reject(new Error('current'));
    await second.promise.catch(() => {});
  });
  expect(errors).toHaveLength(1);
});

it('owns only the specific session that opened the dialog', async () => {
  const first = deferred<{ id: string }>();
  const second = deferred<{ id: string }>();
  render(<Probe />);
  expect(setup.owns(setup.session())).toBe(false);
  act(() =>
    setup.start(
      () => first.promise,
      () => {},
      () => {},
    ),
  );
  await settle(first, { id: 'first' });
  const firstSession = setup.session();
  expect(setup.owns(firstSession)).toBe(true);
  // A newer operation takes ownership; the older session can no longer apply a
  // completion even though a dialog is still open.
  act(() =>
    setup.start(
      () => second.promise,
      () => {},
      () => {},
    ),
  );
  await settle(second, { id: 'second' });
  expect(setup.owns(firstSession)).toBe(false);
  expect(setup.owns(setup.session())).toBe(true);
  act(() => setup.cancel());
  expect(setup.owns(setup.session())).toBe(false);
  expect(screen.getByText('closed')).toBeVisible();
});

it('treats a load that declines to commit as cancelled', async () => {
  const declined = deferred<{ id: string } | null>();
  render(<Probe />);
  act(() =>
    setup.start(
      () => declined.promise,
      () => {},
      () => {},
    ),
  );
  await settle(declined, null);
  expect(screen.getByText('closed')).toBeVisible();
  expect(setup.owns(setup.session())).toBe(false);
});

it('invalidates and aborts outstanding option requests on unmount', async () => {
  const pending = deferred<{ id: string }>();
  const commits: string[] = [];
  const errors: unknown[] = [];
  let signal: AbortSignal | undefined;
  const view = render(<Probe />);
  act(() =>
    setup.start(
      (value) => {
        signal = value;
        return pending.promise;
      },
      (value) => commits.push(value.id),
      (error) => errors.push(error),
    ),
  );
  expect(signal?.aborted).toBe(false);
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => {
    pending.resolve({ id: 'late' });
    await pending.promise;
  });
  expect(commits).toEqual([]);
  expect(errors).toEqual([]);
});

it('does not notify after unmount when a pending request rejects', async () => {
  const pending = deferred<{ id: string }>();
  const errors: unknown[] = [];
  const view = render(<Probe />);
  act(() =>
    setup.start(
      () => pending.promise,
      () => {},
      (error) => errors.push(error),
    ),
  );
  view.unmount();
  await act(async () => {
    pending.reject(new Error('offline'));
    await pending.promise.catch(() => {});
  });
  expect(errors).toEqual([]);
});

it('starts a fresh operation after a StrictMode setup/cleanup/setup cycle', async () => {
  const pending = deferred<{ id: string }>();
  const commits: string[] = [];
  render(
    <StrictMode>
      <Probe />
    </StrictMode>,
  );
  act(() =>
    setup.start(
      () => pending.promise,
      (value) => commits.push(value.id),
      () => {},
    ),
  );
  await settle(pending, { id: 'strict' });
  expect(commits).toEqual(['strict']);
  expect(screen.getByText('open')).toBeVisible();
});
