import { describe, expect, it, vi } from 'vitest';

import { BrowserAnalysisError } from './contracts';
import { DownloadManager, type DownloadSpec } from './downloads';
import { ArtifactCache, MemoryArtifactStore } from './storage';
import { bytesFor, digest, streamResponse } from './testSupport';

function spec(contents: string, overrides: Partial<DownloadSpec> = {}): DownloadSpec {
  const { sha256, size } = bytesFor(contents);
  return {
    key: `model:${sha256}`,
    url: '/api/browser-analysis/models/x/artifact',
    expectedBytes: size,
    sha256,
    ...overrides,
  };
}

function manager(
  fetchImpl: (url: string, init?: RequestInit) => Promise<Response>,
  overrides: {
    cache?: ArtifactCache;
    retries?: number;
    maxAggregateBytes?: number;
    timeoutMs?: number;
    maxConcurrent?: number;
  } = {},
) {
  return new DownloadManager({
    fetchImpl,
    digestFn: digest,
    retries: overrides.retries ?? 0,
    timeoutMs: overrides.timeoutMs ?? 500,
    maxAggregateBytes: overrides.maxAggregateBytes,
    maxConcurrent: overrides.maxConcurrent,
    cache: overrides.cache,
  });
}

describe('download manager', () => {
  it('streams, reports progress and verifies size and digest', async () => {
    const request = spec('downloaded model payload');
    const fetched: string[] = [];
    const downloads = manager(async (url) => {
      fetched.push(url);
      return streamResponse(['downloaded ', 'model payload']);
    });
    const progress: number[] = [];
    const result = await downloads.download(request, {
      onProgress: (event) => progress.push(event.transferredBytes),
    });
    expect(new TextDecoder().decode(result.bytes)).toBe('downloaded model payload');
    expect(result.fromCache).toBe(false);
    expect(fetched).toEqual([request.url]);
    expect(progress.at(-1)).toBe(request.expectedBytes);
  });

  it('rejects a server-declared size that does not match the manifest', async () => {
    const request = spec('payload');
    const downloads = manager(async () =>
      streamResponse(['payload'], { contentLength: request.expectedBytes + 1 }),
    );
    await expect(downloads.download(request)).rejects.toMatchObject({ code: 'size-mismatch' });
  });

  it('rejects truncated and oversized bodies', async () => {
    const request = spec('a complete artifact');
    const truncated = manager(async () => streamResponse(['a complete']));
    await expect(truncated.download(request)).rejects.toMatchObject({ code: 'size-mismatch' });
    const oversized = manager(async () =>
      streamResponse(['a complete artifact', ' plus extra bytes']),
    );
    await expect(oversized.download(request)).rejects.toMatchObject({ code: 'size-mismatch' });
  });

  it('rejects a digest mismatch and never stores the artifact', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const request = spec('expected payload');
    const downloads = manager(async () => streamResponse(['exchanged payloa']), { cache });
    await expect(downloads.download(request)).rejects.toMatchObject({ code: 'digest-mismatch' });
    expect(await store.list()).toHaveLength(0);
  });

  it('deduplicates concurrent requests for the same immutable artifact', async () => {
    const request = spec('shared payload');
    let calls = 0;
    const downloads = manager(async () => {
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      return streamResponse(['shared payload']);
    });
    const [first, second] = await Promise.all([
      downloads.download(request),
      downloads.download(request),
    ]);
    expect(calls).toBe(1);
    expect(first.bytes.byteLength).toBe(second.bytes.byteLength);
    expect(downloads.activeCount).toBe(0);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('serves a verified artifact from the cache without a network request', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const request = spec('cached payload');
    let calls = 0;
    const first = manager(
      async () => {
        calls += 1;
        return streamResponse(['cached payload']);
      },
      { cache },
    );
    await first.download(request);
    const second = manager(
      async () => {
        calls += 1;
        return streamResponse(['should not be used']);
      },
      { cache },
    );
    const result = await second.download(request);
    expect(result.fromCache).toBe(true);
    expect(calls).toBe(1);
  });

  it('cancels an individual caller without activating partial data', async () => {
    const request = spec('payload');
    const controller = new AbortController();
    const downloads = manager(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    );
    const pending = downloads.download(request, { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: 'download-aborted' });
    downloads.cancelAll();
  });

  it('cancelAll aborts all transfers and prevents stale activation', async () => {
    const request = spec('payload');
    const downloads = manager(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return streamResponse(['payload']);
    });
    const pending = downloads.download(request);
    downloads.cancelAll('logout');
    await expect(pending).rejects.toMatchObject({ code: 'download-aborted' });
    expect(downloads.activeCount).toBe(0);
  });

  it('bounds aggregate transfer bytes', async () => {
    const first = spec('a'.repeat(60));
    const second = spec('b'.repeat(60), { key: 'other' });
    const downloads = manager(
      async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
        return streamResponse(['a'.repeat(60)]);
      },
      { maxAggregateBytes: 100 },
    );
    const pending = downloads.download(first);
    await expect(downloads.download(second)).rejects.toMatchObject({ code: 'quota-exceeded' });
    downloads.cancelAll();
    await expect(pending).rejects.toBeInstanceOf(BrowserAnalysisError);
  });

  it('classifies authorization failures without retrying', async () => {
    const request = spec('payload');
    const fetchImpl = vi.fn(async () => streamResponse([], { status: 401 }));
    const downloads = manager(fetchImpl, { retries: 3 });
    await expect(downloads.download(request)).rejects.toMatchObject({ code: 'unauthorized' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('retries a bounded number of transport failures', async () => {
    const request = spec('payload');
    let calls = 0;
    const downloads = manager(
      async () => {
        calls += 1;
        if (calls === 1) throw new TypeError('network down');
        return streamResponse(['payload']);
      },
      { retries: 1 },
    );
    const result = await downloads.download(request);
    expect(calls).toBe(2);
    expect(result.sha256).toBe(request.sha256);
  });

  it('rejects an invalid download specification before any request', async () => {
    const fetchImpl = vi.fn(async () => streamResponse(['payload']));
    const downloads = manager(fetchImpl);
    await expect(
      downloads.download({ key: 'k', url: '/x', expectedBytes: 10, sha256: 'nope' }),
    ).rejects.toMatchObject({ code: 'digest-mismatch' });
    await expect(
      downloads.download({ key: 'k', url: '/x', expectedBytes: -1, sha256: 'a'.repeat(64) }),
    ).rejects.toMatchObject({ code: 'size-mismatch' });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

function controlledFetch() {
  const pending: Array<{
    url: string;
    signal?: AbortSignal;
    resolve: (response: Response) => void;
    reject: (error: unknown) => void;
  }> = [];
  let active = 0;
  let peak = 0;
  const fetchImpl = (url: string, init?: RequestInit) =>
    new Promise<Response>((resolve, reject) => {
      active += 1;
      peak = Math.max(peak, active);
      const entry = {
        url,
        signal: init?.signal ?? undefined,
        resolve: (response: Response) => {
          active -= 1;
          resolve(response);
        },
        reject: (error: unknown) => {
          active -= 1;
          reject(error);
        },
      };
      pending.push(entry);
      init?.signal?.addEventListener('abort', () =>
        entry.reject(new DOMException('aborted', 'AbortError')),
      );
    });
  return {
    fetchImpl,
    pending,
    get active() {
      return active;
    },
    get peak() {
      return peak;
    },
  };
}

describe('download manager — generation-safe reservations and subscribers', () => {
  it('does not let cancelled operations free slots owned by newer work', async () => {
    const remote = controlledFetch();
    const downloads = manager(remote.fetchImpl, { maxAggregateBytes: 10_000 });
    const first = downloads.download(spec('a'.repeat(8)));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    downloads.cancelAll();
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });

    const second = downloads.download(spec('b'.repeat(8), { key: 'b' }));
    const third = downloads.download(spec('c'.repeat(8), { key: 'c' }));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(3));
    expect(remote.active).toBe(2); // configured concurrency, old slot not reused
    remote.pending[1].resolve(streamResponse(['b'.repeat(8)]));
    remote.pending[2].resolve(streamResponse(['c'.repeat(8)]));
    await expect(second).resolves.toMatchObject({ key: 'b' });
    await expect(third).resolves.toMatchObject({ key: 'c' });
    expect(downloads.activeCount).toBe(0);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('keeps aggregate-byte accounting across cancellation and replacement', async () => {
    const remote = controlledFetch();
    const downloads = manager(remote.fetchImpl, { maxAggregateBytes: 20 });
    const first = downloads.download(spec('a'.repeat(10)));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    downloads.cancelAll();
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });
    expect(downloads.outstandingBytes).toBe(0);
    const replacement = downloads.download(spec('b'.repeat(10), { key: 'b' }));
    await vi.waitFor(() => expect(downloads.outstandingBytes).toBe(10));
    remote.pending.at(-1)!.resolve(streamResponse(['b'.repeat(10)]));
    await expect(replacement).resolves.toMatchObject({ key: 'b' });
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('rejects already-aborted signals before any request', async () => {
    const fetchImpl = vi.fn(async () => streamResponse(['payload']));
    const downloads = manager(fetchImpl);
    const controller = new AbortController();
    controller.abort();
    await expect(
      downloads.download(spec('payload'), { signal: controller.signal }),
    ).rejects.toMatchObject({ code: 'download-aborted' });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('does not count cancelled queued operations as active transfers', async () => {
    const remote = controlledFetch();
    const downloads = manager(remote.fetchImpl, { maxAggregateBytes: 10_000, maxConcurrent: 1 });
    const first = downloads.download(spec('a'.repeat(8)));
    const queued = downloads.download(spec('b'.repeat(8), { key: 'b' }));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    expect(remote.active).toBe(1);
    downloads.cancelAll();
    await expect(queued).rejects.toMatchObject({ code: 'download-aborted' });
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });
    expect(remote.pending).toHaveLength(1); // queued request never started
    expect(downloads.activeCount).toBe(0);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('shares one transfer between subscribers; cancelling one keeps the other', async () => {
    const remote = controlledFetch();
    const downloads = manager(remote.fetchImpl);
    const request = spec('shared payload');
    const firstProgress: number[] = [];
    const secondProgress: number[] = [];
    const controller = new AbortController();
    const first = downloads.download(request, {
      signal: controller.signal,
      onProgress: (event) => firstProgress.push(event.transferredBytes),
    });
    const second = downloads.download(request, {
      onProgress: (event) => secondProgress.push(event.transferredBytes),
    });
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    controller.abort();
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });
    remote.pending[0].resolve(streamResponse(['shared ', 'payload']));
    await expect(second).resolves.toMatchObject({ key: request.key });
    expect(firstProgress).toEqual([]);
    expect(secondProgress.at(-1)).toBe(request.expectedBytes);
    expect(downloads.activeCount).toBe(0);
  });

  it('aborts the underlying transfer when the last subscriber cancels', async () => {
    const remote = controlledFetch();
    const cache = new ArtifactCache(new MemoryArtifactStore());
    const downloads = manager(remote.fetchImpl, { cache });
    const request = spec('lonely payload');
    const controller = new AbortController();
    const pending = downloads.download(request, { signal: controller.signal });
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: 'download-aborted' });
    await vi.waitFor(() => expect(remote.pending[0].signal?.aborted).toBe(true));
    expect(await cache.totalBytes()).toBe(0);
  });

  it('discards a cancelled download after hashing completes', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const request = spec('hash payload');
    let releaseDigest!: () => void;
    const digestGate = new Promise<void>((resolve) => {
      releaseDigest = resolve;
    });
    const downloads = new DownloadManager({
      fetchImpl: async () => streamResponse(['hash payload']),
      digestFn: async (bytes) => {
        await digestGate;
        return digest(bytes);
      },
      cache,
    });
    const controller = new AbortController();
    const pending = downloads.download(request, { signal: controller.signal });
    await vi.waitFor(() => expect(store.list()).resolves.toHaveLength(0));
    controller.abort();
    releaseDigest();
    await expect(pending).rejects.toMatchObject({ code: 'download-aborted' });
    expect(await cache.totalBytes()).toBe(0);
  });

  it('rejects conflicting specifications that share one deduplication key', async () => {
    const remote = controlledFetch();
    const downloads = manager(remote.fetchImpl);
    const request = spec('payload');
    const pending = downloads.download(request);
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    await expect(
      downloads.download({ ...request, expectedBytes: request.expectedBytes + 1 }),
    ).rejects.toMatchObject({ code: 'download-failed' });
    downloads.cancelAll();
    await expect(pending).rejects.toBeDefined();
  });

  it('enforces the exact concurrency limit and releases every reservation', async () => {
    const remote = controlledFetch();
    const downloads = manager(remote.fetchImpl, { maxAggregateBytes: 10_000 });
    const requests = Array.from({ length: 5 }, (_, index) =>
      downloads.download(spec(`${index}`.repeat(8), { key: `k${index}` })),
    );
    await vi.waitFor(() => expect(remote.pending).toHaveLength(2));
    expect(remote.peak).toBe(2);
    for (let index = 0; index < 5; index += 1) {
      await vi.waitFor(() => expect(remote.pending.length).toBeGreaterThan(index));
      remote.pending[index].resolve(streamResponse([`${index}`.repeat(8)]));
    }
    await Promise.all(requests);
    expect(remote.peak).toBe(2);
    expect(downloads.activeCount).toBe(0);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('supports repeated cancelAll followed by immediate restart without leaks', async () => {
    const remote = controlledFetch();
    const downloads = manager(remote.fetchImpl, { maxAggregateBytes: 10_000 });
    for (let round = 0; round < 5; round += 1) {
      const pending = downloads.download(spec(`${round}`.repeat(8), { key: `r${round}` }));
      await vi.waitFor(() => expect(downloads.activeCount).toBe(1));
      downloads.cancelAll();
      await expect(pending).rejects.toMatchObject({ code: 'download-aborted' });
      expect(downloads.activeCount).toBe(0);
      expect(downloads.outstandingBytes).toBe(0);
    }
    const final = downloads.download(spec('final'.repeat(2), { key: 'final' }));
    await vi.waitFor(() => expect(downloads.activeCount).toBe(1));
    remote.pending.at(-1)!.resolve(streamResponse(['finalfinal']));
    await expect(final).resolves.toMatchObject({ key: 'final' });
  });
});

function stubbornFetch() {
  const pending: Array<{
    url: string;
    resolve: (response: Response) => void;
    reject: (error: unknown) => void;
  }> = [];
  let active = 0;
  let peak = 0;
  const fetchImpl = (url: string) =>
    new Promise<Response>((resolve, reject) => {
      active += 1;
      peak = Math.max(peak, active);
      pending.push({
        url,
        resolve: (response: Response) => {
          active -= 1;
          resolve(response);
        },
        reject: (error: unknown) => {
          active -= 1;
          reject(error);
        },
      });
    });
  return {
    fetchImpl,
    pending,
    get active() {
      return active;
    },
    get peak() {
      return peak;
    },
  };
}

describe('download manager — actual occupancy across retiring generations', () => {
  it('keeps actual concurrency bounded while cancelled transfers are still retiring', async () => {
    const remote = stubbornFetch();
    const downloads = manager(remote.fetchImpl, { maxAggregateBytes: 10_000, maxConcurrent: 2 });
    const first = downloads.download(spec('a'.repeat(8)));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    downloads.cancelAll();
    await expect(first).rejects.toBeDefined; // the caller observes cancellation first
    const second = downloads.download(spec('b'.repeat(8), { key: 'b' }));
    const third = downloads.download(spec('c'.repeat(8), { key: 'c' }));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(2));
    // First transfer still occupies a real slot; only one replacement started.
    expect(remote.active).toBe(2);
    expect(downloads.activeCount).toBe(2);
    remote.pending[0].resolve(streamResponse(['a'.repeat(8)]));
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });
    await vi.waitFor(() => expect(remote.pending).toHaveLength(3));
    remote.pending[1].resolve(streamResponse(['b'.repeat(8)]));
    remote.pending[2].resolve(streamResponse(['c'.repeat(8)]));
    await expect(second).resolves.toMatchObject({ key: 'b' });
    await expect(third).resolves.toMatchObject({ key: 'c' });
    expect(remote.peak).toBe(2);
    expect(downloads.activeCount).toBe(0);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('keeps byte reservations until retiring work settles', async () => {
    const remote = stubbornFetch();
    const downloads = manager(remote.fetchImpl, {
      maxAggregateBytes: 15,
      maxConcurrent: 2,
    });
    const first = downloads.download(spec('a'.repeat(10)));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    expect(downloads.outstandingBytes).toBe(10);
    downloads.cancelAll();
    const replacement = downloads.download(spec('b'.repeat(10), { key: 'b' }));
    await expect(replacement).rejects.toMatchObject({ code: 'quota-exceeded' });
    expect(downloads.outstandingBytes).toBe(10); // old reservation retained
    remote.pending[0].resolve(streamResponse(['a'.repeat(10)]));
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });
    await vi.waitFor(() => expect(downloads.outstandingBytes).toBe(0));
    const retried = downloads.download(spec('b'.repeat(10), { key: 'b' }));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(2));
    remote.pending[1].resolve(streamResponse(['b'.repeat(10)]));
    await expect(retried).resolves.toMatchObject({ key: 'b' });
  });

  it('captures ownership before cache lookup and does not start work after cancelAll', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const original = cache.getVerified.bind(cache);
    cache.getVerified = async (...args) => {
      await gate;
      return original(...args);
    };
    const fetchImpl = vi.fn(async () => streamResponse(['payload']));
    const downloads = manager(fetchImpl, { cache });
    const pendingDownload = downloads.download(spec('payload'));
    downloads.cancelAll();
    release();
    await expect(pendingDownload).rejects.toMatchObject({ code: 'download-aborted' });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(downloads.activeCount).toBe(0);
  });

  it('removes a queued last subscriber promptly', async () => {
    const remote = stubbornFetch();
    const downloads = manager(remote.fetchImpl, { maxConcurrent: 1 });
    const first = downloads.download(spec('a'.repeat(8)));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    const controller = new AbortController();
    const queued = downloads.download(spec('b'.repeat(8), { key: 'b' }), {
      signal: controller.signal,
    });
    controller.abort();
    await expect(queued).rejects.toMatchObject({ code: 'download-aborted' });
    remote.pending[0].resolve(streamResponse(['a'.repeat(8)]));
    await expect(first).resolves.toHaveProperty('bytes');
    expect(remote.pending).toHaveLength(1);
    expect(downloads.activeCount).toBe(0);
  });

  it('starts a same-key replacement after cancellation without deleting its cache', async () => {
    const remote = stubbornFetch();
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const downloads = manager(remote.fetchImpl, { cache, maxConcurrent: 2 });
    const key = `model:${bytesFor('replacement payload').sha256}`;
    const old = downloads.download(spec('replacement payload', { key }));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(1));
    downloads.cancelAll();
    const replacement = downloads.download(spec('replacement payload', { key }));
    await vi.waitFor(() => expect(remote.pending).toHaveLength(2));
    remote.pending[1].resolve(streamResponse(['replacement payload']));
    await expect(replacement).resolves.toMatchObject({ key });
    remote.pending[0].resolve(streamResponse(['replacement payload']));
    await expect(old).rejects.toMatchObject({ code: 'download-aborted' });
    expect(
      await cache.getVerified(key, bytesFor('replacement payload').sha256, digest),
    ).not.toBeNull();
  });
});

