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
  } = {},
) {
  return new DownloadManager({
    fetchImpl,
    digestFn: digest,
    retries: overrides.retries ?? 0,
    timeoutMs: overrides.timeoutMs ?? 500,
    maxAggregateBytes: overrides.maxAggregateBytes,
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
