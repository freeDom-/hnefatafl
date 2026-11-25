/**
 * Bounded, cancellable artifact download manager.
 *
 * Every download is explicitly initiated, size-bounded, digest-verified before
 * activation, cancellable and deduplicated. An HTTP success status alone is never
 * treated as proof of integrity. Nothing is persisted until the complete byte
 * sequence matches its declared size and SHA-256 digest.
 *
 * Concurrency and aggregate-byte accounting are generation-scoped: cancellation
 * starts a fresh reservation pool, while operations from the old generation can
 * only release reservations into their own retired pool. This prevents a
 * cancelled operation from freeing capacity owned by newer work.
 */
import { BrowserAnalysisError, LIMITS, type DownloadProgress } from './contracts';
import { classifyFetchFailure } from './manifests';
import { sha256Hex, type ArtifactCache, type DigestFn } from './storage';

export interface DownloadSpec {
  key: string;
  url: string;
  expectedBytes: number;
  sha256: string;
  publicationId?: string;
  displayName?: string;
  /** Runtime assets are verified but not duplicated into IndexedDB. */
  cacheable?: boolean;
  /** Immutable, content-addressed public asset: may reuse the origin HTTP cache. */
  immutable?: boolean;
}

export interface DownloadResult {
  key: string;
  bytes: ArrayBuffer;
  sha256: string;
  fromCache: boolean;
  /** True when the verified bytes were retained for future reuse. */
  retained?: boolean;
  persistent?: boolean;
}

export type ProgressListener = (progress: DownloadProgress) => void;

interface Subscriber {
  listener: ProgressListener | null;
  signal: AbortSignal | null;
  active: boolean;
}

interface SharedDownload {
  spec: DownloadSpec;
  promise: Promise<DownloadResult>;
  controller: AbortController;
  pool: SlotPool;
  generation: number;
  subscribers: Set<Subscriber>;
  settled: boolean;
  terminal: boolean;
}

interface Waiter {
  resolve: (reservation: Reservation) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout> | null;
}

interface Reservation {
  pool: SlotPool;
  released: boolean;
}

interface SlotPool {
  id: number;
  max: number;
  free: number;
  queue: Waiter[];
  inflightBytes: number;
  active: Set<SharedDownload>;
}

type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

function abortError(message: string): BrowserAnalysisError {
  return new BrowserAnalysisError('download-aborted', message);
}

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

function sameSpec(left: DownloadSpec, right: DownloadSpec): boolean {
  return (
    left.url === right.url &&
    left.expectedBytes === right.expectedBytes &&
    left.sha256 === right.sha256
  );
}

export class DownloadManager {
  private pool: SlotPool;
  private generation = 0;
  private activeByKey = new Map<string, SharedDownload>();

  constructor(
    private readonly options: {
      fetchImpl?: FetchFn;
      digestFn?: DigestFn;
      cache?: ArtifactCache | null;
      maxConcurrent?: number;
      maxAggregateBytes?: number;
      timeoutMs?: number;
      retries?: number;
    } = {},
  ) {
    this.pool = this.newPool(0);
  }

  private newPool(id: number): SlotPool {
    return {
      id,
      max: this.maxConcurrent,
      free: this.maxConcurrent,
      queue: [],
      inflightBytes: 0,
      active: new Set(),
    };
  }

  private get fetchImpl(): FetchFn {
    return this.options.fetchImpl ?? ((input, init) => fetch(input, init));
  }

  private get digestFn(): DigestFn {
    return this.options.digestFn ?? sha256Hex;
  }

  private get maxConcurrent(): number {
    return this.options.maxConcurrent ?? LIMITS.maxConcurrentDownloads;
  }

  private get maxAggregateBytes(): number {
    return this.options.maxAggregateBytes ?? LIMITS.maxAggregateDownloadBytes;
  }

  private get timeoutMs(): number {
    return this.options.timeoutMs ?? LIMITS.downloadTimeoutMs;
  }

  private get retries(): number {
    return this.options.retries ?? LIMITS.downloadRetries;
  }

