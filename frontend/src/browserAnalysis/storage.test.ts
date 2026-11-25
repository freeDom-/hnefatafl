import { describe, expect, it } from 'vitest';

import { BrowserAnalysisError } from './contracts';
import { ArtifactCache, MemoryArtifactStore, openArtifactStore } from './storage';
import { bytesFor, digest } from './testSupport';

function artifact(contents: string) {
  const { bytes, sha256 } = bytesFor(contents);
  return { bytes, sha256 };
}

describe('artifact cache', () => {
  it('stores, verifies and reuses content-addressed artifacts', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const { bytes, sha256 } = artifact('model-a');
    await cache.admit(`model:${sha256}`, bytes, { publicationId: 'p', displayName: 'A' });
    const verified = await cache.getVerified(`model:${sha256}`, sha256, digest);
    expect(verified).not.toBeNull();
    expect(new TextDecoder().decode(verified!)).toBe('model-a');
    expect(await cache.totalBytes()).toBe(bytes.byteLength);
    const entries = await store.list();
    expect(entries[0]).toMatchObject({ key: `model:${sha256}`, size: bytes.byteLength });
  });

  it('invalidates corrupted persisted artifacts on reuse', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const { bytes, sha256 } = artifact('model-b');
    await cache.admit(`model:${sha256}`, bytes, {});
    // Simulate a corrupted record with the same key but different bytes.
    await store.put(`model:${sha256}`, artifact('corrupted').bytes, {});
    expect(await cache.getVerified(`model:${sha256}`, sha256, digest)).toBeNull();
    expect(await store.list()).toHaveLength(0);
  });

  it('evicts least-recently used entries under byte and entry quotas', async () => {
    let clock = Date.now();
    const cache = new ArtifactCache(new MemoryArtifactStore(), {
      maxBytes: 20,
      maxEntries: 2,
      now: () => clock++,
    });
    const first = artifact('0123456789');
    const second = artifact('abcdefghij');
    const third = artifact('ABCDEFGHIJ');
    await cache.admit('first', first.bytes, {});
    await cache.admit('second', second.bytes, {});
    await cache.getVerified('first', first.sha256, digest);
    await cache.admit('third', third.bytes, {});
    const entries = await (cache as any).store.list();
    expect(entries.map((entry: { key: string }) => entry.key).sort()).toEqual(['first', 'third']);
  });

  it('rejects an artifact larger than the application quota', async () => {
    const cache = new ArtifactCache(new MemoryArtifactStore(), { maxBytes: 4 });
    await expect(cache.admit('big', artifact('0123456789').bytes, {})).rejects.toMatchObject({
      code: 'quota-exceeded',
    });
  });

  it('degrades to a non-persistent store when IndexedDB is unavailable', async () => {
    const store = await openArtifactStore();
    expect(store.persistent).toBe(false);
    const cache = new ArtifactCache(store);
    const { bytes, sha256 } = artifact('model-c');
    await cache.admit(`model:${sha256}`, bytes, {});
    expect(await cache.getVerified(`model:${sha256}`, sha256, digest)).not.toBeNull();
  });

  it('reports storage failures without breaking the application', async () => {
    const failure = new MemoryArtifactStore();
    failure.put = async () => {
      throw new BrowserAnalysisError('quota-exceeded', 'full');
    };
    const cache = new ArtifactCache(failure);
    const { bytes } = artifact('model-d');
    await expect(cache.admit('key', bytes, {})).rejects.toMatchObject({ code: 'quota-exceeded' });
    await expect(cache.clear()).resolves.toBeUndefined();
  });
});
