/**
 * Bounded browser storage for verified analysis artifacts.
 *
 * Trade-off (documented in the Phase 4 validation report): published ONNX
 * models are stored in IndexedDB because they are large, immutable, reused per
 * initialization and are the only authorized offline-eligible artifact. Runtime
 * JavaScript/WASM assets are *not* duplicated into IndexedDB: executing stored
 * bytes would require blob workers (broader CSP) and Pyodide resolves its
 * internal assets by URL. They are verified on every initialization and reused
 * through the origin's immutable HTTP cache instead.
 *
 * Storage is an optimisation, never a correctness dependency: a denied,
 * unavailable or quota-exhausted store degrades to in-memory/unverified reuse and
 * the application stays usable.
 */
import { BrowserAnalysisError, LIMITS } from './contracts';

export interface AdmissionResult {
  retained: boolean;
  persistent: boolean;
  reason: 'stored' | 'quota-exceeded' | 'storage-unavailable';
}

export interface StoredArtifact {
  key: string;
  publicationId: string | null;
  displayName: string | null;
  size: number;
  lastUsed: number;
}

export interface ArtifactStore {
  readonly persistent: boolean;
  put(
    key: string,
    bytes: ArrayBuffer,
    meta: { publicationId?: string; displayName?: string },
  ): Promise<void>;
  get(key: string): Promise<ArrayBuffer | null>;
  touch(key: string, at?: number): Promise<void>;
  list(): Promise<StoredArtifact[]>;
  remove(key: string): Promise<void>;
  clear(): Promise<void>;
}

interface IndexedRecord {
  key: string;
  bytes: ArrayBuffer;
  bytesLength: number;
  publicationId: string | null;
  displayName: string | null;
  lastUsed: number;
}

const DB_NAME = 'hnefatafl-browser-analysis';
const DB_VERSION = 1;
const STORE = 'artifacts';

function now(): number {
  return Date.now();
}

export class MemoryArtifactStore implements ArtifactStore {
  readonly persistent = false;
  private records = new Map<string, IndexedRecord>();

  async put(
    key: string,
    bytes: ArrayBuffer,
    meta: { publicationId?: string; displayName?: string },
  ): Promise<void> {
    // Store a copy so callers cannot mutate a verified artifact after admission.
    this.records.set(key, {
      key,
      bytes: bytes.slice(0),
      bytesLength: bytes.byteLength,
      publicationId: meta.publicationId ?? null,
      displayName: meta.displayName ?? null,
      lastUsed: now(),
    });
  }

  async get(key: string): Promise<ArrayBuffer | null> {
    return this.records.get(key)?.bytes.slice(0) ?? null;
  }

  async touch(key: string, at = now()): Promise<void> {
    const record = this.records.get(key);
    if (record) record.lastUsed = at;
  }

  async list(): Promise<StoredArtifact[]> {
    return [...this.records.values()].map((record) => ({
      key: record.key,
      publicationId: record.publicationId,
      displayName: record.displayName,
      size: record.bytesLength,
      lastUsed: record.lastUsed,
    }));
  }

  async remove(key: string): Promise<void> {
    this.records.delete(key);
  }

  async clear(): Promise<void> {
    this.records.clear();
  }
}

function request<T>(value: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    value.onsuccess = () => resolve(value.result);
    value.onerror = () => reject(value.error ?? new Error('IndexedDB request failed'));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () =>
      reject(transaction.error ?? new Error('IndexedDB transaction failed'));
    transaction.onabort = () =>
      reject(transaction.error ?? new Error('IndexedDB transaction aborted'));
  });
}

export class IndexedDbArtifactStore implements ArtifactStore {
  readonly persistent = true;
  private constructor(private readonly db: IDBDatabase) {}

  static async open(): Promise<IndexedDbArtifactStore> {
    if (typeof indexedDB === 'undefined') {
      throw new BrowserAnalysisError('storage-unavailable', 'Browser storage is unavailable');
    }
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const open = indexedDB.open(DB_NAME, DB_VERSION);
      open.onupgradeneeded = () => {
        const database = open.result;
        if (!database.objectStoreNames.contains(STORE)) {
          database.createObjectStore(STORE, { keyPath: 'key' });
        }
      };
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error ?? new Error('Cannot open browser storage'));
      open.onblocked = () => reject(new Error('Browser storage upgrade is blocked'));
    });
    return new IndexedDbArtifactStore(db);
  }

  async put(
    key: string,
    bytes: ArrayBuffer,
    meta: { publicationId?: string; displayName?: string },
  ): Promise<void> {
    // One readwrite transaction writes the complete artifact; there is no
    // partially activated entry and a concurrent tab sees either nothing or all.
    const transaction = this.db.transaction(STORE, 'readwrite');
    const store = transaction.objectStore(STORE);
    store.put({
      key,
      bytes,
      bytesLength: bytes.byteLength,
      publicationId: meta.publicationId ?? null,
      displayName: meta.displayName ?? null,
      lastUsed: now(),
    });
    await transactionDone(transaction);
  }

  async get(key: string): Promise<ArrayBuffer | null> {
    const transaction = this.db.transaction(STORE, 'readonly');
    const record = await request<IndexedRecord | undefined>(
      transaction.objectStore(STORE).get(key),
    );
    return record?.bytes ?? null;
  }

  async touch(key: string, at = now()): Promise<void> {
    const transaction = this.db.transaction(STORE, 'readwrite');
    const store = transaction.objectStore(STORE);
    const record = await request<IndexedRecord | undefined>(store.get(key));
    if (record) store.put({ ...record, lastUsed: at });
    await transactionDone(transaction);
  }

  async list(): Promise<StoredArtifact[]> {
    const transaction = this.db.transaction(STORE, 'readonly');
    const records = await request<IndexedRecord[]>(transaction.objectStore(STORE).getAll());
    return records.map((record) => ({
      key: record.key,
      publicationId: record.publicationId,
      displayName: record.displayName,
      size: record.bytesLength,
      lastUsed: record.lastUsed,
    }));
  }

  async remove(key: string): Promise<void> {
    const transaction = this.db.transaction(STORE, 'readwrite');
    transaction.objectStore(STORE).delete(key);
    await transactionDone(transaction);
  }

  async clear(): Promise<void> {
    const transaction = this.db.transaction(STORE, 'readwrite');
    transaction.objectStore(STORE).clear();
    await transactionDone(transaction);
  }
}

