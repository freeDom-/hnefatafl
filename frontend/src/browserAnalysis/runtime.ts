/**
 * Production browser-analysis runtime manager.
 *
 * Reuses the validated spike clients and workers verbatim from the staged runtime
 * directory, and owns everything the application must bound:
 *
 * - capability detection and availability;
 * - explicit, verified runtime asset loading (no eager Pyodide/ORT import);
 * - the Jarl alpha-beta path without any neural model or ORT initialization;
 * - neural MCTS over explicitly published models only;
 * - one active search, cancellation, watchdogs (owned by the validated clients)
 *   and structured errors;
 * - account-context invalidation and bounded disposal.
 *
 * It never invokes server analysis as a substitute and never silently changes
 * search strength, algorithms or providers.
 */
import {
  BrowserAnalysisError,
  LIMITS,
  RUNTIME_JS,
  type Availability,
  type BrowserAnalysisState,
  type DownloadProgress,
  type ModelManifest,
  type ProviderRequest,
  type RuntimeManifest,
  type RuntimeStatus,
  type SearchRequest,
  type SearchResult,
} from './contracts';
import { DownloadManager, type DownloadSpec } from './downloads';
import {
  assertModelRuntimeCompatible,
  classifyFetchFailure,
  modelArtifactUrl,
  runtimeAssetUrl,
  validateAvailability,
  validateModelCatalog,
  validateModelManifest,
} from './manifests';
import { ArtifactCache, openArtifactStore } from './storage';

type AnyRecord = Record<string, any>;

interface LocalAnalysisClientLike {
  request(payload: unknown, options?: { timeoutMs?: number }): Promise<unknown>;
  cancel(reason?: string): void;
  dispose(): void;
}

interface NeuralClientLike {
  load(
    url: string,
    options: {
      provider?: ProviderRequest;
      size?: number;
      ruleset?: string;
      expectedSha256?: string;
    },
  ): Promise<AnyRecord>;
  modelIdentity(): { sha256: string; ruleset: string; size: number };
  cancelWork?: (options?: { reason?: string }) => Promise<unknown>;
  dispose?(): void;
}

interface CoordinatorLike {
  search(request: AnyRecord): Promise<AnyRecord>;
  abort?(reason?: unknown): void;
  dispose(): void;
}

export interface RuntimeOptions {
  fetchImpl?: typeof fetch;
  cache?: ArtifactCache;
  loadModule?: (url: string) => Promise<AnyRecord>;
  onStateChange?: (status: RuntimeStatus) => void;
  digestFn?: (bytes: ArrayBuffer) => Promise<string>;
  storageQuotaBytes?: number;
}

const REQUIRED_CAPABILITIES: Array<[string, () => boolean]> = [
  ['WebAssembly', () => typeof WebAssembly === 'object'],
  ['Web Workers', () => typeof Worker === 'function'],
];

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

function asError(error: unknown, fallback: string): BrowserAnalysisError {
  if (error instanceof BrowserAnalysisError) return error;
  const message = (error as { message?: string })?.message ?? fallback;
  return new BrowserAnalysisError('search-failed', message, false);
}

export class BrowserAnalysisRuntime {
  private status: RuntimeStatus = {
    state: 'idle',
    runtimeId: null,
    engine: null,
    provider: null,
    error: null,
  };
  private manifest: RuntimeManifest | null = null;
  private generation = 0;
  private disposed = false;
  private jarl: LocalAnalysisClientLike | null = null;
  private neural: NeuralClientLike | null = null;
  private coordinator: CoordinatorLike | null = null;
  private activeProvider: ProviderRequest | null = null;
  private model: ModelManifest | null = null;
  private result: SearchResult | null = null;
  private cache: ArtifactCache | null;
  private downloads: DownloadManager;
  private moduleCache = new Map<string, AnyRecord>();
  private readonly loadModule: (url: string) => Promise<AnyRecord>;

