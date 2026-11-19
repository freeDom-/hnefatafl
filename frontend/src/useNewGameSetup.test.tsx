import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useNewGameSetup } from './useNewGameSetup';

type Deferred = {
  promise: Promise<boolean>;
  resolve: (value: boolean) => void;
  reject: (reason: unknown) => void;
};

const deferred = (): Deferred => {
  let resolve!: (value: boolean) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<boolean>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
};

let setup: ReturnType<typeof useNewGameSetup>;

function Probe() {
  setup = useNewGameSetup();
  return <p>{setup.open ? 'open' : 'closed'}</p>;
}

const settle = async (value: Deferred, commit = true) =>
  act(async () => {
    value.resolve(commit);
    await value.promise;
  });

afterEach(cleanup);

it('lets only the newest overlapping operation open the dialog', async () => {
  const first = deferred();
  const second = deferred();
  render(<Probe />);
  act(() =>
    setup.start(
      () => first.promise,
      () => {},
    ),
  );
  act(() =>
    setup.start(
      () => second.promise,
      () => {},
    ),
  );
  await settle(second);
  expect(screen.getByText('open')).toBeVisible();
  // The superseded operation resolving later must not open a second dialog or
  // replace the options the newest operation already published.
  await settle(first);
  expect(screen.getByText('open')).toBeVisible();
  expect(screen.queryByText('closed')).toBeNull();
});

it('discards a late response after cancellation and can reopen explicitly', async () => {
  const late = deferred();
  render(<Probe />);
  act(() =>
    setup.start(
      () => late.promise,
      () => {},
    ),
  );
  act(() => setup.cancel());
  await settle(late);
  expect(screen.getByText('closed')).toBeVisible();
  const reopened = deferred();
  act(() =>
    setup.start(
      () => reopened.promise,
      () => {},
    ),
  );
  await settle(reopened);
  expect(screen.getByText('open')).toBeVisible();
});

it('does not report a failure from a superseded or aborted operation', async () => {
  const first = deferred();
  const second = deferred();
  const errors: unknown[] = [];
  render(<Probe />);
  act(() =>
    setup.start(
      () => first.promise,
      (error) => errors.push(error),
    ),
  );
  act(() =>
    setup.start(
      () => second.promise,
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

it('only owns the dialog until it is cancelled', async () => {
  const operation = deferred();
  render(<Probe />);
  expect(setup.owns()).toBe(false);
  act(() =>
    setup.start(
      () => operation.promise,
      () => {},
    ),
  );
  await settle(operation);
  expect(setup.owns()).toBe(true);
  act(() => setup.cancel());
  expect(setup.owns()).toBe(false);
  // The load result was discarded; a completion from the cancelled dialog must
  // not be treated as current.
  expect(screen.getByText('closed')).toBeVisible();
});

it('treats a load that declines to commit as cancelled', async () => {
  const declined = deferred();
  render(<Probe />);
  act(() =>
    setup.start(
      () => declined.promise,
      () => {},
    ),
  );
  await settle(declined, false);
  expect(screen.getByText('closed')).toBeVisible();
  expect(setup.owns()).toBe(false);
});