  get activeCount(): number {
    return this.pool.active.size;
  }

  get outstandingBytes(): number {
    return this.pool.inflightBytes;
  }

  private acquire(pool: SlotPool, deadline: number): Promise<Reservation> {
    if (pool !== this.pool) {
      return Promise.reject(abortError('Download was cancelled'));
    }
    if (pool.free > 0) {
      pool.free -= 1;
      return Promise.resolve({ pool, released: false });
    }
    return new Promise<Reservation>((resolve, reject) => {
      const remaining = deadline - now();
      if (remaining <= 0) {
        reject(
          new BrowserAnalysisError('download-failed', 'Download queue deadline expired', true),
        );
        return;
      }
      const waiter: Waiter = { resolve, reject, timer: null };
      waiter.timer = setTimeout(() => {
        pool.queue = pool.queue.filter((entry) => entry !== waiter);
        reject(
          new BrowserAnalysisError('download-failed', 'Download queue deadline expired', true),
        );
      }, remaining);
      pool.queue.push(waiter);
    });
  }

  private release(pool: SlotPool, reservation: Reservation): void {
    if (reservation.released) return;
    reservation.released = true;
    // A retired pool never grants capacity to the current generation.
    if (pool !== this.pool) return;
    const waiter = pool.queue.shift();
    if (waiter) {
      if (waiter.timer !== null) clearTimeout(waiter.timer);
      waiter.resolve({ pool, released: false });
    } else {
      pool.free = Math.min(pool.max, pool.free + 1);
    }
  }

  /**
   * Download one artifact. Concurrent callers for the same key share one transfer
   * and one verification; each caller can cancel its own await without disturbing
   * another live subscriber.
   */
  async download(
    spec: DownloadSpec,
    hooks: { onProgress?: ProgressListener; signal?: AbortSignal } = {},
  ): Promise<DownloadResult> {
    this.validateSpec(spec);
    if (hooks.signal?.aborted) {
      throw abortError('Download cancelled');
    }
    if (this.options.cache) {
      const cached = await this.options.cache.getVerified(spec.key, spec.sha256, this.digestFn);
      if (hooks.signal?.aborted) throw abortError('Download cancelled');
      if (cached) {
        return {
          key: spec.key,
          bytes: cached,
          sha256: spec.sha256,
          fromCache: true,
          retained: true,
          persistent: this.options.cache.persistent,
        };
      }
    }

    let shared = this.activeByKey.get(spec.key);
    if (shared && !sameSpec(shared.spec, spec)) {
      throw new BrowserAnalysisError(
        'download-failed',
        'Conflicting download specifications share one artifact key',
      );
    }
    if (!shared) {
      shared = this.createShared(spec);
      this.activeByKey.set(spec.key, shared);
      this.start(shared);
    }
    return this.subscribe(shared, hooks);
  }

  private createShared(spec: DownloadSpec): SharedDownload {
    const shared: SharedDownload = {
      spec,
      promise: Promise.resolve() as unknown as Promise<DownloadResult>,
      controller: new AbortController(),
      pool: this.pool,
      generation: this.generation,
      subscribers: new Set(),
      settled: false,
      terminal: false,
    };
    return shared;
  }

  private subscribe(
    shared: SharedDownload,
    hooks: { onProgress?: ProgressListener; signal?: AbortSignal },
  ): Promise<DownloadResult> {
    if (hooks.signal?.aborted) {
      throw abortError('Download cancelled');
    }
    const subscriber: Subscriber = {
      listener: hooks.onProgress ?? null,
      signal: hooks.signal ?? null,
      active: true,
    };
    shared.subscribers.add(subscriber);
    return new Promise<DownloadResult>((resolve, reject) => {
      const detach = () => {
        subscriber.active = false;
        shared.subscribers.delete(subscriber);
        hooks.signal?.removeEventListener('abort', onAbort);
      };
      const onAbort = () => {
        if (!subscriber.active) return;
        detach();
        if (shared.subscribers.size === 0 && !shared.terminal) {
          // The last interested caller left: do not keep transferring bytes that
          // nobody will consume.
          shared.controller.abort('cancelled by subscriber');
        }
        reject(abortError('Download cancelled'));
      };
      if (hooks.signal) {
        hooks.signal.addEventListener('abort', onAbort, { once: true });
      }
      shared.promise.then(
        (value) => {
          if (!subscriber.active) return;
          detach();
          resolve(value);
        },
        (error) => {
          if (!subscriber.active) return;
          detach();
          reject(error);
        },
      );
    });
  }

