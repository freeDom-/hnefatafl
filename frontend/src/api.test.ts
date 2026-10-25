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
