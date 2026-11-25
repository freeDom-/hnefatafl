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
 * - operation ownership across every asynchronous stage;
 * - account-context invalidation and bounded disposal.
 *
 * It never invokes server analysis as a substitute and never silently changes
 * search strength, algorithms or providers.
 */
import {
  BrowserAnalysisError,
  JARL_ALGORITHM,
  LIMITS,
  NEURAL_ALGORITHM,
  RUNTIME_JS,
  type AnalysisAlgorithm,
  type Availability,
  type BrowserAnalysisState,
  type DownloadProgress,
  type ModelDownloadResult,
  type ModelManifest,
  type ObservedPlacement,
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
  verifyRuntimeIdentity,
} from './manifests';
import { ArtifactCache, openArtifactStore, sha256Hex, type DigestFn } from './storage';

type AnyRecord = Record<string, any>;

interface LocalAnalysisClientLike {
  request(payload: unknown, options?: { timeoutMs?: number }): Promise<unknown>;
  cancel(reason?: string): void;
  dispose(): void;
}

interface NeuralModelIdentity {
  sha256: string;
  ruleset: string;
  size: number;
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
  loadModelBytes(
    bytes: ArrayBuffer,
    options: {
      provider?: ProviderRequest;
      size?: number;
      ruleset?: string;
      expectedSha256?: string;
    },
  ): Promise<AnyRecord>;
  modelIdentity(): NeuralModelIdentity;
  cancelWork?: (options?: { reason?: string }) => Promise<unknown>;
  dispose?(): void;
}

interface CoordinatorLike {
  search(request: AnyRecord): Promise<AnyRecord>;
  abort?(reason?: unknown): void;
  dispose(): void;
}

interface DisposableResource {
  dispose?: () => void;
}

interface Operation {
  id: number;
  context: number;
  runtimeId: string | null;
  controller: AbortController;
  active: boolean;
  resources: DisposableResource[];
}

export interface RuntimeOptions {
  fetchImpl?: typeof fetch;
  cache?: ArtifactCache;
  loadModule?: (url: string) => Promise<AnyRecord>;
  onStateChange?: (status: RuntimeStatus) => void;
  digestFn?: DigestFn;
  storageQuotaBytes?: number;
}

/** Production-equivalent Jarl configuration (alpha_beta catalog profile). */
export const JARL_SETTINGS: Record<string, unknown> = {
  algorithm: 'alpha_beta',
  depth: 5,
  time_limit: 2.0,
  heuristic: 'shield_maiden',
  search: {
    legacy: false,
    transposition: true,
    move_ordering: true,
    symmetry_transposition: false,
    symmetry_pruning: false,
    table_capacity: 100_000,
  },
};

const JARL_FIELDS = new Set(['depth', 'time_limit', 'seed', 'heuristic', 'search']);
const HEURISTICS = new Set(['shield_maiden', 'shield_bearer']);
const NEURAL_FIELDS = new Set(['iterations', 'time_limit', 'seed', 'exploration', 'temperature']);
const PLACEMENTS = new Set<ObservedPlacement>([
  'wasm',
  'webgpu-session-unverified',
  'webgpu-mixed',
  'cpu-fallback',
  'placement-unknown',
]);

const REQUIRED_CAPABILITIES: Array<[string, () => boolean]> = [
  ['WebAssembly', () => typeof WebAssembly === 'object'],
  ['Web Workers', () => typeof Worker === 'function'],
];

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

function isRecord(value: unknown): value is AnyRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asError(error: unknown, fallback: string): BrowserAnalysisError {
  if (error instanceof BrowserAnalysisError) return error;
  const message = (error as { message?: string })?.message ?? fallback;
  return new BrowserAnalysisError('search-failed', message, false);
}

function cancelled(message = 'Operation was cancelled'): BrowserAnalysisError {
  return new BrowserAnalysisError('cancelled', message, false);
}

function positiveInteger(value: unknown, maximum: number): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= maximum
    ? value
    : null;
}

function boundedNumber(value: unknown, low: number, high: number): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= low && value <= high
    ? value
    : null;
}

/** Reject unknown or invalid model-free settings; never fall back to baseline. */
export function normalizeJarlSettings(settings: Record<string, unknown> | undefined): AnyRecord {
  const provided = settings ?? {};
  for (const key of Object.keys(provided)) {
    if (!JARL_FIELDS.has(key)) {
      throw new BrowserAnalysisError('unsupported', `Unsupported Jarl setting: ${key}`);
    }
  }
  const effective: AnyRecord = { ...JARL_SETTINGS };
  if (provided.depth !== undefined) {
    const depth = positiveInteger(provided.depth, 5);
    if (depth === null || depth < 1) {
      throw new BrowserAnalysisError('unsupported', 'Jarl depth must be an integer in 1..5');
    }
    effective.depth = depth;
  }
  if (provided.time_limit !== undefined) {
    const seconds = boundedNumber(provided.time_limit, 0.01, LIMITS.maxClassicalBudgetSeconds);
    if (seconds === null) {
      throw new BrowserAnalysisError(
        'unsupported',
        `Jarl time limit must be ${0.01}..${LIMITS.maxClassicalBudgetSeconds} seconds`,
      );
    }
    effective.time_limit = seconds;
  }
  if (provided.seed !== undefined) {
    const seed = positiveInteger(provided.seed, 2 ** 32 - 1);
    if (seed === null) throw new BrowserAnalysisError('unsupported', 'Jarl seed is invalid');
    effective.seed = seed;
  }
  if (provided.heuristic !== undefined) {
    if (typeof provided.heuristic !== 'string' || !HEURISTICS.has(provided.heuristic)) {
      throw new BrowserAnalysisError('unsupported', 'Unsupported Jarl heuristic');
    }
    effective.heuristic = provided.heuristic;
  }
  if (provided.search !== undefined) {
    if (!isRecord(provided.search)) {
      throw new BrowserAnalysisError('unsupported', 'Jarl search settings must be an object');
    }
    effective.search = { ...(effective.search as AnyRecord), ...provided.search };
  }
  return effective;
}

