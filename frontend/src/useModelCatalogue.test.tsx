import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { parseModelCatalogue, useModelCatalogue } from './useModelCatalogue';
import type { Model } from './types';

const hugin: Model = { id: 'a'.repeat(24), name: 'Hugin', ruleset: 'tablut' };
const munin: Model = { id: 'b'.repeat(24), name: 'Munin', ruleset: 'brandubh' };

type Pending = {
  settle: (value: unknown) => void;
  fail: (reason: unknown) => void;
};
const pending: Pending[] = [];
const fetcher = vi.fn(
  (_path: string, init?: RequestInit) =>
    new Promise<Response>((resolve, reject) => {
      const signal = init?.signal;
      pending.push({
        settle: (value) =>
          resolve({ ok: true, status: 200, statusText: '', json: async () => value } as Response),
        fail: reject,
      });
      signal?.addEventListener('abort', () =>
        reject(new DOMException('Request cancelled', 'AbortError')),
      );
    }),
);
/** Start a refresh and hand back its own promise, so completion order is explicit. */
const start = (result: { current: { refresh: (s?: AbortSignal) => Promise<void> } }) => {
  let attempt!: Promise<void>;
  act(() => {
    attempt = result.current.refresh();
  });
  return attempt;
};
const respond = async (index: number, value: unknown, attempt: Promise<void>) => {
  await act(async () => {
    pending[index].settle(value);
    await attempt;
  });
};
const reject = async (index: number, reason: unknown, attempt: Promise<void>) => {
  await act(async () => {
    pending[index].fail(reason);
    await attempt.catch(() => undefined);
  });
};

beforeEach(() => {
  pending.length = 0;
  fetcher.mockClear();
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('publishes a valid catalogue and an empty registry alike', async () => {
  const { result } = renderHook(() => useModelCatalogue());
  expect(result.current.models).toEqual([]);

  const first = start(result);
  await respond(0, [hugin, munin], first);
  expect(result.current.models).toEqual([hugin, munin]);

  const second = start(result);
  await respond(1, [], second);
  expect(result.current.models).toEqual([]);
});

it('lets the newest request win when an older one finishes last', async () => {
  const { result } = renderHook(() => useModelCatalogue());
  const older = start(result);
  const newer = start(result);
  expect(pending).toHaveLength(2);

  await respond(1, [munin], newer);
  expect(result.current.models).toEqual([munin]);

  // The older request cannot overwrite the newer catalogue, aborted or not.
  await respond(0, [hugin], older);
  expect(result.current.models).toEqual([munin]);
});

it('lets a late failure leave the newest catalogue untouched', async () => {
  const { result } = renderHook(() => useModelCatalogue());
  const older = start(result);
  const newer = start(result);

  await respond(1, [munin], newer);
  await reject(0, new Error('Catalogue offline'), older);
  expect(result.current.models).toEqual([munin]);
});

it('keeps the last valid catalogue when the newest request fails', async () => {
  const { result } = renderHook(() => useModelCatalogue());
  const first = start(result);
  await respond(0, [hugin], first);

  const second = start(result);
  await reject(1, new Error('Catalogue offline'), second);
  expect(result.current.models).toEqual([hugin]);
});

it('never publishes an aborted request', async () => {
  const { result } = renderHook(() => useModelCatalogue());
  const controller = new AbortController();
  let attempt!: Promise<void>;
  act(() => {
    attempt = result.current.refresh(controller.signal);
  });
  act(() => {
    controller.abort();
  });
  await act(async () => {
    await attempt.catch(() => undefined);
  });
  expect(result.current.models).toEqual([]);

  // The catalogue is still usable afterwards.
  const next = start(result);
  await respond(1, [hugin], next);
  expect(result.current.models).toEqual([hugin]);
});

it('rejects a malformed payload and preserves the catalogue', async () => {
  const { result } = renderHook(() => useModelCatalogue());
  const first = start(result);
  await respond(0, [hugin], first);

  const second = start(result);
  await act(async () => {
    pending[1].settle({ models: [] });
    await expect(second).rejects.toThrow('unreadable model catalogue');
  });
  expect(result.current.models).toEqual([hugin]);

  // A retry after a malformed response publishes the repaired catalogue.
  const third = start(result);
  await respond(2, [munin], third);
  expect(result.current.models).toEqual([munin]);
});

it.each([
  ['an object', { id: hugin.id }],
  ['null', null],
  ['a string', 'models'],
  ['an entry without model fields', [{ id: hugin.id }]],
  ['a non-object entry', [1]],
])('rejects %s as a catalogue', async (_label, payload) => {
  const { result } = renderHook(() => useModelCatalogue());
  const first = start(result);
  await respond(0, [hugin], first);

  const second = start(result);
  await act(async () => {
    pending[1].settle(payload);
    await expect(second).rejects.toThrow('unreadable model catalogue');
  });
  expect(result.current.models).toEqual([hugin]);
});

it('keeps sequential requests working', async () => {
  const { result } = renderHook(() => useModelCatalogue());
  const first = start(result);
  await respond(0, [hugin], first);
  const second = start(result);
  await respond(1, [hugin, munin], second);
  expect(result.current.models).toEqual([hugin, munin]);
});

it('accepts only a list of registry entries', () => {
  expect(parseModelCatalogue([hugin])).toEqual([hugin]);
  expect(parseModelCatalogue([])).toEqual([]);
  for (const payload of [null, {}, [{ name: 'Hugin' }], [null]])
    expect(() => parseModelCatalogue(payload)).toThrow('unreadable model catalogue');
});