function streamWithCancel(
  chunks: string[],
  cancel: () => Promise<void>,
  contentLength?: number,
): Response {
  const encoded = chunks.map((chunk) => new TextEncoder().encode(chunk));
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of encoded) controller.enqueue(chunk);
      controller.close();
    },
    cancel,
  });
  const headers = new Headers();
  headers.set(
    'content-length',
    String(contentLength ?? encoded.reduce((sum, chunk) => sum + chunk.byteLength, 0)),
  );
  return { ok: true, status: 200, headers, body: stream } as unknown as Response;
}

describe('download manager — bounded caller outcomes (H3)', () => {
  it('bounds a delayed cache lookup and never starts a transfer afterwards', async () => {
    const gate = new Promise<void>((resolve) => setTimeout(resolve, 0));
    let release!: () => void;
    const lookupGate = new Promise<void>((resolve) => {
      release = resolve;
    });
    void gate;
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const original = cache.getVerified.bind(cache);
    cache.getVerified = async (...args) => {
      await lookupGate;
      return original(...args);
    };
    const fetchImpl = vi.fn(async () => streamResponse(['payload']));
    const downloads = manager(fetchImpl, { cache, timeoutMs: 50 });
    const pending = downloads.download(spec('payload'));
    await expect(pending).rejects.toMatchObject({ code: 'download-timeout' });
    release();
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(downloads.activeCount).toBe(0);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('returns a timeout while retaining accounting until delayed hashing settles', async () => {
    const request = spec('hash deadline payload');
    let releaseDigest!: () => void;
    const digestGate = new Promise<void>((resolve) => {
      releaseDigest = resolve;
    });
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const downloads = new DownloadManager({
      fetchImpl: async () => streamResponse(['hash deadline payload']),
      digestFn: async (bytes) => {
        await digestGate;
        return digest(bytes);
      },
      cache,
      timeoutMs: 60,
      retries: 0,
    });
    const started = Date.now();
    await expect(downloads.download(request)).rejects.toMatchObject({
      code: 'download-timeout',
    });
    expect(Date.now() - started).toBeLessThan(2000);
    expect(downloads.outstandingBytes).toBe(request.expectedBytes);
    releaseDigest();
    await vi.waitFor(() => expect(downloads.outstandingBytes).toBe(0));
    expect(downloads.activeCount).toBe(0);
    expect(await cache.totalBytes()).toBe(0);
    const later = downloads.download(request);
    await expect(later).resolves.toMatchObject({ key: request.key });
  });

  it('does not activate a cache admission that finishes after the caller deadline', async () => {
    const request = spec('slow admission payload');
    let releaseAdmission!: () => void;
    const admissionGate = new Promise<void>((resolve) => {
      releaseAdmission = resolve;
    });
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    let admissionStarted = false;
    const originalAdmit = cache.admit.bind(cache);
    cache.admit = async (...args) => {
      admissionStarted = true;
      await admissionGate;
      return originalAdmit(...args);
    };
    const downloads = manager(async () => streamResponse(['slow admission payload']), {
      cache,
      timeoutMs: 50,
    });
    await expect(downloads.download(request)).rejects.toMatchObject({
      code: 'download-timeout',
    });
    await vi.waitFor(() => expect(admissionStarted).toBe(true));
    releaseAdmission();
    await vi.waitFor(() => expect(downloads.outstandingBytes).toBe(0));
    expect(await cache.totalBytes()).toBe(0);
    expect(downloads.activeCount).toBe(0);
  });

  it('cancels the response body when a declared size is rejected', async () => {
    const cancel = vi.fn(async () => undefined);
    const request = spec('declared size payload');
    const downloads = manager(async () =>
      streamWithCancel(['declared size payload'], cancel, request.expectedBytes + 1),
    );
    await expect(downloads.download(request)).rejects.toMatchObject({ code: 'size-mismatch' });
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it('cancels the reader when a stream is oversized', async () => {
    const cancel = vi.fn(async () => undefined);
    const request = spec('small payload');
    const downloads = manager(async () =>
      streamWithCancel(['small payload', ' plus extra bytes'], cancel),
    );
    await expect(downloads.download(request)).rejects.toMatchObject({ code: 'size-mismatch' });
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it('keeps the original failure when response cancellation itself fails', async () => {
    const request = spec('declared size payload');
    const downloads = manager(async () =>
      streamWithCancel(
        ['declared size payload'],
        async () => {
          throw new Error('cancel failed');
        },
        request.expectedBytes + 1,
      ),
    );
    await expect(downloads.download(request)).rejects.toMatchObject({ code: 'size-mismatch' });
  });

  it('cancels during cache lookup without starting work', async () => {
    let release!: () => void;
    const lookupGate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const cache = new ArtifactCache(new MemoryArtifactStore());
    const original = cache.getVerified.bind(cache);
    cache.getVerified = async (...args) => {
      await lookupGate;
      return original(...args);
    };
    const fetchImpl = vi.fn(async () => streamResponse(['payload']));
    const downloads = manager(fetchImpl, { cache });
    const controller = new AbortController();
    const pending = downloads.download(spec('payload'), { signal: controller.signal });
    controller.abort();
    release();
    await expect(pending).rejects.toMatchObject({ code: 'download-aborted' });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(downloads.activeCount).toBe(0);
  });
});

describe('download manager — tracked cache verification (I1)', () => {
  function gatedCache() {
    const cache = new ArtifactCache(new MemoryArtifactStore());
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const original = cache.getVerified.bind(cache);
    const spy = vi.fn(async (...args: Parameters<typeof original>) => {
      await gate;
      return original(...args);
    });
    cache.getVerified = spy;
    return { cache, spy, release };
  }

  it('keeps pending cache lookup in occupancy after the caller times out', async () => {
    const { cache, release } = gatedCache();
    const fetchImpl = vi.fn(async () => streamResponse(['payload']));
    const downloads = manager(fetchImpl, { cache, timeoutMs: 50 });
    const request = spec('payload');
    await expect(downloads.download(request)).rejects.toMatchObject({
      code: 'download-timeout',
    });
    expect(downloads.occupancy.cacheVerifications).toBe(1);
    const idle = downloads.whenIdle();
    release();
    await idle;
    expect(downloads.occupancy.cacheVerifications).toBe(0);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(downloads.activeCount).toBe(0);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('deduplicates repeated timed-out cache lookups for the same artifact', async () => {
    const { cache, spy, release } = gatedCache();
    const downloads = manager(async () => streamResponse(['payload']), {
      cache,
      timeoutMs: 30,
    });
    const request = spec('payload');
    await expect(downloads.download(request)).rejects.toMatchObject({
      code: 'download-timeout',
    });
    await expect(downloads.download(request)).rejects.toMatchObject({
      code: 'download-timeout',
    });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(downloads.occupancy.cacheVerifications).toBe(1);
    release();
    await downloads.whenIdle();
    expect(downloads.occupancy.cacheVerifications).toBe(0);
  });

  it('does not start a transfer when a late cache result arrives after cancellation', async () => {
    const { cache, release } = gatedCache();
    const fetchImpl = vi.fn(async () => streamResponse(['payload']));
    const downloads = manager(fetchImpl, { cache });
    const controller = new AbortController();
    const pending = downloads.download(spec('payload'), { signal: controller.signal });
    controller.abort();
    release();
    await expect(pending).rejects.toMatchObject({ code: 'download-aborted' });
    await downloads.whenIdle();
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(downloads.occupancy.cacheVerifications).toBe(0);
  });

  it('reports cache verification in drain status until it settles', async () => {
    const { cache, release } = gatedCache();
    const downloads = manager(async () => streamResponse(['payload']), {
      cache,
      timeoutMs: 20,
    });
    await expect(downloads.download(spec('payload'))).rejects.toMatchObject({
      code: 'download-timeout',
    });
    const pendingDrain = downloads.drain(10);
    await expect(pendingDrain).resolves.toMatchObject({
      drained: false,
      cacheVerifications: 1,
    });
    release();
    await downloads.whenIdle();
    expect(downloads.occupancy.cacheVerifications).toBe(0);
  });
});

describe('download manager — cross-generation cache-verification accounting (J1)', () => {
  async function admittedCache(contents: string) {
    const cache = new ArtifactCache(new MemoryArtifactStore());
    const fixture = bytesFor(contents);
    await cache.admit(`model:${fixture.sha256}`, fixture.bytes, { publicationId: 'p' });
    return { cache, fixture };
  }

  function gated(
    cache: ArtifactCache,
    gates: Map<string, Promise<void>>,
  ): ReturnType<typeof vi.fn> {
    const original = cache.getVerified.bind(cache);
    const spy = vi.fn(async (key: string, ...rest: unknown[]) => {
      await gates.get(key);
      return (original as (...args: unknown[]) => Promise<ArrayBuffer | null>)(key, ...rest);
    });
    cache.getVerified = spy as typeof cache.getVerified;
    return spy;
  }

  it('retains generation 1 verification when generation 2 joins the same key', async () => {
    const request = spec('shared cached payload');
    const { cache } = await admittedCache('shared cached payload');
    const gates = new Map<string, Promise<void>>();
    let release!: () => void;
    gates.set(
      request.key,
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );
    const spy = gated(cache, gates);
    const fetchImpl = vi.fn(async () => streamResponse(['unexpected']));
    const downloads = manager(fetchImpl, { cache, timeoutMs: 2_000 });
    const first = downloads.download(request);
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    downloads.cancelAll();
    const second = downloads.download(request);
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 1,
      activeVerifications: 1,
      activeTransfers: 0,
      reservedBytes: request.expectedBytes,
    });
    await expect(downloads.drain(10)).resolves.toMatchObject({
      drained: false,
      cacheVerifications: 1,
      activeVerifications: 1,
    });
    release();
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });
    await expect(second).resolves.toMatchObject({ fromCache: true, retained: true });
    await downloads.whenIdle();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 0,
      activeVerifications: 0,
      activeTransfers: 0,
      reservedBytes: 0,
    });
  });

  it('completes a newer generation artifact while an older verification stays tracked', async () => {
    const older = spec('older cached payload');
    const newer = spec('newer cached payload');
    const cache = new ArtifactCache(new MemoryArtifactStore());
    await cache.admit(older.key, bytesFor('older cached payload').bytes, {
      publicationId: 'p',
    });
    await cache.admit(newer.key, bytesFor('newer cached payload').bytes, {
      publicationId: 'p',
    });
    const gates = new Map<string, Promise<void>>();
    let releaseOlder!: () => void;
    gates.set(
      older.key,
      new Promise<void>((resolve) => {
        releaseOlder = resolve;
      }),
    );
    gates.set(newer.key, Promise.resolve());
    gated(cache, gates);
    const downloads = manager(async () => streamResponse(['unexpected']), {
      cache,
      timeoutMs: 2_000,
    });
    const pendingOlder = downloads.download(older);
    await vi.waitFor(() => expect(downloads.occupancy.activeVerifications).toBe(1));
    downloads.cancelAll();
    await expect(downloads.download(newer)).resolves.toMatchObject({ fromCache: true });
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 1,
      activeVerifications: 1,
      reservedBytes: older.expectedBytes,
    });
    await expect(downloads.drain(10)).resolves.toMatchObject({
      drained: false,
      cacheVerifications: 1,
    });
    releaseOlder();
    await expect(pendingOlder).rejects.toMatchObject({ code: 'download-aborted' });
    await downloads.whenIdle();
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 0,
      activeVerifications: 0,
      reservedBytes: 0,
    });
  });

  it('keeps one verification across repeated cancellation generations', async () => {
    const request = spec('repeated cancellation payload');
    const { cache } = await admittedCache('repeated cancellation payload');
    const gates = new Map<string, Promise<void>>();
    let release!: () => void;
    gates.set(
      request.key,
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );
    const spy = gated(cache, gates);
    const downloads = manager(async () => streamResponse(['unexpected']), {
      cache,
      timeoutMs: 2_000,
    });
    const first = downloads.download(request);
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    downloads.cancelAll();
    downloads.cancelAll();
    const third = downloads.download(request);
    expect(downloads.occupancy.cacheVerifications).toBe(1);
    expect(downloads.occupancy.activeVerifications).toBe(1);
    release();
    await expect(first).rejects.toMatchObject({ code: 'download-aborted' });
    await expect(third).resolves.toMatchObject({ fromCache: true });
    await downloads.whenIdle();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('bounds concurrent verification of distinct artifact keys', async () => {
    const first = spec('first cached payload');
    const second = spec('second cached payload');
    const cache = new ArtifactCache(new MemoryArtifactStore());
    for (const [request, contents] of [
      [first, 'first cached payload'],
      [second, 'second cached payload'],
    ] as const) {
      await cache.admit(request.key, bytesFor(contents).bytes, { publicationId: 'p' });
    }
    const gates = new Map<string, Promise<void>>();
    let releaseFirst!: () => void;
    let releaseSecond!: () => void;
    gates.set(
      first.key,
      new Promise<void>((resolve) => {
        releaseFirst = resolve;
      }),
    );
    gates.set(
      second.key,
      new Promise<void>((resolve) => {
        releaseSecond = resolve;
      }),
    );
    const spy = gated(cache, gates);
    const downloads = manager(async () => streamResponse(['unexpected']), {
      cache,
      maxConcurrent: 1,
      timeoutMs: 2_000,
    });
    const pendingFirst = downloads.download(first);
    const pendingSecond = downloads.download(second);
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 2,
      activeVerifications: 1,
      queued: 1,
      reservedBytes: first.expectedBytes,
    });
    releaseFirst();
    await expect(pendingFirst).resolves.toMatchObject({ fromCache: true });
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(downloads.occupancy).toMatchObject({
      activeVerifications: 1,
      reservedBytes: second.expectedBytes,
    });
    releaseSecond();
    await expect(pendingSecond).resolves.toMatchObject({ fromCache: true });
    await downloads.whenIdle();
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 0,
      activeVerifications: 0,
      queued: 0,
      reservedBytes: 0,
    });
  });

  it('applies the aggregate byte budget to cache verification', async () => {
    const first = spec('budget payload one');
    const second = spec('budget payload two');
    const cache = new ArtifactCache(new MemoryArtifactStore());
    await cache.admit(first.key, bytesFor('budget payload one').bytes, {
      publicationId: 'p',
    });
    await cache.admit(second.key, bytesFor('budget payload two').bytes, {
      publicationId: 'p',
    });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const original = cache.getVerified.bind(cache);
    cache.getVerified = async (key, ...rest) => {
      if (key === first.key) await gate;
      return (original as (...args: unknown[]) => Promise<ArrayBuffer | null>)(key, ...rest);
    };
    const downloads = manager(async () => streamResponse(['unexpected']), {
      cache,
      maxConcurrent: 2,
      maxAggregateBytes: first.expectedBytes,
      timeoutMs: 2_000,
    });
    const pendingFirst = downloads.download(first);
    await vi.waitFor(() => expect(downloads.occupancy).toMatchObject({ activeVerifications: 1 }));
    await expect(downloads.download(second)).rejects.toMatchObject({
      code: 'quota-exceeded',
    });
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 1,
      activeVerifications: 1,
      reservedBytes: first.expectedBytes,
    });
    expect(downloads.outstandingBytes).toBeLessThanOrEqual(first.expectedBytes);
    release();
    await pendingFirst;
    await downloads.whenIdle();
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 0,
      activeVerifications: 0,
      reservedBytes: 0,
    });
  });

  it('deduplicates compatible lookups and rejects conflicting specifications', async () => {
    const request = spec('compatible cached payload');
    const { cache } = await admittedCache('compatible cached payload');
    const gates = new Map<string, Promise<void>>();
    let release!: () => void;
    gates.set(
      request.key,
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );
    const spy = gated(cache, gates);
    const downloads = manager(async () => streamResponse(['unexpected']), {
      cache,
      timeoutMs: 2_000,
    });
    const first = downloads.download(request);
    const second = downloads.download(request);
    const conflicting = downloads.download({
      ...request,
      sha256: bytesFor('another payload').sha256,
      expectedBytes: bytesFor('another payload').size,
    });
    await expect(conflicting).rejects.toMatchObject({ code: 'download-failed' });
    expect(downloads.occupancy.cacheVerifications).toBe(1);
    release();
    await expect(first).resolves.toMatchObject({ fromCache: true });
    await expect(second).resolves.toMatchObject({ fromCache: true });
    await downloads.whenIdle();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(downloads.outstandingBytes).toBe(0);
  });

  it('does not double-release or activate stale results after caller timeout', async () => {
    const request = spec('stale payload');
    const { cache } = await admittedCache('stale payload');
    const gates = new Map<string, Promise<void>>();
    let release!: () => void;
    gates.set(
      request.key,
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );
    gated(cache, gates);
    const fetchImpl = vi.fn(async () => streamResponse(['unexpected']));
    const downloads = manager(fetchImpl, { cache, timeoutMs: 30 });
    await expect(downloads.download(request)).rejects.toMatchObject({
      code: 'download-timeout',
    });
    downloads.cancelAll();
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 1,
      activeVerifications: 1,
    });
    release();
    await downloads.whenIdle();
    expect(downloads.occupancy).toMatchObject({
      cacheVerifications: 0,
      activeVerifications: 0,
      reservedBytes: 0,
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    await expect(downloads.download(request)).resolves.toMatchObject({ fromCache: true });
    expect(downloads.outstandingBytes).toBe(0);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
