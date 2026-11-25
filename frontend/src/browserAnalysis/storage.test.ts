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

  it('reports an artifact larger than the application quota without retention', async () => {
    const cache = new ArtifactCache(new MemoryArtifactStore(), { maxBytes: 4 });
    await expect(cache.admit('big', artifact('0123456789').bytes, {})).resolves.toMatchObject({
      retained: false,
      reason: 'quota-exceeded',
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
    await expect(cache.admit('key', bytes, {})).resolves.toMatchObject({
      retained: false,
      reason: 'storage-unavailable',
    });
    await expect(cache.clear()).resolves.toBeUndefined();
  });
});

describe('artifact cache — concurrent admission and retention', () => {
  it('serializes concurrent admissions within byte and entry quotas', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store, { maxBytes: 20, maxEntries: 5 });
    const artifacts = [artifact('0123456789'), artifact('abcdefghij'), artifact('ABCDEFGHIJ')];
    const results = await Promise.all(
      artifacts.map((value, index) => cache.admit(`k${index}`, value.bytes, {})),
    );
    expect(results.filter((result) => result.retained)).toHaveLength(3);
    // Serialized admission may evict an earlier artifact, but it can never leave
    // the cache above its declared byte or entry limits.
    expect(await cache.totalBytes()).toBeLessThanOrEqual(20);
    expect((await store.list()).length).toBeLessThanOrEqual(2);
  });

  it('replaces the same key without evicting unrelated entries', async () => {
    let clock = Date.now();
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store, {
      maxBytes: 20,
      maxEntries: 2,
      now: () => clock++,
    });
    await cache.admit('a', artifact('0123456789').bytes, {});
    await cache.admit('b', artifact('abcdefghij').bytes, {});
    await cache.admit('b', artifact('abcdefghij').bytes, {});
    const keys = (await store.list()).map((entry) => entry.key).sort();
    expect(keys).toEqual(['a', 'b']);
    expect(await cache.totalBytes()).toBe(20);
  });

  it('reports in-memory retention separately from persistent retention', async () => {
    const cache = new ArtifactCache(new MemoryArtifactStore());
    const result = await cache.admit('key', artifact('payload').bytes, {});
    expect(result).toMatchObject({ retained: true, persistent: false, reason: 'stored' });
  });

  it('invalidates a cached artifact whose size does not match the publication', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const value = artifact('model');
    await cache.admit('key', value.bytes, {});
    expect(
      await cache.getVerified('key', value.sha256, digest, value.bytes.byteLength + 1),
    ).toBeNull();
    expect(await store.list()).toHaveLength(0);
  });
});

describe('artifact cache — admission ownership', () => {
  it('never removes a newer same-key admission from an obsolete cleanup path', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const first = await cache.admit('key', artifact('first payload').bytes, {});
    const second = await cache.admit('key', artifact('second payload').bytes, {});
    expect(first.admissionId).not.toBe(second.admissionId);
    await cache.removeOwned('key', first.admissionId!);
    expect(await store.list()).toHaveLength(1);
    await cache.removeOwned('key', second.admissionId!);
    expect(await store.list()).toHaveLength(0);
  });

  it('does not remove an admission owned by a different key generation', async () => {
    const store = new MemoryArtifactStore();
    const cache = new ArtifactCache(store);
    const admitted = await cache.admit('key', artifact('payload').bytes, {});
    await cache.removeOwned('key', (admitted.admissionId ?? 1) + 1000);
    expect(await store.list()).toHaveLength(1);
  });
});