  constructor(private readonly options: RuntimeOptions = {}) {
    this.cache = options.cache ?? null;
    this.downloads = new DownloadManager({
      fetchImpl: options.fetchImpl,
      digestFn: options.digestFn,
      cache: this.cache,
    });
    this.loadModule =
      options.loadModule ??
      (async (url: string) => (await import(/* @vite-ignore */ url)) as AnyRecord);
  }

  static async create(options: RuntimeOptions = {}): Promise<BrowserAnalysisRuntime> {
    let cache = options.cache ?? null;
    if (!cache) {
      const store = await openArtifactStore();
      cache = new ArtifactCache(store, {
        maxBytes: options.storageQuotaBytes ?? LIMITS.maxStoredModelBytes,
      });
    }
    return new BrowserAnalysisRuntime({ ...options, cache });
  }

  getStatus(): RuntimeStatus {
    return { ...this.status, error: this.status.error ? { ...this.status.error } : null };
  }

  private setState(
    state: BrowserAnalysisState,
    patch: Partial<Omit<RuntimeStatus, 'state'>> = {},
  ): RuntimeStatus {
    this.status = { ...this.status, ...patch, state };
    this.options.onStateChange?.(this.getStatus());
    return this.getStatus();
  }

  private fail(error: unknown): never {
    const failure = asError(error, 'Browser analysis failed');
    this.setState('error', {
      error: { code: failure.code, message: failure.message, retryable: failure.retryable },
    });
    throw failure;
  }