export async function openArtifactStore(): Promise<ArtifactStore> {
  try {
    return await IndexedDbArtifactStore.open();
  } catch {
    return new MemoryArtifactStore();
  }
}

export type DigestFn = (bytes: ArrayBuffer) => Promise<string>;

export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new BrowserAnalysisError(
      'unsupported',
      'Cryptographic digests require a secure browser context',
    );
  }
  const digest = await subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('');
}

/** Content-addressed cache with quota awareness, LRU eviction and verification. */
export class ArtifactCache {
  private chain: Promise<void> = Promise.resolve();

  constructor(
    private readonly store: ArtifactStore,
    private readonly options: {
      maxBytes?: number;
      maxEntries?: number;
      now?: () => number;
    } = {},
  ) {}

  private get maxBytes(): number {
    return this.options.maxBytes ?? LIMITS.maxStoredModelBytes;
  }

  private get maxEntries(): number {
    return this.options.maxEntries ?? LIMITS.maxStoredModels;
  }

  get persistent(): boolean {
    return this.store.persistent;
  }

  async totalBytes(): Promise<number> {
    return (await this.store.list()).reduce((sum, entry) => sum + entry.size, 0);
  }

  /**
   * Admit a fully downloaded, digest-verified artifact.
   *
   * Admissions are serialized so concurrent callers cannot each observe spare
   * quota and jointly exceed the byte or entry limits. The incoming key's own
   * older copy is replaced without evicting other entries. Storage failure is
   * reported (`retained:false`) rather than breaking the download; the
   * application remains usable without persistent retention.
   */
  admit(
    key: string,
    bytes: ArrayBuffer,
    meta: { publicationId?: string; displayName?: string },
  ): Promise<AdmissionResult> {
    const queued = this.chain.then(() => this.admitNow(key, bytes, meta));
    this.chain = queued.then(
      () => undefined,
      () => undefined,
    );
    return queued;
  }

  private async admitNow(
    key: string,
    bytes: ArrayBuffer,
    meta: { publicationId?: string; displayName?: string },
  ): Promise<AdmissionResult> {
    if (bytes.byteLength > this.maxBytes) {
      return { retained: false, persistent: this.persistent, reason: 'quota-exceeded' };
    }
    try {
      const entries = (await this.store.list()).sort((a, b) => a.lastUsed - b.lastUsed);
      const existing = entries.find((entry) => entry.key === key);
      let total = entries.reduce((sum, entry) => sum + entry.size, 0);
      let count = entries.length;
      if (existing) total -= existing.size;
      else count += 1;
      const evictable = entries.filter((entry) => entry.key !== key);
      while (
        (total + bytes.byteLength > this.maxBytes || count > this.maxEntries) &&
        evictable.length > 0
      ) {
        const victim = evictable.shift()!;
        await this.store.remove(victim.key);
        total -= victim.size;
        count -= 1;
      }
      if (total + bytes.byteLength > this.maxBytes || count > this.maxEntries) {
        return { retained: false, persistent: this.persistent, reason: 'quota-exceeded' };
      }
      await this.store.put(key, bytes, meta);
      await this.store.touch(key, this.options.now?.() ?? Date.now()).catch(() => undefined);
      return { retained: true, persistent: this.persistent, reason: 'stored' };
    } catch {
      // Quota error, private-mode restriction or closed database: keep the
      // downloaded bytes usable for this operation but do not claim retention.
      return { retained: false, persistent: this.persistent, reason: 'storage-unavailable' };
    }
  }

  /**
   * Return verified bytes, deleting a corrupted persisted copy. A cache miss is
   * `null`; a size or digest mismatch is invalidated and reported as a miss.
   */
  async getVerified(
    key: string,
    digest: string,
    digestFn: DigestFn = sha256Hex,
    expectedBytes?: number,
  ): Promise<ArrayBuffer | null> {
    let bytes: ArrayBuffer | null;
    try {
      bytes = await this.store.get(key);
    } catch {
      return null;
    }
    if (!bytes) return null;
    if (expectedBytes !== undefined && bytes.byteLength !== expectedBytes) {
      await this.remove(key);
      return null;
    }
    const actual = await digestFn(bytes);
    if (actual !== digest) {
      await this.remove(key);
      return null;
    }
    await this.store.touch(key, this.options.now?.() ?? Date.now()).catch(() => undefined);
    return bytes;
  }

  async remove(key: string): Promise<void> {
    try {
      await this.store.remove(key);
    } catch {
      // Losing a cache entry is never fatal.
    }
  }

  async clear(): Promise<void> {
    try {
      await this.store.clear();
    } catch {
      // Unavailable storage is already the degraded state.
    }
  }
}
