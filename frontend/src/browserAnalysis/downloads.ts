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
  generation: number;
  deadline: number;
  subscribers: Set<Subscriber>;
  settled: boolean;
  terminal: boolean;
}

interface Waiter {
  resolve: (reservation: Reservation) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout> | null;
  signal: AbortSignal;
  onAbort: () => void;
  generation: number;
}

interface Reservation {
  generation: number;
  released: boolean;
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
  private generation = 0;
  private activeByKey = new Map<string, SharedDownload>();
  /** Actual transfers still running, including retiring generations. */
  private activeTransfers = 0;
  /** Byte reservations held until the corresponding transfer genuinely settles. */
  private reservedBytes = 0;
  private queue: Waiter[] = [];
  private transfers = new Set<SharedDownload>();
  private idleWaiters: Array<() => void> = [];

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
  ) {}

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
    return this.activeTransfers;
  }

  get outstandingBytes(): number {
    return this.reservedBytes;
  }

  get occupancy(): {
    activeTransfers: number;
    reservedBytes: number;
    transfers: number;
    queued: number;
  } {
    return {
      activeTransfers: this.activeTransfers,
      reservedBytes: this.reservedBytes,
      transfers: this.transfers.size,
      queued: this.queue.length,
    };
  }

  /** Resolve once every transfer (including non-interruptible retirement) settles. */
  whenIdle(): Promise<void> {
    if (this.transfers.size === 0 && this.queue.length === 0) return Promise.resolve();
    return new Promise<void>((resolve) => this.idleWaiters.push(resolve));
  }

  private notifyIdle(): void {
    if (this.transfers.size !== 0 || this.queue.length !== 0) return;
    const waiters = this.idleWaiters;
    this.idleWaiters = [];
    for (const resolve of waiters) resolve();
  }

  async drain(timeoutMs: number): Promise<{
    drained: boolean;
    activeTransfers: number;
    reservedBytes: number;
    transfers: number;
    queued: number;
  }> {
    if (this.transfers.size === 0 && this.queue.length === 0) {
      return { drained: true, ...this.occupancy };
    }
    let timer: ReturnType<typeof setTimeout> | null = null;
    const timeout = new Promise<void>((resolve) => {
      timer = setTimeout(resolve, timeoutMs);
    });
    try {
      await Promise.race([this.whenIdle(), timeout]);
    } finally {
      if (timer !== null) clearTimeout(timer);
    }
    return { drained: this.transfers.size === 0 && this.queue.length === 0, ...this.occupancy };
  }

  private acquire(
    generation: number,
    shared: SharedDownload,
    deadline: number,
  ): Promise<Reservation> {
    if (generation !== this.generation || shared.controller.signal.aborted) {
      return Promise.reject(abortError('Download was cancelled'));
    }
    if (this.activeTransfers < this.maxConcurrent) {
      this.activeTransfers += 1;
      return Promise.resolve({ generation, released: false });
    }
    if (this.queue.length >= this.maxConcurrent * 4) {
      return Promise.reject(
        new BrowserAnalysisError('quota-exceeded', 'Download queue is full', true),
      );
    }
    return new Promise<Reservation>((resolve, reject) => {
      const remaining = deadline - now();
      if (remaining <= 0) {
        reject(
          new BrowserAnalysisError('download-failed', 'Download queue deadline expired', true),
        );
        return;
      }
      const waiter: Waiter = {
        resolve,
        reject,
        timer: null,
        signal: shared.controller.signal,
        generation,
        onAbort: () => {
          this.queue = this.queue.filter((entry) => entry !== waiter);
          if (waiter.timer !== null) clearTimeout(waiter.timer);
          reject(abortError('Download cancelled while queued'));
        },
      };
      if (waiter.signal.aborted) {
        waiter.onAbort();
        return;
      }
      waiter.signal.addEventListener('abort', waiter.onAbort, { once: true });
      waiter.timer = setTimeout(() => {
        this.queue = this.queue.filter((entry) => entry !== waiter);
        waiter.signal.removeEventListener('abort', waiter.onAbort);
        reject(
          new BrowserAnalysisError('download-failed', 'Download queue deadline expired', true),
        );
      }, remaining);
      this.queue.push(waiter);
    });
  }

  private release(reservation: Reservation): void {
    if (reservation.released) return;
    reservation.released = true;
    // The slot becomes genuinely free only when the transfer has settled,
    // regardless of whether its generation was cancelled.
    this.activeTransfers = Math.max(0, this.activeTransfers - 1);
    const waiter = this.queue.shift();
    if (!waiter) return;
    if (waiter.timer !== null) clearTimeout(waiter.timer);
    waiter.signal.removeEventListener('abort', waiter.onAbort);
    if (waiter.signal.aborted || waiter.generation !== this.generation) {
      waiter.reject(abortError('Download was cancelled'));
      this.release({ generation: this.generation, released: false });
      return;
    }
    this.activeTransfers += 1;
    waiter.resolve({ generation: this.generation, released: false });
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
    const generation = this.generation;
    const deadline = now() + this.timeoutMs;
    if (hooks.signal?.aborted) {
      throw abortError('Download cancelled');
    }
    if (this.options.cache) {
      const cached = await this.raceDeadline(
        this.options.cache.getVerified(spec.key, spec.sha256, this.digestFn, spec.expectedBytes),
        deadline,
        'Cache lookup',
      );
      // Ownership is captured before the asynchronous cache lookup; a cancelAll
      // during the lookup must not start a new obsolete transfer afterwards.
      if (generation !== this.generation || hooks.signal?.aborted) {
        throw abortError('Download cancelled');
      }
      if (cached) {
        if (now() > deadline) {
          throw new BrowserAnalysisError(
            'download-timeout',
            'Cache lookup exceeded its deadline',
            true,
          );
        }
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
    if (
      shared &&
      (shared.generation !== this.generation || shared.controller.signal.aborted || shared.terminal)
    ) {
      // A cancelled/abandoned transfer is replaced, never joined. Its cleanup
      // will only remove itself if this newer entry is still not present.
      shared = undefined;
    }
    if (!shared) {
      shared = this.createShared(spec, deadline);
      this.activeByKey.set(spec.key, shared);
      this.start(shared);
    }
    return this.subscribe(shared, hooks);
  }

  private raceDeadline<T>(promise: Promise<T>, deadline: number, stage: string): Promise<T> {
    const remaining = deadline - now();
    if (remaining <= 0) {
      promise.catch(() => undefined);
      return Promise.reject(
        new BrowserAnalysisError('download-timeout', `${stage} exceeded its deadline`, true),
      );
    }
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(
          new BrowserAnalysisError('download-timeout', `${stage} exceeded its deadline`, true),
        );
      }, remaining);
      promise.then(
        (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        (error) => {
          clearTimeout(timer);
          reject(error);
        },
      );
    });
  }

  private createShared(spec: DownloadSpec, deadline: number): SharedDownload {
    const shared: SharedDownload = {
      spec,
      promise: Promise.resolve() as unknown as Promise<DownloadResult>,
      controller: new AbortController(),
      generation: this.generation,
      deadline,
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
      const remaining = shared.deadline - now();
      const deadlineTimer = setTimeout(
        () => {
          if (!subscriber.active) return;
          detach();
          if (shared.subscribers.size === 0 && !shared.terminal) {
            shared.controller.abort('caller deadline');
          }
          reject(
            new BrowserAnalysisError(
              'download-timeout',
              'Download did not complete within its deadline',
              true,
            ),
          );
        },
        Math.max(0, remaining),
      );
      const detach = () => {
        subscriber.active = false;
        shared.subscribers.delete(subscriber);
        hooks.signal?.removeEventListener('abort', onAbort);
        clearTimeout(deadlineTimer);
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
      this.transfers.delete(shared);
      if (this.activeByKey.get(shared.spec.key) === shared) {
        this.activeByKey.delete(shared.spec.key);
      }
      this.notifyIdle();
    };
    this.transfers.add(shared);
    shared.promise = this.run(shared);
    // Never leave an unhandled rejection when every caller has gone away.
    shared.promise.then(cleanup, cleanup);
  }

  private async run(shared: SharedDownload): Promise<DownloadResult> {
    const { spec } = shared;
    const generation = shared.generation;
    const deadline = shared.deadline;
    const reservation = await this.acquire(generation, shared, deadline);
    let reserved = false;
    try {
      if (
        generation !== this.generation ||
        shared.controller.signal.aborted ||
        shared.subscribers.size === 0
      ) {
        throw abortError('Download cancelled');
      }
      if (this.reservedBytes + spec.expectedBytes > this.maxAggregateBytes) {
        throw new BrowserAnalysisError(
          'quota-exceeded',
          'Aggregate download budget exceeded',
          true,
        );
      }
      this.reservedBytes += spec.expectedBytes;
      reserved = true;
      let attempt = 0;
      for (;;) {
        try {
          const result = await this.transfer(shared, deadline);
          if (
            generation !== this.generation ||
            shared.controller.signal.aborted ||
            shared.subscribers.size === 0
          ) {
            throw abortError('Download was cancelled before activation');
          }
          if (now() > deadline) {
            throw new BrowserAnalysisError(
              'download-timeout',
              'Download exceeded its deadline before cache admission',
              true,
            );
          }
          let retained = false;
          if (this.options.cache && spec.cacheable !== false) {
            const admitted = await this.options.cache.admit(spec.key, result.bytes, {
              publicationId: spec.publicationId,
              displayName: spec.displayName,
            });
            retained = admitted.retained;
            if (
              generation !== this.generation ||
              shared.controller.signal.aborted ||
              shared.subscribers.size === 0 ||
              now() > deadline
            ) {
              // Only remove the exact admission made by this obsolete transfer;
              // a newer same-key admission must survive.
              if (admitted.admissionId !== undefined) {
                await this.options.cache.removeOwned(spec.key, admitted.admissionId);
              }
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
            generation !== this.generation ||
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
      if (reserved) {
        // Retain the byte reservation until the transfer genuinely settles.
        this.reservedBytes = Math.max(0, this.reservedBytes - spec.expectedBytes);
      }
      this.release(reservation);
    }
  }

  /** Abort every outstanding transfer (logout, navigation, disposal). */
  cancelAll(reason = 'Download cancelled'): void {
    this.generation += 1;
    const queued = this.queue;
    this.queue = [];
    for (const waiter of queued) {
      if (waiter.timer !== null) clearTimeout(waiter.timer);
      waiter.signal.removeEventListener('abort', waiter.onAbort);
      waiter.reject(abortError(reason));
    }
    this.activeByKey.clear();
    // Active transfers keep their slots and byte reservations until their
    // underlying work genuinely settles; cancellation is not proof of completion.
    for (const shared of [...this.transfers]) {
      shared.controller.abort(reason);
    }
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
      throw new BrowserAnalysisError(
        'download-timeout',
        'Download exceeded its deadline before transfer',
        true,
      );
    }
    const timeoutController = new AbortController();
    const onSharedAbort = () => timeoutController.abort();
    shared.controller.signal.addEventListener('abort', onSharedAbort, { once: true });
    const timeout = setTimeout(() => timeoutController.abort('timeout'), remaining);
    let response: Response | null = null;
    let bodyConsumed = false;
    try {
      response = await this.fetchImpl(spec.url, {
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
      bodyConsumed = true;
      if (now() > deadline) {
        throw new BrowserAnalysisError(
          'download-timeout',
          'Download exceeded its deadline after transfer',
          true,
        );
      }
      const digest = await this.digestFn(bytes);
      if (now() > deadline) {
        throw new BrowserAnalysisError(
          'download-timeout',
          'Download exceeded its deadline during hashing',
          true,
        );
      }
      if (digest !== spec.sha256) {
        throw new BrowserAnalysisError(
          'digest-mismatch',
          'Downloaded artifact failed its integrity check',
        );
      }
      return { key: spec.key, bytes, sha256: digest, fromCache: false };
    } catch (error) {
      if (response && !bodyConsumed) {
        // Explicitly cancel the unread response stream; releasing a reader lock
        // is not equivalent to cancelling the underlying body.
        try {
          await response.body?.cancel();
        } catch {
          // The transport may already be closed.
        }
      }
      if (shared.controller.signal.aborted) throw abortError('Download cancelled');
      if (timeoutController.signal.aborted) {
        throw new BrowserAnalysisError(
          'download-timeout',
          'Download exceeded its deadline during transfer',
          true,
        );
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
    let completed = false;
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
      completed = true;
    } finally {
      if (!completed) {
        try {
          await reader.cancel();
        } catch {
          // The stream may already be errored/closed.
        }
      }
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