  private publishProgress(shared: SharedDownload, transferred: number): void {
    for (const subscriber of [...shared.subscribers]) {
      if (!subscriber.active || subscriber.signal?.aborted) continue;
      subscriber.listener?.({
        key: shared.spec.key,
        transferredBytes: transferred,
        totalBytes: shared.spec.expectedBytes,
      });
    }
  }

  private start(shared: SharedDownload): void {
    const cleanup = () => {
      shared.terminal = true;
      shared.settled = true;
      shared.pool.active.delete(shared);
      if (this.activeByKey.get(shared.spec.key) === shared) {
        this.activeByKey.delete(shared.spec.key);
      }
    };
    shared.promise = this.run(shared);
    // Never leave an unhandled rejection when every caller has gone away.
    shared.promise.then(cleanup, cleanup);
  }

  private async run(shared: SharedDownload): Promise<DownloadResult> {
    const { spec, pool } = shared;
    const deadline = now() + this.timeoutMs;
    const reservation = await this.acquire(pool, deadline);
    shared.pool.active.add(shared);
    try {
      if (pool !== this.pool || shared.controller.signal.aborted || shared.subscribers.size === 0) {
        throw abortError('Download cancelled');
      }
      if (pool.inflightBytes + spec.expectedBytes > this.maxAggregateBytes) {
        throw new BrowserAnalysisError(
          'quota-exceeded',
          'Aggregate download budget exceeded',
          true,
        );
      }
      pool.inflightBytes += spec.expectedBytes;
      try {
        let attempt = 0;
        for (;;) {
          try {
            const result = await this.transfer(shared, deadline);
            if (
              pool !== this.pool ||
              shared.controller.signal.aborted ||
              shared.subscribers.size === 0
            ) {
              throw abortError('Download was cancelled before activation');
            }
            let retained = false;
            if (this.options.cache && spec.cacheable !== false) {
              const admitted = await this.options.cache.admit(spec.key, result.bytes, {
                publicationId: spec.publicationId,
                displayName: spec.displayName,
              });
              retained = admitted.retained;
              if (
                pool !== this.pool ||
                shared.controller.signal.aborted ||
                shared.subscribers.size === 0
              ) {
                // A cancelled/cached admission must not remain activated.
                await this.options.cache.remove(spec.key);
                throw abortError('Download was cancelled during cache admission');
              }
            }
            return {
              ...result,
              retained,
              persistent: this.options.cache?.persistent ?? false,
            };
          } catch (error) {
            const failure =
              error instanceof BrowserAnalysisError
                ? error
                : new BrowserAnalysisError('download-failed', 'Download failed', true);
            const retryable =
              error instanceof BrowserAnalysisError
                ? error.retryable
                : (error as { name?: string })?.name !== 'AbortError';
            if (
              !retryable ||
              pool !== this.pool ||
              shared.controller.signal.aborted ||
              shared.subscribers.size === 0 ||
              attempt >= this.retries ||
              now() >= deadline
            ) {
              throw failure;
            }
            attempt += 1;
          }
        }
      } finally {
        pool.inflightBytes = Math.max(0, pool.inflightBytes - spec.expectedBytes);
      }
    } finally {
      this.release(pool, reservation);
    }
  }

  /** Abort every outstanding transfer (logout, navigation, disposal). */
  cancelAll(reason = 'Download cancelled'): void {
    const retired = this.pool;
    this.generation += 1;
    this.pool = this.newPool(this.generation);
    this.activeByKey.clear();
    for (const waiter of retired.queue) {
      if (waiter.timer !== null) clearTimeout(waiter.timer);
      waiter.reject(abortError(reason));
    }
    retired.queue = [];
    for (const shared of [...retired.active]) {
      shared.controller.abort(reason);
    }
    retired.active.clear();
  }

