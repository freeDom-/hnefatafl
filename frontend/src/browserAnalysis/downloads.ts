/**
 * Bounded, cancellable artifact download manager.
 *
 * Every download is explicitly initiated, size-bounded, digest-verified before
 * activation, cancellable and deduplicated. An HTTP success status alone is never
 * treated as proof of integrity. Nothing is persisted until the complete byte
 * sequence matches its declared size and SHA-256 digest.
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
}

export type ProgressListener = (progress: DownloadProgress) => void;

interface SharedDownload {
  promise: Promise<DownloadResult>;
  controller: AbortController;
  declaredBytes: number;
  settled: boolean;
  generation: number;
  listeners: Set<ProgressListener>;
}

type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

function abortError(message: string): BrowserAnalysisError {
  return new BrowserAnalysisError('download-aborted', message);
}

export class DownloadManager {
  private active = new Map<string, SharedDownload>();
  private generation = 0;
  private inflightBytes = 0;
  private queue: Array<() => void> = [];
  private slots: number;

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
    this.slots = this.maxConcurrent;
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
    return this.active.size;
  }

  get outstandingBytes(): number {
    return this.inflightBytes;
  }

  private acquire(): Promise<void> {
    if (this.slots > 0) {
      this.slots -= 1;
      return Promise.resolve();
    }
    return new Promise((resolve) => this.queue.push(resolve));
  }

  private release(): void {
    const next = this.queue.shift();
    if (next) next();
    else this.slots += 1;
  }

  /**
   * Download one artifact. Concurrent callers for the same key share one transfer
   * and one verification; each caller can still cancel its own await.
   */
  async download(
    spec: DownloadSpec,
    hooks: { onProgress?: ProgressListener; signal?: AbortSignal } = {},
  ): Promise<DownloadResult> {
    this.validateSpec(spec);
    if (hooks.signal?.aborted) throw abortError('Download cancelled');
    if (this.options.cache) {
      const cached = await this.options.cache.getVerified(spec.key, spec.sha256, this.digestFn);
      if (cached) {
        return { key: spec.key, bytes: cached, sha256: spec.sha256, fromCache: true };
      }
    }
    let shared = this.active.get(spec.key);
    if (!shared) {
      if (this.inflightBytes + spec.expectedBytes > this.maxAggregateBytes) {
        throw new BrowserAnalysisError(
          'quota-exceeded',
          'Aggregate download budget exceeded',
          true,
        );
      }
      this.inflightBytes += spec.expectedBytes;
      shared = this.start(spec);
      this.active.set(spec.key, shared);
    }
    return this.awaitShared(shared, hooks);
  }

  private awaitShared(
    shared: SharedDownload,
    hooks: { onProgress?: ProgressListener; signal?: AbortSignal },
  ): Promise<DownloadResult> {
    const listener = hooks.onProgress;
    if (listener) shared.listeners.add(listener);
    const onAbort = () => {
      rejectWith(abortError('Download cancelled'));
    };
    let rejectWith: (error: Error) => void = () => undefined;
    const cleanup = () => {
      if (listener) shared.listeners.delete(listener);
      hooks.signal?.removeEventListener('abort', onAbort);
    };
    return new Promise<DownloadResult>((resolve, reject) => {
      rejectWith = reject;
      if (hooks.signal) hooks.signal.addEventListener('abort', onAbort, { once: true });
      shared.promise.then(
        (value) => {
          cleanup();
          resolve(value);
        },
        (error) => {
          cleanup();
          reject(error);
        },
      );
    });
  }

  /** Abort every outstanding transfer (logout, navigation, disposal). */
  cancelAll(reason = 'Download cancelled'): void {
    this.generation += 1;
    for (const shared of this.active.values()) {
      shared.settled = true;
      shared.controller.abort(reason);
    }
    this.active.clear();
    this.inflightBytes = 0;
    // Wake queued acquisitions so they observe the cancelled generation.
    const queued = this.queue;
    this.queue = [];
    this.slots = this.maxConcurrent;
    for (const resolve of queued) resolve();
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

  private start(spec: DownloadSpec): SharedDownload {
    const controller = new AbortController();
    const generation = this.generation;
    const shared: SharedDownload = {
      promise: Promise.resolve() as unknown as Promise<DownloadResult>,
      controller,
      declaredBytes: spec.expectedBytes,
      settled: false,
      generation,
      listeners: new Set(),
    };
    shared.promise = (async () => {
      await this.acquire();
      try {
        let attempt = 0;
        for (;;) {
          try {
            const result = await this.transfer(spec, controller.signal, shared);
            if (generation !== this.generation || shared.settled) {
              throw abortError('Download was cancelled before activation');
            }
            if (this.options.cache && spec.cacheable !== false) {
              await this.options.cache
                .admit(spec.key, result.bytes, {
                  publicationId: spec.publicationId,
                  displayName: spec.displayName,
                })
                .catch(() => undefined);
            }
            return result;
          } catch (error) {
            const failure = error as BrowserAnalysisError;
            const retryable =
              failure instanceof BrowserAnalysisError
                ? failure.retryable
                : (failure as { name?: string })?.name !== 'AbortError';
            if (
              !retryable ||
              controller.signal.aborted ||
              generation !== this.generation ||
              attempt >= this.retries
            ) {
              throw failure instanceof BrowserAnalysisError
                ? failure
                : abortError('Download failed');
            }
            attempt += 1;
          }
        }
      } finally {
        shared.settled = true;
        this.release();
        if (this.active.get(spec.key) === shared) {
          this.active.delete(spec.key);
          this.inflightBytes = Math.max(0, this.inflightBytes - spec.expectedBytes);
        }
      }
    })();
    // Never leave an unhandled rejection when every caller has gone away.
    shared.promise.catch(() => undefined);
    return shared;
  }

  private async transfer(
    spec: DownloadSpec,
    signal: AbortSignal,
    shared: SharedDownload,
  ): Promise<DownloadResult> {
    const timeoutController = new AbortController();
    const onExternalAbort = () => timeoutController.abort();
    signal.addEventListener('abort', onExternalAbort, { once: true });
    const timeout = setTimeout(() => timeoutController.abort('timeout'), this.timeoutMs);
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
      const bytes = await this.readBounded(response, spec, timeoutController.signal, shared);
      const digest = await this.digestFn(bytes);
      if (digest !== spec.sha256) {
        throw new BrowserAnalysisError(
          'digest-mismatch',
          'Downloaded artifact failed its integrity check',
        );
      }
      return { key: spec.key, bytes, sha256: digest, fromCache: false };
    } catch (error) {
      if (signal.aborted) throw abortError('Download cancelled');
      if (timeoutController.signal.aborted) {
        throw new BrowserAnalysisError('download-failed', 'Download exceeded its deadline', true);
      }
      if (error instanceof BrowserAnalysisError) throw error;
      throw new BrowserAnalysisError('download-failed', 'Download failed', true);
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener('abort', onExternalAbort);
    }
  }

  private async readBounded(
    response: Response,
    spec: DownloadSpec,
    signal: AbortSignal,
    shared: SharedDownload,
  ): Promise<ArrayBuffer> {
    if (!response.body) {
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== spec.expectedBytes) {
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
        if (received > spec.expectedBytes) {
          throw new BrowserAnalysisError(
            'size-mismatch',
            'Downloaded artifact exceeds its declared size',
          );
        }
        chunks.push(value);
        for (const listener of shared.listeners) {
          listener({ key: spec.key, transferredBytes: received, totalBytes: spec.expectedBytes });
        }
      }
    } finally {
      reader.releaseLock();
    }
    if (received !== spec.expectedBytes) {
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
