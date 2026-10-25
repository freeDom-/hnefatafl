import { afterEach, expect, it, vi } from 'vitest';
import { GuestEngine } from './guestEngine';
class TestWorker {
  static instances: TestWorker[] = [];
  onmessage?: (event: { data: unknown }) => void;
  onerror?: () => void;
  terminate = vi.fn();
  postMessage = vi.fn();
  constructor(public url: string) {
    TestWorker.instances.push(this);
  }
}
const manifest = { schema: 1, base: '/assets/guest-' + 'a'.repeat(64) + '/' };
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  TestWorker.instances = [];
});
async function started() {
  vi.stubGlobal('Worker', TestWorker);
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(manifest))),
  );
  const runtime = new GuestEngine();
  await runtime.start();
  return runtime;
}
it('uses static assets without credentials and disposal rejects work and terminates the worker', async () => {
  const runtime = await started();
  expect(fetch).toHaveBeenCalledWith(
    '/assets/guest-manifest.json',
    expect.objectContaining({ credentials: 'omit', cache: 'no-store' }),
  );
  const pending = runtime.request({ action: 'info' });
  const rejection = expect(pending).rejects.toThrow('cancelled');
  runtime.dispose();
  await rejection;
  expect(TestWorker.instances[0].terminate).toHaveBeenCalledOnce();
  expect(runtime.ready).toBe(false);
  await expect(runtime.request({ action: 'info' })).rejects.toThrow('unavailable');
});
it('rejects concurrent work, ignores wrong/late IDs and never lets a timeout continue computing', async () => {
  const runtime = await started();
  vi.useFakeTimers();
  const first = runtime.request({ action: 'info' }, 25);
  const rejected = expect(first).rejects.toThrow('timed out');
  await expect(runtime.request({ action: 'computer' })).rejects.toThrow('busy');
  TestWorker.instances[0].onmessage?.({ data: { id: 999, value: {} } });
  await vi.advanceTimersByTimeAsync(25);
  await rejected;
  TestWorker.instances[0].onmessage?.({ data: { id: 1, value: {} } });
  expect(TestWorker.instances[0].terminate).toHaveBeenCalledOnce();
});
it('does not start a worker from a manifest arriving after navigation/disposal', async () => {
  vi.stubGlobal('Worker', TestWorker);
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const runtime = new GuestEngine();
  const starting = runtime.start();
  const rejection = expect(starting).rejects.toThrow('unavailable');
  runtime.dispose();
  finish(new Response(JSON.stringify(manifest)));
  await rejection;
  expect(TestWorker.instances).toHaveLength(0);
});
it('refuses external or unversioned runtime locations', async () => {
  vi.stubGlobal('Worker', TestWorker);
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ ...manifest, base: 'https://example.test/worker/' })),
    ),
  );
  await expect(new GuestEngine().start()).rejects.toThrow('unavailable');
  expect(TestWorker.instances).toHaveLength(0);
});