/** Reject unknown or out-of-contract neural settings before any worker work. */
export function normalizeNeuralSettings(settings: Record<string, unknown> | undefined): AnyRecord {
  const provided = settings ?? {};
  for (const key of Object.keys(provided)) {
    if (!NEURAL_FIELDS.has(key)) {
      throw new BrowserAnalysisError('unsupported', `Unsupported neural setting: ${key}`);
    }
  }
  const effective: AnyRecord = {
    iterations: 128,
    time_limit: 1.0,
    seed: 7,
    exploration: 1.4,
  };
  if (provided.iterations !== undefined) {
    const iterations = positiveInteger(provided.iterations, LIMITS.maxSimulations);
    if (iterations === null || iterations < 1) {
      throw new BrowserAnalysisError('unsupported', 'Neural iterations must be 1..4096');
    }
    effective.iterations = iterations;
  }
  if (provided.time_limit !== undefined) {
    const seconds = boundedNumber(provided.time_limit, 0.05, LIMITS.maxNeuralBudgetSeconds);
    if (seconds === null) {
      throw new BrowserAnalysisError(
        'unsupported',
        `Neural time limit must be 0.05..${LIMITS.maxNeuralBudgetSeconds} seconds`,
      );
    }
    effective.time_limit = seconds;
  }
  if (provided.seed !== undefined) {
    const seed = positiveInteger(provided.seed, 2 ** 32 - 1);
    if (seed === null) throw new BrowserAnalysisError('unsupported', 'Neural seed is invalid');
    effective.seed = seed;
  }
  if (provided.exploration !== undefined) {
    const exploration = boundedNumber(provided.exploration, 0, 10);
    if (exploration === null) {
      throw new BrowserAnalysisError('unsupported', 'Neural exploration must be 0..10');
    }
    effective.exploration = exploration;
  }
  return effective;
}

export class BrowserAnalysisRuntime {
  private status: RuntimeStatus = {
    state: 'idle',
    runtimeId: null,
    engine: null,
    evidence: null,
    lastSettings: null,
    error: null,
  };
  private manifest: RuntimeManifest | null = null;
  private runtimeId: string | null = null;
  private enabled = false;
  private available = false;
  private context = 0;
  private operationSequence = 0;
  private operations = new Set<Operation>();
  private disposed = false;
  private initState: { promise: Promise<RuntimeStatus>; context: number } | null = null;
  private jarl: LocalAnalysisClientLike | null = null;
  private neural: NeuralClientLike | null = null;
  private coordinator: CoordinatorLike | null = null;
  private loadedModelDigest: string | null = null;
  private loadedProvider: ProviderRequest | null = null;
  private result: SearchResult | null = null;
  private cache: ArtifactCache | null;
  private downloads: DownloadManager;
  private moduleCache = new Map<string, AnyRecord>();
  private readonly loadModule: (url: string) => Promise<AnyRecord>;
  private readonly digestFn: DigestFn;
  private cleanupBarrier: Promise<void> = Promise.resolve();
  private activeSearch: { op: Operation; promise: Promise<SearchResult> } | null = null;