  private validateSpec(spec: DownloadSpec): void {
    if (!spec.key || !spec.url) {
      throw new BrowserAnalysisError('download-failed', 'Download request is incomplete');
    }
    if (!/^[0-9a-f]{64}$/.test(spec.sha256)) {
      throw new BrowserAnalysisError('digest-mismatch', 'Download identity has no valid digest');
    }
    if (
      !Number.isInteger(spec.expectedBytes) ||
      spec.expectedBytes <= 0 ||
      spec.expectedBytes > LIMITS.maxAggregateDownloadBytes
    ) {
      throw new BrowserAnalysisError('size-mismatch', 'Download size is not acceptable');
    }
  }

  private async transfer(shared: SharedDownload, deadline: number): Promise<DownloadResult> {
    const { spec } = shared;
    const remaining = deadline - now();
    if (remaining <= 0) {
      throw new BrowserAnalysisError('download-failed', 'Download exceeded its deadline', true);
    }
    const timeoutController = new AbortController();
    const onSharedAbort = () => timeoutController.abort();
    shared.controller.signal.addEventListener('abort', onSharedAbort, { once: true });
    const timeout = setTimeout(() => timeoutController.abort('timeout'), remaining);
    try {
      const response = await this.fetchImpl(spec.url, {
        signal: timeoutController.signal,
        credentials: 'same-origin',
        cache: spec.immutable ? 'force-cache' : 'no-store',
        redirect: 'error',
      });
      if (!response.ok) throw classifyFetchFailure(response.status);
      const declared = response.headers.get('content-length');
      if (declared !== null && Number(declared) !== spec.expectedBytes) {
        throw new BrowserAnalysisError(
          'size-mismatch',
          'Server-declared size does not match the published artifact size',
        );
      }
      const bytes = await this.readBounded(response, shared, timeoutController.signal);
      if (now() > deadline) {
        throw new BrowserAnalysisError('download-failed', 'Download exceeded its deadline', true);
      }
      const digest = await this.digestFn(bytes);
      if (now() > deadline) {
        throw new BrowserAnalysisError('download-failed', 'Download exceeded its deadline', true);
      }
      if (digest !== spec.sha256) {
        throw new BrowserAnalysisError(
          'digest-mismatch',
          'Downloaded artifact failed its integrity check',
        );
      }
      return { key: spec.key, bytes, sha256: digest, fromCache: false };
    } catch (error) {
      if (shared.controller.signal.aborted) throw abortError('Download cancelled');
      if (timeoutController.signal.aborted) {
        throw new BrowserAnalysisError('download-failed', 'Download exceeded its deadline', true);
      }
      if (error instanceof BrowserAnalysisError) throw error;
      throw new BrowserAnalysisError('download-failed', 'Download failed', true);
    } finally {
      clearTimeout(timeout);
      shared.controller.signal.removeEventListener('abort', onSharedAbort);
    }
  }

  private async readBounded(
    response: Response,
    shared: SharedDownload,
    signal: AbortSignal,
  ): Promise<ArrayBuffer> {
    const expected = shared.spec.expectedBytes;
    if (!response.body) {
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== expected) {
        throw new BrowserAnalysisError(
          'size-mismatch',
          'Downloaded artifact has an unexpected size',
        );
      }
      return bytes;
    }
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let received = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (signal.aborted) throw abortError('Download cancelled');
        received += value.byteLength;
        if (received > expected) {
          throw new BrowserAnalysisError(
            'size-mismatch',
            'Downloaded artifact exceeds its declared size',
          );
        }
        chunks.push(value);
        this.publishProgress(shared, received);
      }
    } finally {
      reader.releaseLock();
    }
    if (received !== expected) {
      throw new BrowserAnalysisError(
        'size-mismatch',
        'Downloaded artifact was truncated or has an unexpected size',
      );
    }
    const bytes = new Uint8Array(received);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes.buffer;
  }
}
