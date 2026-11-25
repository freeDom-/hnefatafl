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