  constructor(private readonly options: RuntimeOptions = {}) {
    this.cache = options.cache ?? null;
    this.digestFn = options.digestFn ?? sha256Hex;
    this.downloads = new DownloadManager({
      fetchImpl: options.fetchImpl,
      digestFn: this.digestFn,
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
    return {
      ...this.status,
      evidence: this.status.evidence ? { ...this.status.evidence } : null,
      lastSettings: this.status.lastSettings ? { ...this.status.lastSettings } : null,
      error: this.status.error ? { ...this.status.error } : null,
    };
  }

  private get fetchImpl(): typeof fetch {
    return this.options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private setState(
    state: BrowserAnalysisState,
    patch: Partial<Omit<RuntimeStatus, 'state'>> = {},
  ): RuntimeStatus {
    this.status = { ...this.status, ...patch, state };
    this.options.onStateChange?.(this.getStatus());
    return this.getStatus();
  }

  private setStateIfOwned(
    op: Operation,
    state: BrowserAnalysisState,
    patch: Partial<Omit<RuntimeStatus, 'state'>> = {},
  ): void {
    if (this.owns(op)) this.setState(state, patch);
  }

  // ------------------------------------------------------------ operation ownership
  private begin(contextRuntimeId: string | null, signal?: AbortSignal): Operation {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    const op: Operation = {
      id: ++this.operationSequence,
      context: this.context,
      runtimeId: contextRuntimeId === null ? null : this.runtimeId,
      controller: new AbortController(),
      active: true,
      resources: [],
    };
    if (signal) {
      if (signal.aborted) op.controller.abort();
      else signal.addEventListener('abort', () => op.controller.abort(), { once: true });
    }
    this.operations.add(op);
    return op;
  }

  /** Capture ownership at admission; obsolete operations may not allocate. */
  private owns(op: Operation): boolean {
    return (
      !this.disposed &&
      op.active &&
      op.context === this.context &&
      (op.runtimeId === null || op.runtimeId === this.runtimeId)
    );
  }

  private assertOwned(op: Operation, message = 'Operation was superseded'): void {
    if (!this.owns(op)) throw cancelled(message);
    if (op.controller.signal.aborted) throw cancelled(message);
  }

  private track<T extends DisposableResource>(op: Operation, resource: T): T {
    op.resources.push(resource);
    return resource;
  }

  private commitResources(op: Operation): void {
    op.resources = [];
  }

  private disposeResources(op: Operation): void {
    const resources = op.resources;
    op.resources = [];
    for (const resource of resources) {
      try {
        resource.dispose?.();
      } catch {
        // Bounded disposal is owned by the validated client.
      }
    }
  }

  /** Finish successfully without aborting resources committed to the runtime. */
  private finish(op: Operation): void {
    op.active = false;
    this.operations.delete(op);
  }

  private abandon(op: Operation): void {
    op.active = false;
    op.controller.abort();
    this.operations.delete(op);
    this.disposeResources(op);
  }

  private abortOperations(predicate: (op: Operation) => boolean): void {
    for (const op of [...this.operations]) {
      if (predicate(op)) this.abandon(op);
    }
  }

  private async withTimeout<T>(
    promise: Promise<T>,
    ms: number,
    message: string,
    signal?: AbortSignal,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new BrowserAnalysisError('search-failed', message, true)),
        ms,
      );
      const onAbort = () => {
        clearTimeout(timer);
        reject(cancelled());
      };
      if (signal?.aborted) {
        clearTimeout(timer);
        reject(cancelled());
        return;
      }
      signal?.addEventListener('abort', onAbort, { once: true });
      promise.then(
        (value) => {
          clearTimeout(timer);
          signal?.removeEventListener('abort', onAbort);
          resolve(value);
        },
        (error) => {
          clearTimeout(timer);
          signal?.removeEventListener('abort', onAbort);
          reject(error);
        },
      );
    });
  }

  /** Let an individual caller cancel its own wait without cancelling shared work. */
  private awaitCaller<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
    if (!signal) return promise;
    if (signal.aborted) return Promise.reject(cancelled());
    return new Promise<T>((resolve, reject) => {
      const onAbort = () => reject(cancelled());
      signal.addEventListener('abort', onAbort, { once: true });
      promise.then(
        (value) => {
          signal.removeEventListener('abort', onAbort);
          resolve(value);
        },
        (error) => {
          signal.removeEventListener('abort', onAbort);
          reject(error);
        },
      );
    });
  }

  // ------------------------------------------------------------------ availability
  private async fetchJson(
    op: Operation,
    path: string,
    validate: (payload: unknown) => AnyRecord,
  ): Promise<AnyRecord> {
    let response: Response;
    try {
      response = await this.fetchImpl(path, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { accept: 'application/json' },
        signal: op.controller.signal,
      });
    } catch (error) {
      if (op.controller.signal.aborted) throw cancelled();
      throw error;
    }
    if (!response.ok) throw classifyFetchFailure(response.status);
    const payload = validate(await response.json());
    this.assertOwned(op, 'Availability response was superseded');
    return payload;
  }

  private async loadAvailability(op: Operation): Promise<Availability> {
    const payload = await this.fetchJson(
      op,
      '/api/browser-analysis/availability',
      (raw) => validateAvailability(raw) as unknown as AnyRecord,
    );
    const availability = payload as Availability;
    if (availability.runtime) {
      await verifyRuntimeIdentity(availability.runtime, this.digestFn);
      this.assertOwned(op, 'Runtime identity verification was superseded');
    }
    this.applyAvailability(availability);
    return availability;
  }

  /** Apply an availability result and invalidate obsolete runtime state. */
  private applyAvailability(payload: Availability): void {
    this.enabled = payload.enabled;
    if (!payload.enabled) {
      this.available = false;
      this.disposeRuntime('Browser analysis is disabled');
      this.setState('disabled', {
        runtimeId: null,
        engine: null,
        evidence: null,
        lastSettings: null,
        error: null,
      });
      return;
    }
    if (!payload.runtimeAvailable || !payload.runtime) {
      this.available = false;
      this.disposeRuntime(`Browser runtime is unavailable (${payload.reason ?? 'unknown'})`);
      this.setState('unavailable', {
        runtimeId: null,
        engine: null,
        evidence: null,
        lastSettings: null,
        error: {
          code: 'runtime-missing',
          message: `Browser runtime is unavailable (${payload.reason ?? 'unknown'})`,
          retryable: true,
        },
      });
      return;
    }
    this.available = true;
    const next = payload.runtime;
    if (next.runtimeId !== this.runtimeId) {
      // Runtime identity changed: no worker, module or model from the previous
      // identity may be reused or allowed to publish results.
      this.disposeRuntime('Runtime identity changed');
      this.manifest = next;
      this.runtimeId = next.runtimeId;
      this.setState('idle', {
        runtimeId: next.runtimeId,
        engine: null,
        evidence: null,
        lastSettings: null,
        error: null,
      });
    } else {
      this.manifest = next;
    }
  }

  async availability(options: { signal?: AbortSignal } = {}): Promise<Availability> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    const op = this.begin(null, options.signal);
    try {
      return await this.loadAvailability(op);
    } finally {
      this.finish(op);
    }
  }

  async listModels(options: { signal?: AbortSignal } = {}): Promise<ModelManifest[]> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    const op = this.begin(this.runtimeId, options.signal);
    try {
      const payload = await this.fetchJson(op, '/api/browser-analysis/models', (raw) => {
        if (!isRecord(raw) || raw.schema !== 1 || !Array.isArray(raw.models)) {
          throw new BrowserAnalysisError('model-integrity', 'Model catalog response is invalid');
        }
        return raw;
      });
      const models = validateModelCatalog(payload);
      const cursor = this.manifest;
      if (cursor) {
        for (const model of models) assertModelRuntimeCompatible(model, cursor);
      }
      this.assertOwned(op);
      return models;
    } finally {
      this.finish(op);
    }
  }

  // ------------------------------------------------------------------ initialization
  async initialize(
    options: { signal?: AbortSignal; skipProbe?: boolean } = {},
  ): Promise<RuntimeStatus> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    if (this.initState && this.initState.context === this.context) {
      return this.awaitCaller(this.initState.promise, options.signal);
    }
    const promise = this.initializeInternal(options);
    this.initState = { promise, context: this.context };
    promise.then(
      () => {
        if (this.initState?.promise === promise) this.initState = null;
      },
      () => {
        if (this.initState?.promise === promise) this.initState = null;
      },
    );
    return this.awaitCaller(promise, options.signal);
  }

  private async initializeInternal(options: {
    signal?: AbortSignal;
    skipProbe?: boolean;
  }): Promise<RuntimeStatus> {
    const op = this.begin(null, options.signal);
    try {
      const availability = await this.loadAvailability(op);
      if (!availability.enabled || !this.manifest) {
        return this.getStatus();
      }
      op.runtimeId = this.runtimeId;
      this.assertOwned(op);
      for (const [name, supported] of REQUIRED_CAPABILITIES) {
        if (!supported()) {
          throw new BrowserAnalysisError(
            'capability-missing',
            `${name} is not available in this browser`,
          );
        }
      }
      if (this.jarl && this.status.state === 'ready') {
        return this.getStatus();
      }
      await this.waitForCleanup(op);
      this.setStateIfOwned(op, 'downloading', {
        runtimeId: this.runtimeId,
        evidence: null,
        lastSettings: null,
        error: null,
      });
      await this.verifyRuntimeAssets(op);
      this.assertOwned(op);
      this.setStateIfOwned(op, 'initializing', { runtimeId: this.runtimeId });
      const analysisClient = await this.loadRuntimeModule(op, RUNTIME_JS.analysisClient);
      this.assertOwned(op);
      const jarl = this.track(
        op,
        new analysisClient.LocalAnalysisClient() as LocalAnalysisClientLike,
      );
      const onAbort = () => {
        try {
          jarl.cancel('cancelled');
        } catch {
          // Idle cancellation is a no-op in the validated client.
        }
      };
      op.controller.signal.addEventListener('abort', onAbort, { once: true });
      let info: AnyRecord = {};
      if (!options.skipProbe) {
        info = (await this.withTimeout(
          jarl.request({ action: 'info' }, { timeoutMs: LIMITS.maxJobTimeoutMs }),
          LIMITS.initTimeoutMs,
          'Runtime initialization timed out',
          op.controller.signal,
        )) as AnyRecord;
      }
      this.assertOwned(op);
      op.controller.signal.removeEventListener('abort', onAbort);
      this.commitResources(op);
      if (this.jarl && this.jarl !== jarl) {
        try {
          this.jarl.dispose();
        } catch {
          // Replaced worker disposal is best effort.
        }
      }
      this.jarl = jarl;
      this.setStateIfOwned(op, 'ready', {
        runtimeId: this.runtimeId,
        engine: (info?.info as AnyRecord) ?? info ?? null,
        evidence: null,
        lastSettings: null,
        error: null,
      });
      return this.getStatus();
    } catch (error) {
      this.disposeResources(op);
      if (this.owns(op)) {
        if (op.controller.signal.aborted) {
          this.setState('cancelled', { error: null });
          throw cancelled();
        }
        const failure = asError(error, 'Runtime initialization failed');
        this.setState('error', {
          error: { code: failure.code, message: failure.message, retryable: failure.retryable },
        });
        throw failure;
      }
      throw cancelled('Initialization was superseded');
    } finally {
      this.finish(op);
    }
  }

  private async verifyRuntimeAssets(op: Operation): Promise<void> {
    const manifest = this.manifest;
    if (!manifest) throw new BrowserAnalysisError('runtime-missing', 'Runtime manifest is missing');
    for (const file of manifest.files) {
      this.assertOwned(op, 'Runtime download was superseded');
      const spec: DownloadSpec = {
        key: `runtime:${manifest.runtimeId}:${file.path}`,
        url: runtimeAssetUrl(manifest, file.path),
        expectedBytes: file.bytes,
        sha256: file.sha256,
        cacheable: false,
        immutable: true,
      };
      await this.downloads.download(spec, { signal: op.controller.signal });
    }
    this.assertOwned(op, 'Runtime download was superseded');
  }

  private async loadRuntimeModule(op: Operation, name: string): Promise<AnyRecord> {
    const runtime = this.runtimeId;
    const manifest = this.manifest;
    if (!runtime || !manifest) {
      throw new BrowserAnalysisError('runtime-missing', 'Runtime manifest is missing');
    }
    const key = `${runtime}:${name}`;
    const cached = this.moduleCache.get(key);
    if (cached) return cached;
    const module = await this.loadModule(runtimeAssetUrl(manifest, name));
    this.assertOwned(op, 'Runtime module import was superseded');
    // Bind the browser module cache to the immutable runtime identity.
    this.moduleCache.set(key, module);
    return module;
  }

  private waitForCleanup(op: Operation): Promise<void> {
    return this.withTimeout(
      this.cleanupBarrier,
      LIMITS.shutdownTimeoutMs,
      'Previous runtime cleanup did not finish',
      op.controller.signal,
    );
  }

  // ---------------------------------------------------------------------- downloads
  /** Re-check current publication authorization without inferring it from cache. */
  private async revalidateModel(model: ModelManifest, op: Operation): Promise<void> {
    const payload = await this.fetchJson(
      op,
      `/api/browser-analysis/models/${model.publicationId}`,
      (raw) => raw as AnyRecord,
    );
    const current = validateModelManifest(payload);
    if (
      current.onnxSha256 !== model.onnxSha256 ||
      current.publicationId !== model.publicationId ||
      current.status !== 'published'
    ) {
      throw new BrowserAnalysisError(
        'model-not-published',
        'The model publication changed; refresh the published model list',
      );
    }
  }

  private modelSpec(model: ModelManifest): DownloadSpec {
    return {
      key: `model:${model.onnxSha256}`,
      url: modelArtifactUrl(model),
      expectedBytes: model.artifactBytes,
      sha256: model.onnxSha256,
      publicationId: model.publicationId,
      displayName: model.displayName,
    };
  }

  async downloadModel(
    model: ModelManifest,
    hooks: { onProgress?: (progress: DownloadProgress) => void; signal?: AbortSignal } = {},
  ): Promise<ModelDownloadResult> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    if (!this.manifest) {
      throw new BrowserAnalysisError('not-initialized', 'The runtime manifest is not loaded');
    }
    assertModelRuntimeCompatible(model, this.manifest);
    const op = this.begin(this.runtimeId, hooks.signal);
    try {
      await this.revalidateModel(model, op);
      this.assertOwned(op);
      const result = await this.downloads.download(this.modelSpec(model), {
        signal: op.controller.signal,
        onProgress: hooks.onProgress,
      });
      this.assertOwned(op, 'Model download was superseded');
      return {
        sha256: result.sha256,
        bytes: result.bytes.byteLength,
        fromCache: result.fromCache,
        retained: result.retained ?? false,
        persistent: result.persistent ?? false,
      };
    } finally {
      this.finish(op);
    }
  }

  /** Return verified model bytes from the cache or one bounded download. */
  private async ensureModelBytes(model: ModelManifest, op: Operation): Promise<ArrayBuffer> {
    if (this.cache) {
      const cached = await this.cache.getVerified(
        `model:${model.onnxSha256}`,
        model.onnxSha256,
        this.digestFn,
        model.artifactBytes,
      );
      this.assertOwned(op, 'Cached model load was superseded');
      if (cached) return cached;
    }
    const result = await this.downloads.download(this.modelSpec(model), {
      signal: op.controller.signal,
    });
    this.assertOwned(op, 'Model download was superseded');
    return result.bytes;
  }

  // -------------------------------------------------------------------------- search
  async runSearch(
    request: SearchRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<SearchResult> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    if (!this.enabled) {
      throw new BrowserAnalysisError('disabled', 'Browser analysis is disabled');
    }
    if (!this.available || !this.manifest || !this.runtimeId) {
      throw new BrowserAnalysisError('runtime-missing', 'Browser runtime is unavailable');
    }
    if (!this.jarl) {
      throw new BrowserAnalysisError('not-initialized', 'Initialize the runtime first');
    }
    const algorithm = request.algorithm;
    if (algorithm !== JARL_ALGORITHM && algorithm !== NEURAL_ALGORITHM) {
      throw new BrowserAnalysisError('unsupported', 'Unsupported analysis algorithm');
    }
    if (algorithm === JARL_ALGORITHM && request.model) {
      throw new BrowserAnalysisError('unsupported', 'Jarl does not accept a neural model');
    }
    if (algorithm === JARL_ALGORITHM && request.provider) {
      throw new BrowserAnalysisError('unsupported', 'Jarl has no inference provider');
    }
    if (algorithm === NEURAL_ALGORITHM && !request.model) {
      throw new BrowserAnalysisError('model-not-published', 'Neural MCTS requires a model');
    }
    if (this.activeSearch) {
      throw new BrowserAnalysisError('superseded', 'Another search is already active');
    }
    const op = this.begin(this.runtimeId, options.signal);
    const promise = this.executeSearch(op, request, algorithm);
    this.activeSearch = { op, promise };
    promise.then(
      () => {
        if (this.activeSearch?.promise === promise) this.activeSearch = null;
      },
      () => {
        if (this.activeSearch?.promise === promise) this.activeSearch = null;
      },
    );
    return promise;
  }

  private async executeSearch(
    op: Operation,
    request: SearchRequest,
    algorithm: AnalysisAlgorithm,
  ): Promise<SearchResult> {
    const started = now();
    const model = request.model ?? null;
    try {
      await this.waitForCleanup(op);
      this.assertOwned(op);
      this.result = null;
      this.setStateIfOwned(op, 'searching', {
        evidence: null,
        lastSettings: null,
        error: null,
      });
      let result: SearchResult;
      if (algorithm === JARL_ALGORITHM) {
        result = await this.runJarl(op, request, started);
      } else {
        result = await this.runNeural(op, request, model as ModelManifest, started);
      }
      this.assertOwned(op, 'Search result was superseded');
      this.result = result;
      this.setStateIfOwned(op, 'ready', {
        evidence: result.evidence,
        lastSettings: result.settings,
        error: null,
      });
      return result;
    } catch (error) {
      const failure =
        error instanceof BrowserAnalysisError ? error : asError(error, 'Search failed');
      if (this.owns(op) && (failure.code === 'cancelled' || op.controller.signal.aborted)) {
        this.setState('cancelled', { error: null });
        throw cancelled();
      }
      if (this.owns(op)) {
        this.setState('error', {
          error: { code: failure.code, message: failure.message, retryable: failure.retryable },
        });
      }
      throw failure;
    } finally {
      this.disposeResources(op);
      this.finish(op);
    }
  }

  private async runJarl(
    op: Operation,
    request: SearchRequest,
    started: number,
  ): Promise<SearchResult> {
    const client = this.jarl;
    if (!client) throw new BrowserAnalysisError('not-initialized', 'Runtime is not initialized');
    const effective = normalizeJarlSettings(request.settings);
    const watchdog = Math.min(
      LIMITS.maxJobTimeoutMs,
      Math.max(1000, Math.round(Number(effective.time_limit) * 1000) + 2000),
    );
    const response = (await this.withTimeout(
      client.request(
        { action: 'analysis', state: request.state, config: effective },
        { timeoutMs: watchdog },
      ),
      watchdog,
      'Jarl search exceeded its watchdog',
      op.controller.signal,
    )) as AnyRecord;
    this.assertOwned(op, 'Jarl result was superseded');
    if (!isRecord(response) || response.ok !== true) {
      throw new BrowserAnalysisError(
        'search-failed',
        String((response as AnyRecord)?.message ?? 'Local analysis failed'),
      );
    }
    const event = response.event;
    if (!isRecord(event) || !isRecord(event.analysis) || !isRecord(event.settings)) {
      throw new BrowserAnalysisError('search-failed', 'Jarl result contract is malformed');
    }
    return {
      analysis: event.analysis as Record<string, unknown>,
      algorithm: JARL_ALGORITHM,
      settings: event.settings as Record<string, unknown>,
      evidence: {
        executionRuntime: 'pyodide',
        requestedProvider: null,
        observedPlacement: 'none',
        gpuExecutionVerified: false,
        inferenceObserved: false,
        inferences: 0,
        completedSimulations: null,
        completionReason: null,
      },
      model: null,
      positionIdentity: typeof event.position_key === 'string' ? event.position_key : null,
      searchId: null,
      elapsedMs: now() - started,
      authoritative: false,
    };
  }

  private async runNeural(
    op: Operation,
    request: SearchRequest,
    model: ModelManifest,
    started: number,
  ): Promise<SearchResult> {
    const manifest = this.manifest;
    if (!manifest) throw new BrowserAnalysisError('runtime-missing', 'Runtime manifest is missing');
    assertModelRuntimeCompatible(model, manifest);
    const provider: ProviderRequest = request.provider ?? 'wasm';
    if (provider !== 'wasm' && provider !== 'webgpu') {
      throw new BrowserAnalysisError('unsupported', 'Unsupported inference provider');
    }
    if (
      provider === 'webgpu' &&
      typeof navigator !== 'undefined' &&
      !(navigator as { gpu?: unknown }).gpu
    ) {
      throw new BrowserAnalysisError(
        'capability-missing',
        'WebGPU is not available in this browser',
      );
    }
    const effective = normalizeNeuralSettings(request.settings);
    await this.revalidateModel(model, op);
    this.assertOwned(op);
    const bytes = await this.ensureModelBytes(model, op);
    if (!this.neural) {
      const neuralModule = await this.loadRuntimeModule(op, RUNTIME_JS.neuralClient);
      this.assertOwned(op);
      const workerUrl = runtimeAssetUrl(manifest, RUNTIME_JS.neuralWorker);
      const neural = this.track(
        op,
        new neuralModule.NeuralClient({ workerUrl }) as NeuralClientLike,
      );
      this.commitResources(op);
      this.neural = neural;
    }
    if (!this.coordinator) {
      const searchModule = await this.loadRuntimeModule(op, RUNTIME_JS.searchClient);
      this.assertOwned(op);
      const coordinator = this.track(
        op,
        new searchModule.NeuralSearchCoordinator({ neural: this.neural }) as CoordinatorLike,
      );
      this.commitResources(op);
      this.coordinator = coordinator;
    }
    const neural = this.neural;
    const coordinator = this.coordinator;
    if (!neural || !coordinator) {
      throw new BrowserAnalysisError('not-initialized', 'Neural runtime is unavailable');
    }
    if (this.loadedModelDigest !== model.onnxSha256 || this.loadedProvider !== provider) {
      const loadAbort = () => {
        try {
          coordinator.abort?.(new Error('Model load cancelled'));
        } catch {
          // Bounded cleanup is owned by the validated clients.
        }
        void neural.cancelWork?.({ reason: 'Model load cancelled' }).catch(() => undefined);
      };
      op.controller.signal.addEventListener('abort', loadAbort, { once: true });
      try {
        const loaded = (await neural.loadModelBytes(bytes, {
          provider,
          size: model.boardSize,
          ruleset: model.ruleset,
          expectedSha256: model.onnxSha256,
        })) as AnyRecord;
        this.assertOwned(op, 'Model initialization was superseded');
        const identity = neural.modelIdentity();
        if (
          loaded?.model_sha256 !== model.onnxSha256 ||
          loaded?.model_ruleset !== model.ruleset ||
          loaded?.model_bytes !== model.artifactBytes ||
          identity.sha256 !== model.onnxSha256 ||
          identity.ruleset !== model.ruleset ||
          identity.size !== model.boardSize
        ) {
          throw new BrowserAnalysisError(
            'model-integrity',
            'Loaded model identity does not match the published artifact',
          );
        }
        if (loaded?.provider_requested !== provider) {
          throw new BrowserAnalysisError(
            'model-integrity',
            'Model session provider does not match the request',
          );
        }
        this.loadedModelDigest = model.onnxSha256;
        this.loadedProvider = provider;
      } finally {
        op.controller.signal.removeEventListener('abort', loadAbort);
      }
    }
    const identity = neural.modelIdentity();
    if (
      identity.sha256 !== model.onnxSha256 ||
      identity.ruleset !== model.ruleset ||
      identity.size !== model.boardSize
    ) {
      throw new BrowserAnalysisError('model-integrity', 'Loaded model identity changed');
    }
    const abortCoordinator = () => {
      try {
        coordinator.abort?.(new Error('Search cancelled'));
      } catch {
        // The validated coordinator owns bounded shutdown recovery.
      }
    };
    op.controller.signal.addEventListener('abort', abortCoordinator, { once: true });
    let raw: AnyRecord;
    try {
      raw = (await coordinator.search({
        state: request.state,
        settings: effective,
        model: identity,
        provider: { requested: provider },
        trace: request.trace === true,
      })) as AnyRecord;
    } finally {
      op.controller.signal.removeEventListener('abort', abortCoordinator);
    }
    this.assertOwned(op, 'Neural search result was superseded');
    return this.finalizeNeural(raw, model, provider, effective, started);
  }

  private finalizeNeural(
    raw: AnyRecord,
    model: ModelManifest,
    provider: ProviderRequest,
    requestedSettings: AnyRecord,
    started: number,
  ): SearchResult {
    if (!isRecord(raw) || !isRecord(raw.analysis) || !isRecord(raw.search)) {
      throw new BrowserAnalysisError('search-failed', 'Neural search result contract is malformed');
    }
    const search = raw.search;
    const coordinator = isRecord(search.coordinator) ? search.coordinator : null;
    const inferences = coordinator ? positiveInteger(coordinator.inferences, 1_000_000) : null;
    const completed = coordinator
      ? positiveInteger(coordinator.completed_simulations, 1_000_000)
      : null;
    if (inferences === null || completed === null) {
      throw new BrowserAnalysisError(
        'search-failed',
        'Neural search telemetry is missing or malformed',
      );
    }
    const reported = search.provider;
    let observed: ObservedPlacement = 'none';
    let gpuVerified = false;
    if (inferences > 0) {
      if (!isRecord(reported)) {
        throw new BrowserAnalysisError(
          'search-failed',
          'Neural inference completed without provider evidence',
        );
      }
      if (reported.requested !== provider) {
        throw new BrowserAnalysisError(
          'search-failed',
          'Provider evidence contradicts the requested provider',
        );
      }
      const placement = reported.execution_placement;
      if (typeof placement !== 'string' || !PLACEMENTS.has(placement as ObservedPlacement)) {
        throw new BrowserAnalysisError('search-failed', 'Provider placement is malformed');
      }
      if (provider === 'wasm' && String(placement).startsWith('webgpu')) {
        throw new BrowserAnalysisError(
          'search-failed',
          'Provider evidence reports GPU placement for a WASM request',
        );
      }
      if (typeof reported.gpu_execution_verified !== 'boolean') {
        throw new BrowserAnalysisError(
          'search-failed',
          'Provider GPU verification evidence is malformed',
        );
      }
      observed = placement as ObservedPlacement;
      gpuVerified = reported.gpu_execution_verified;
    } else if (reported !== null && reported !== undefined) {
      throw new BrowserAnalysisError(
        'search-failed',
        'Provider evidence is present for a search without inference',
      );
    }
    const effective = isRecord(raw.settings) ? (raw.settings as AnyRecord) : requestedSettings;
    const completionReason =
      typeof search.completion_reason === 'string' ? search.completion_reason : null;
    return {
      analysis: raw.analysis as Record<string, unknown>,
      algorithm: NEURAL_ALGORITHM,
      settings: effective,
      evidence: {
        executionRuntime: 'pyodide',
        requestedProvider: provider,
        observedPlacement: observed,
        gpuExecutionVerified: gpuVerified,
        inferenceObserved: inferences > 0,
        inferences,
        completedSimulations: completed,
        completionReason,
      },
      model: { sha256: model.onnxSha256, ruleset: model.ruleset, size: model.boardSize },
      positionIdentity:
        typeof raw.analysis.position_key === 'string' ? raw.analysis.position_key : null,
      searchId: typeof search.search_id === 'string' ? search.search_id : null,
      elapsedMs: now() - started,
      authoritative: false,
    };
  }

  // ------------------------------------------------------------ cancellation & lifecycle
  /** Cooperative cancellation of the active search/initialization and inference work. */
  async cancel(reason = 'Search cancelled'): Promise<void> {
    const search = this.activeSearch;
    if (search) {
      search.op.active = false;
      search.op.controller.abort();
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
    }
    this.abortOperations((op) => op !== search?.op);
    const tasks: Promise<unknown>[] = [];
    if (this.neural?.cancelWork) {
      tasks.push(this.neural.cancelWork({ reason }).catch(() => undefined));
    }
    if (search) tasks.push(search.promise.catch(() => undefined));
    const barrier = Promise.allSettled(tasks).then(() => undefined);
    this.cleanupBarrier = barrier;
    await this.withTimeout(
      barrier,
      LIMITS.shutdownTimeoutMs,
      'Cancellation cleanup did not finish',
    ).catch(() => undefined);
    if (this.status.state === 'searching' || this.status.state === 'initializing') {
      this.setState('cancelled', { error: null });
    }
  }

  private disposeClients(): void {
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
    this.loadedModelDigest = null;
    this.loadedProvider = null;
  }

  /** Drop every runtime-bound worker, module and result without changing the account. */
  private disposeRuntime(message: string): void {
    this.abortOperations((op) => op.runtimeId !== null);
    this.disposeClients();
    this.moduleCache.clear();
    this.manifest = null;
    this.runtimeId = null;
    this.result = null;
    this.status = {
      ...this.status,
      runtimeId: null,
      engine: null,
      evidence: null,
      lastSettings: null,
    };
    void message;
  }

  /**
   * Invalidate all work bound to the previous account context. Published model
   * artifacts are public shared assets and may be retained; private replay data,
   * positions and results are never stored here. Delayed completions cannot
   * restore the previous account's result because the context generation changes.
   */
  async invalidateAccountContext(): Promise<void> {
    this.context += 1;
    this.abortOperations(() => true);
    this.downloads.cancelAll('Account context changed');
    const search = this.activeSearch;
    this.activeSearch = null;
    const tasks: Promise<unknown>[] = [];
    if (search) {
      search.op.active = false;
      search.op.controller.abort();
      try {
        this.coordinator?.abort?.(new Error('Account context changed'));
        this.jarl?.cancel('Account context changed');
      } catch {
        // Bounded cleanup is owned by the validated clients.
      }
      if (this.neural?.cancelWork) {
        tasks.push(
          this.neural.cancelWork({ reason: 'Account context changed' }).catch(() => undefined),
        );
      }
      tasks.push(search.promise.catch(() => undefined));
    }
    const barrier = Promise.allSettled(tasks).then(() => undefined);
    this.cleanupBarrier = barrier;
    await this.withTimeout(
      barrier,
      LIMITS.shutdownTimeoutMs,
      'Account-context cleanup did not finish',
    ).catch(() => undefined);
    this.disposeClients();
    this.result = null;
    this.setState('idle', {
      runtimeId: this.runtimeId,
      engine: null,
      evidence: null,
      lastSettings: null,
      error: null,
    });
  }

  /** Release workers and abort transfers; the runtime object becomes reusable. */
  async dispose(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    this.context += 1;
    this.abortOperations(() => true);
    this.downloads.cancelAll('Runtime disposed');
    const search = this.activeSearch;
    this.activeSearch = null;
    if (search) {
      search.op.controller.abort();
      try {
        this.coordinator?.abort?.(new Error('Runtime disposed'));
        this.jarl?.cancel('Runtime disposed');
      } catch {
        // Bounded cleanup is owned by the validated clients.
      }
    }
    this.disposeClients();
    this.moduleCache.clear();
    this.manifest = null;
    this.runtimeId = null;
    this.enabled = false;
    this.available = false;
    this.result = null;
    this.initState = null;
    this.setState('idle', {
      runtimeId: null,
      engine: null,
      evidence: null,
      lastSettings: null,
      error: null,
    });
  }

  getLastResult(): SearchResult | null {
    return this.result;
  }

  async storageUsage(): Promise<{ persistent: boolean; bytes: number }> {
    if (!this.cache) return { persistent: false, bytes: 0 };
    return { persistent: this.cache.persistent, bytes: await this.cache.totalBytes() };
  }
}

export async function modelFromCatalog(
  publicationId: string,
  options: { fetchImpl?: typeof fetch; signal?: AbortSignal } = {},
): Promise<ModelManifest> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const response = await fetchImpl('/api/browser-analysis/models', {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { accept: 'application/json' },
    signal: options.signal,
  });
  if (!response.ok) throw classifyFetchFailure(response.status);
  const models = validateModelCatalog(await response.json());
  const model = models.find((candidate) => candidate.publicationId === publicationId);
  if (!model) {
    throw new BrowserAnalysisError('model-not-published', 'The model is not published');
  }
  return validateModelManifest(model);
}