  async availability(): Promise<Availability> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const response = await fetchImpl('/api/browser-analysis/availability', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { accept: 'application/json' },
    });
    if (!response.ok) throw classifyFetchFailure(response.status);
    const payload = validateAvailability(await response.json());
    if (!payload.enabled) {
      this.setState('disabled', { runtimeId: null, engine: null, provider: null, error: null });
      return payload;
    }
    if (!payload.runtimeAvailable || !payload.runtime) {
      this.setState('unavailable', {
        runtimeId: null,
        engine: null,
        provider: null,
        error: {
          code: 'runtime-missing',
          message: `Browser runtime is unavailable (${payload.reason ?? 'unknown'})`,
          retryable: true,
        },
      });
      return payload;
    }
    this.manifest = payload.runtime;
    return payload;
  }

  async listModels(): Promise<ModelManifest[]> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const response = await fetchImpl('/api/browser-analysis/models', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { accept: 'application/json' },
    });
    if (!response.ok) throw classifyFetchFailure(response.status);
    return validateModelCatalog(await response.json());
  }

  /**
   * Verify every runtime asset from the trusted manifest, then lazily import the
   * validated client and prove the Python engine initializes inside the worker.
   * Jarl initialization never downloads a neural model or starts ONNX.
   */
  async initialize(
    options: { signal?: AbortSignal; skipProbe?: boolean } = {},
  ): Promise<RuntimeStatus> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    this.result = null;
    const availability = await this.availability();
    if (!availability.enabled) return this.getStatus();
    const manifest = this.manifest;
    if (!manifest) return this.getStatus();
    for (const [name, supported] of REQUIRED_CAPABILITIES) {
      if (!supported()) {
        return this.fail(
          new BrowserAnalysisError(
            'capability-missing',
            `${name} is not available in this browser`,
          ),
        );
      }
    }
    const generation = ++this.generation;
    try {
      this.setState('downloading', { runtimeId: manifest.runtimeId, error: null });
      await this.verifyRuntimeAssets(manifest, options.signal);
      if (generation !== this.generation) {
        throw new BrowserAnalysisError('cancelled', 'Initialization was superseded');
      }
      this.setState('initializing', { runtimeId: manifest.runtimeId });
      const analysisClient = await this.loadRuntimeModule(manifest, RUNTIME_JS.analysisClient);
      if (generation !== this.generation) {
        throw new BrowserAnalysisError('cancelled', 'Initialization was superseded');
      }
      const jarl = new analysisClient.LocalAnalysisClient() as LocalAnalysisClientLike;
      this.jarl = jarl;
      if (!options.skipProbe) {
        const info = (await this.withTimeout(
          jarl.request({ action: 'info' }, { timeoutMs: LIMITS.maxJobTimeoutMs }),
          LIMITS.initTimeoutMs,
          'Runtime initialization timed out',
        )) as AnyRecord;
        if (generation !== this.generation) {
          throw new BrowserAnalysisError('cancelled', 'Initialization was superseded');
        }
        this.setState('ready', { engine: (info?.info as AnyRecord) ?? info ?? null, error: null });
      } else {
        this.setState('ready', { error: null });
      }
      return this.getStatus();
    } catch (error) {
      if (generation !== this.generation) {
        return this.getStatus();
      }
      this.releaseWorkers();
      return this.fail(error);
    }
  }

  private async verifyRuntimeAssets(
    manifest: RuntimeManifest,
    signal?: AbortSignal,
  ): Promise<void> {
    const nextManifest = manifest;
    for (const file of nextManifest.files) {
      if (signal?.aborted) {
        throw new BrowserAnalysisError('download-aborted', 'Runtime download cancelled');
      }
      const spec: DownloadSpec = {
        key: `runtime:${nextManifest.runtimeId}:${file.path}`,
        url: runtimeAssetUrl(nextManifest, file.path),
        expectedBytes: file.bytes,
        sha256: file.sha256,
        cacheable: false,
        immutable: true,
      };
      // Verifying the complete inventory before activation also covers indirectly
      // loaded runtime files and worker assets. The immutable origin cache may
      // serve the bytes, but they are always re-hashed here.
      await this.downloads.download(spec, { signal });
    }
    // Required-entry compatibility is checked by the manifest validator; model
    // compatibility is checked when a model is selected.
  }

  private async loadRuntimeModule(manifest: RuntimeManifest, name: string): Promise<AnyRecord> {
    const cached = this.moduleCache.get(name);
    if (cached) return cached;
    const module = await this.loadModule(runtimeAssetUrl(manifest, name));
    this.moduleCache.set(name, module);
    return module;
  }

  private async withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new BrowserAnalysisError('search-failed', message, true)),
        ms,
      );
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

  /** Download and verify a published model into the bounded browser cache. */
  async downloadModel(
    model: ModelManifest,
    hooks: { onProgress?: (progress: DownloadProgress) => void; signal?: AbortSignal } = {},
  ): Promise<void> {
    if (!this.manifest) {
      throw new BrowserAnalysisError('not-initialized', 'The runtime manifest is not loaded');
    }
    assertModelRuntimeCompatible(model, this.manifest);
    await this.downloads.download(
      {
        key: `model:${model.onnxSha256}`,
        url: modelArtifactUrl(model),
        expectedBytes: model.artifactBytes,
        sha256: model.onnxSha256,
        publicationId: model.publicationId,
        displayName: model.displayName,
      },
      hooks,
    );
  }

  /**
   * Execute one advisory search. A model selects neural MCTS; without a model the
   * existing Jarl alpha-beta path runs through the classical worker.
   */
  async runSearch(
    request: SearchRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<SearchResult> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    if (!this.jarl)
      throw new BrowserAnalysisError('not-initialized', 'Initialize the runtime first');
    if (this.status.state === 'searching') {
      throw new BrowserAnalysisError('superseded', 'Another search is already active');
    }
    const manifest = this.manifest;
    if (!manifest) throw new BrowserAnalysisError('not-initialized', 'Runtime manifest is missing');
    const generation = ++this.generation;
    this.result = null;
    this.setState('searching', { error: null });
    const started = now();
    const provider = request.provider ?? 'wasm';
    const model = request.model ?? null;
    try {
      let raw: AnyRecord;
      if (!model) {
        raw = (await this.runJarl(request, options.signal)) as AnyRecord;
        this.model = null;
        this.activeProvider = null;
      } else {
        assertModelRuntimeCompatible(model, manifest);
        raw = await this.runNeural(request, model, provider, options.signal);
        this.model = model;
        this.activeProvider = provider;
      }
      if (generation !== this.generation) {
        throw new BrowserAnalysisError('superseded', 'Search result was superseded');
      }
      const result = this.finalize(raw, model, provider, started);
      this.result = result;
      this.setState('ready', {
        provider: result.provider,
        engine: this.status.engine,
        error: null,
      });
      return result;
    } catch (error) {
      if (generation !== this.generation) {
        throw new BrowserAnalysisError('cancelled', 'Search was cancelled', false);
      }
      const failure = asError(error, 'Search failed');
      if (failure.code === 'cancelled') {
        this.setState('cancelled', { error: null });
      } else {
        this.setState('error', {
          error: { code: failure.code, message: failure.message, retryable: failure.retryable },
        });
      }
      throw failure;
    }
  }

  private async runJarl(request: SearchRequest, signal?: AbortSignal): Promise<AnyRecord> {
    if (signal?.aborted) throw new BrowserAnalysisError('cancelled', 'Search cancelled');
    const client = this.jarl as LocalAnalysisClientLike;
    const result = (await client.request(
      {
        action: 'analysis',
        state: request.state,
        config: request.settings ?? {},
      },
      { timeoutMs: LIMITS.maxJobTimeoutMs },
    )) as AnyRecord;
    if (result?.ok !== true) {
      throw new BrowserAnalysisError(
        'search-failed',
        (result as AnyRecord)?.message ?? 'Local analysis failed',
      );
    }
    return result.event as AnyRecord;
  }

  private async runNeural(
    request: SearchRequest,
    model: ModelManifest,
    provider: ProviderRequest,
    signal?: AbortSignal,
  ): Promise<AnyRecord> {
    if (signal?.aborted) throw new BrowserAnalysisError('cancelled', 'Search cancelled');
    const manifest = this.manifest as RuntimeManifest;
    if (!this.neural) {
      const neuralModule = await this.loadRuntimeModule(manifest, RUNTIME_JS.neuralClient);
      const workerUrl = runtimeAssetUrl(manifest, RUNTIME_JS.neuralWorker);
      this.neural = new neuralModule.NeuralClient({ workerUrl }) as NeuralClientLike;
    }
    if (!this.coordinator) {
      const searchModule = await this.loadRuntimeModule(manifest, RUNTIME_JS.searchClient);
      this.coordinator = new searchModule.NeuralSearchCoordinator({
        neural: this.neural,
      }) as CoordinatorLike;
    }
    const neural = this.neural as NeuralClientLike;
    const loaded =
      this.model?.onnxSha256 === model.onnxSha256 && this.activeProvider === provider
        ? neural.modelIdentity()
        : await this.loadModelSession(model, provider);
    const identity = neural.modelIdentity();
    if (
      identity.sha256 !== model.onnxSha256 ||
      identity.ruleset !== model.ruleset ||
      identity.size !== model.boardSize ||
      loaded.ruleset !== model.ruleset
    ) {
      throw new BrowserAnalysisError('model-integrity', 'Loaded model identity does not match');
    }
    const coordinator = this.coordinator as CoordinatorLike;
    const result = (await coordinator.search({
      state: request.state,
      settings: request.settings ?? {},
      model: identity,
      provider: { requested: provider },
      trace: request.trace === true,
    })) as AnyRecord;
    return result;
  }

  private async loadModelSession(
    model: ModelManifest,
    provider: ProviderRequest,
  ): Promise<AnyRecord> {
    if (provider === 'webgpu' && typeof navigator !== 'undefined' && !('gpu' in navigator)) {
      throw new BrowserAnalysisError(
        'capability-missing',
        'WebGPU is not available in this browser',
      );
    }
    const neural = this.neural as NeuralClientLike;
    return neural.load(modelArtifactUrl(model), {
      provider,
      size: model.boardSize,
      ruleset: model.ruleset,
      expectedSha256: model.onnxSha256,
    });
  }

  private finalize(
    raw: AnyRecord,
    model: ModelManifest | null,
    provider: ProviderRequest,
    started: number,
  ): SearchResult {
    const elapsed = now() - started;
    const analysis = (raw?.analysis as AnyRecord) ?? raw?.result ?? raw ?? {};
    const placement =
      model === null
        ? 'wasm'
        : (((raw?.provider as AnyRecord)?.placement as SearchResult['provider']['placement']) ??
          (provider === 'webgpu' ? 'webgpu-session-unverified' : 'wasm'));
    return {
      analysis,
      provider: {
        requested: provider,
        placement,
        // The pinned ORT Web exposes no supported per-operator diagnostic; GPU
        // execution is never claimed from a session-level provider list.
        gpuExecutionVerified: false,
      },
      model: model
        ? { sha256: model.onnxSha256, ruleset: model.ruleset, size: model.boardSize }
        : null,
      elapsedMs: elapsed,
      authoritative: false,
    };
  }

  /** Cooperative cancellation of the active search and its inference work. */
  cancel(reason = 'Search cancelled'): void {
    this.generation += 1;
    try {
      this.coordinator?.abort?.(new Error(reason));
    } catch {
      // The validated coordinator owns bounded shutdown recovery.
    }
    try {
      this.jarl?.cancel(reason);
    } catch {
      // Idle cancellation is a no-op in the validated client.
    }
    if (this.status.state === 'searching' || this.status.state === 'initializing') {
      this.setState('cancelled', { error: null });
    }
  }

  /**
   * Invalidate all work bound to the previous account context. Published model
   * artifacts are public shared assets and may be retained; private replay data,
   * positions and results are never stored here. Delayed completions cannot
   * restore the previous account's result because the generation is bumped.
   */
  async invalidateAccountContext(): Promise<void> {
    this.generation += 1;
    this.downloads.cancelAll('Account context changed');
    this.result = null;
    this.model = null;
    this.activeProvider = null;
    this.releaseWorkers();
    this.setState('idle', { engine: null, provider: null, error: null });
  }

  /** Release workers and abort transfers; the runtime object becomes reusable. */
  async dispose(): Promise<void> {
    this.disposed = true;
    this.generation += 1;
    this.downloads.cancelAll('Runtime disposed');
    this.releaseWorkers();
    this.moduleCache.clear();
    this.manifest = null;
    this.jarl = null;
    this.neural = null;
    this.coordinator = null;
    this.result = null;
    this.setState('idle', { runtimeId: null, engine: null, provider: null, error: null });
  }

  private releaseWorkers(): void {
    try {
      this.coordinator?.dispose();
    } catch {
      // Bounded disposal is owned by the validated client.
    }
    try {
      this.neural?.dispose?.();
    } catch {
      // Ignore disposal failures on an already-terminated worker.
    }
    try {
      this.jarl?.dispose();
    } catch {
      // Ignore disposal failures on an already-terminated worker.
    }
    this.coordinator = null;
    this.neural = null;
    this.jarl = null;
  }

  getLastResult(): SearchResult | null {
    return this.result;
  }

  async storageUsage(): Promise<{ persistent: boolean; bytes: number }> {
    if (!this.cache) return { persistent: false, bytes: 0 };
    return { persistent: this.cache.persistent, bytes: await this.cache.totalBytes() };
  }
}

export async function modelFromCatalog(publicationId: string): Promise<ModelManifest> {
  const response = await fetch('/api/browser-analysis/models', {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { accept: 'application/json' },
  });
  if (!response.ok) throw classifyFetchFailure(response.status);
  const models = validateModelCatalog(await response.json());
  const model = models.find((candidate) => candidate.publicationId === publicationId);
  if (!model) {
    throw new BrowserAnalysisError('model-not-published', 'The model is not published');
  }
  return validateModelManifest(model);
}
