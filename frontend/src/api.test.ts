import { afterEach, expect, it, vi } from 'vitest';
import { api, ApiError, setCSRF } from './api';
afterEach(() => {
  vi.unstubAllGlobals();
  setCSRF('');
});
it('uses the current in-memory CSRF and exposes typed online quota errors', async () => {
  setCSRF('memory-token');
  const fetcher = vi.fn().mockResolvedValue({
    ok: false,
    status: 429,
    json: async () => ({ detail: 'Quota reached.' }),
  });
  vi.stubGlobal('fetch', fetcher);
  await expect(api('/online/games', { request_id: 'fixture' })).rejects.toMatchObject({
    status: 429,
    message: 'Quota reached.',
    name: 'ApiError',
  });
  expect(fetcher.mock.calls[0][1].headers['X-CSRF-Token']).toBe('memory-token');
  expect(new ApiError(503, 'Unavailable')).toBeInstanceOf(Error);
});
it('ignores a delayed unauthorized response from an aborted account request', async () => {
  const controller = new AbortController();
  const expired = vi.fn();
  window.addEventListener('authentication-required', expired);
  let finish!: (response: unknown) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const request = api('/online/games', undefined, undefined, controller.signal);
  controller.abort();
  finish({ ok: false, status: 401, json: async () => ({ detail: 'Expired' }) });
  await expect(request).rejects.toMatchObject({ name: 'AbortError' });
  expect(expired).not.toHaveBeenCalled();
  window.removeEventListener('authentication-required', expired);
});

it('aborts and ignores an old account response after leaving the protected surface', async () => {
  setCSRF('old-session');
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn((_path: string, options?: RequestInit) => {
    void options;
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  vi.stubGlobal('fetch', fetcher);
  const expired = vi.fn();
  window.addEventListener('authentication-required', expired);
  const pending = api('/games/old-account');
  const signal = fetcher.mock.calls[0][1]?.signal;
  setCSRF('');
  setCSRF('new-session');
  expect(signal?.aborted).toBe(true);
  finish({ ok: false, status: 401, json: async () => ({ detail: 'old account' }) });
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  expect(expired).not.toHaveBeenCalled();
  window.removeEventListener('authentication-required', expired);
});

it('rejects old account data when a response body finishes after the account changes', async () => {
  setCSRF('old-session');
  let finish!: (value: unknown) => void;
  let started!: () => void;
  const decoding = new Promise<void>((resolve) => {
    started = resolve;
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: () => {
        started();
        return new Promise((resolve) => {
          finish = resolve;
        });
      },
    })),
  );
  const pending = api('/games/old-account');
  await decoding;
  setCSRF('new-session');
  finish({ private_game: 'old-account' });
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
});
