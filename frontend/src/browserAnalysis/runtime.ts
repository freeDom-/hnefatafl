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
  type RuntimeDisposalOutcome,
  type RuntimeManifest,
  type RuntimeStatus,
  type SearchRequest,
  type SearchResult,
} from './contracts';
import { DownloadManager, type DownloadSpec } from './downloads';
import { searchIdentity } from './canonical';
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

/** The public Phase 5 contract may own at most one runtime instance per tab. */
let managedRuntimeInstances = 0;

interface LocalAnalysisClientLike {
  load?(): Promise<unknown>;
  request(payload: unknown, options?: { timeoutMs?: number; ceilingMs?: number }): Promise<unknown>;
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
  /** Actual live worker session identity, or null when no session is loaded. */
  sessionIdentity?(): (NeuralModelIdentity & { provider: ProviderRequest }) | null;
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
  detachSignal?: () => void;
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
    effective.search = {
      ...(effective.search as AnyRecord),
      ...validateJarlSearchConfig(provided.search),
    };
  }
  return effective;
}

const JARL_SEARCH_FIELDS = new Set([
  'legacy',
  'transposition',
  'move_ordering',
  'symmetry_transposition',
  'symmetry_pruning',
  'table_capacity',
]);
const MAX_TABLE_CAPACITY = 1_000_000;
const NEURAL_COMPLETION_REASONS = new Set([
  'immediate_win',
  'deadline',
  'simulations',
  'insufficient_budget',
  'completed',
]);

/** Strictly validate the nested Jarl alpha-beta search configuration. */
export function validateJarlSearchConfig(raw: unknown): AnyRecord {
  if (!isRecord(raw)) {
    throw new BrowserAnalysisError('unsupported', 'Jarl search settings must be an object');
  }
  for (const key of Object.keys(raw)) {
    if (!JARL_SEARCH_FIELDS.has(key)) {
      throw new BrowserAnalysisError('unsupported', `Unsupported Jarl search setting: ${key}`);
    }
  }
  const result: AnyRecord = {};
  for (const key of JARL_SEARCH_FIELDS) {
    if (key === 'table_capacity') continue;
    if (raw[key] !== undefined) {
      if (typeof raw[key] !== 'boolean') {
        throw new BrowserAnalysisError('unsupported', `Jarl search ${key} must be a boolean`);
      }
      result[key] = raw[key];
    }
  }
  if (raw.table_capacity !== undefined) {
    const capacity = positiveInteger(raw.table_capacity, MAX_TABLE_CAPACITY);
    if (capacity === null || capacity < 1) {
      throw new BrowserAnalysisError(
        'unsupported',
        'Jarl search table_capacity must be 1..1000000',
      );
    }
    result.table_capacity = capacity;
  }
  if (result.symmetry_transposition === true) result.transposition = true;
  return result;
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
  if (provided.temperature !== undefined) {
    // Deterministic analysis always uses temperature zero. Accepting a nonzero
    // value and silently ignoring it would misreport the effective configuration.
    if (provided.temperature !== 0) {
      throw new BrowserAnalysisError(
        'unsupported',
        'Neural temperature is fixed at 0 for deterministic analysis',
      );
    }
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
  private managedInstance = false;
  private managedReleased = false;
  private disposalPromise: Promise<RuntimeDisposalOutcome> | null = null;
  private disposeOutcome: RuntimeDisposalOutcome | null = null;
  private disposed = false;
  private initState: { promise: Promise<RuntimeStatus>; context: number } | null = null;
  private jarl: LocalAnalysisClientLike | null = null;
  private neural: NeuralClientLike | null = null;
  private coordinator: CoordinatorLike | null = null;
  private result: SearchResult | null = null;
  private cache: ArtifactCache | null;
  private downloads: DownloadManager;
  private moduleCache = new Map<string, AnyRecord>();
  private readonly loadModule: (url: string) => Promise<AnyRecord>;
  private readonly digestFn: DigestFn;
  private cleanupBarrier: Promise<void> = Promise.resolve();
  private availabilitySequence = 0;
  private availabilityApplied = 0;
  private retirements = new Map<
    number,
    {
      clients: {
        jarl: LocalAnalysisClientLike | null;
        neural: NeuralClientLike | null;
        coordinator: CoordinatorLike | null;
      };
      context: number;
    }
  >();
  private retirementSequence = 0;
  private terminatedClients = new WeakSet<object>();
  private activeSearch: { op: Operation; promise: Promise<SearchResult> } | null = null;
  private offlineLease: { accountId: string; until: number; runtimeId: string } | null = null;
  private modelAuthorizations = new Map<string, number>();

  private modelAuthKey(model: ModelManifest): string {
    return `${model.publicationId}:${model.onnxSha256}`;
  }

  /** Latest genuine online publication authorization for this runtime identity. */
  modelAuthorization(model: ModelManifest) {
    const checkedAt = this.modelAuthorizations.get(this.modelAuthKey(model));
    if (checkedAt === undefined || !this.runtimeId) return null;
    return {
      sha256: model.onnxSha256,
      publicationId: model.publicationId,
      runtimeId: this.runtimeId,
      checkedAt,
    };
  }

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
    if (managedRuntimeInstances >= 1) {
      throw new BrowserAnalysisError(
        'quota-exceeded',
        'A browser-analysis runtime is already active in this tab; dispose it first',
        true,
      );
    }
    managedRuntimeInstances += 1;
    try {
      let cache = options.cache ?? null;
      if (!cache) {
        const store = await openArtifactStore();
        cache = new ArtifactCache(store, {
          maxBytes: options.storageQuotaBytes ?? LIMITS.maxStoredModelBytes,
        });
      }
      const runtime = new BrowserAnalysisRuntime({ ...options, cache });
      runtime.managedInstance = true;
      return runtime;
    } catch (error) {
      managedRuntimeInstances -= 1;
      throw error;
    }
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
      if (signal.aborted) {
        op.controller.abort();
      } else {
        // Store the forwarding listener so it can be removed on every terminal
        // path; long-lived caller signals must not retain completed operations.
        const forward = () => op.controller.abort();
        signal.addEventListener('abort', forward, { once: true });
        op.detachSignal = () => signal.removeEventListener('abort', forward);
      }
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
    op.detachSignal?.();
    op.detachSignal = undefined;
    this.operations.delete(op);
  }

  private abandon(op: Operation): void {
    op.active = false;
    op.controller.abort();
    op.detachSignal?.();
    op.detachSignal = undefined;
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
      if (error instanceof TypeError) {
        throw new BrowserAnalysisError(
          'network-unavailable',
          'The analysis origin is unreachable',
          true,
        );
      }
      throw error;
    }
    if (!response.ok) throw classifyFetchFailure(response.status);
    const payload = validate(await response.json());
    this.assertOwned(op, 'Availability response was superseded');
    return payload;
  }

  private async loadAvailability(op: Operation): Promise<Availability> {
    const sequence = ++this.availabilitySequence;
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
    // Out-of-order responses must never replace a newer applied state.
    if (sequence > this.availabilityApplied) {
      this.applyAvailability(availability);
      this.availabilityApplied = sequence;
    }
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
    // A ready/searching compatible runtime is already initialized; return the
    // current state rather than a stale completed initialization result.
    if (
      this.jarl &&
      this.manifest &&
      (this.status.state === 'ready' || this.status.state === 'searching')
    ) {
      return this.getStatus();
    }
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
    // An active compatible runtime is already initialized. In particular, do not
    // interrupt an active search or create a duplicate worker just because another
    // consumer asked for initialization.
    if (
      this.jarl &&
      this.manifest &&
      (this.status.state === 'ready' || this.status.state === 'searching')
    ) {
      return this.getStatus();
    }
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
    // Evidence belongs to the current online authorization attempt. A failed or
    // unreachable revalidation must not leave an older authorization renewable.
    this.modelAuthorizations.delete(this.modelAuthKey(model));
    let payload: AnyRecord;
    try {
      payload = await this.fetchJson(
        op,
        `/api/browser-analysis/models/${model.publicationId}`,
        (raw) => raw as AnyRecord,
      );
    } catch (error) {
      if (error instanceof BrowserAnalysisError && error.code === 'runtime-missing') {
        throw new BrowserAnalysisError(
          'model-not-published',
          'The model publication is no longer available',
        );
      }
      throw error;
    }
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
    this.assertOwned(op);
    this.modelAuthorizations.set(this.modelAuthKey(model), now());
  }

  private modelSpec(model: ModelManifest): DownloadSpec {
    return {
      key: `model:${model.onnxSha256}`,
      url: `${modelArtifactUrl(model)}?version=${model.onnxSha256}`,
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
    // Verification and any required transfer share the download manager's
    // bounded, tracked cache path. There is exactly one cache lookup and late
    // cache work remains accounted until it settles.
    const result = await this.downloads.download(this.modelSpec(model), {
      signal: op.controller.signal,
    });
    this.assertOwned(op, 'Model byte acquisition was superseded');
    return result.bytes;
  }

  // -------------------------------------------------------------------------- search
  async runSearch(
    request: SearchRequest,
    options: { signal?: AbortSignal; offline?: { accountId: string } } = {},
  ): Promise<SearchResult> {
    if (this.disposed) throw new BrowserAnalysisError('unsupported', 'Runtime is disposed');
    if (!this.enabled) {
      throw new BrowserAnalysisError('disabled', 'Browser analysis is disabled');
    }
    if (!this.available || !this.manifest || !this.runtimeId) {
      throw new BrowserAnalysisError('runtime-missing', 'Browser runtime is unavailable');
    }
    const offline = options.offline ?? null;
    if (offline) this.assertOfflineLease(offline.accountId);
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
    const promise = this.executeSearch(op, request, algorithm, offline !== null);
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
    offline: boolean,
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
        result = await this.runNeural(op, request, model as ModelManifest, started, offline);
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
      LIMITS.maxClassicalJobTimeoutMs,
      Math.max(1000, Math.round(Number(effective.time_limit) * 1000) + 2000),
    );
    const cancelJarl = () => {
      try {
        // Propagate cancellation to the actual active worker request, not only
        // to the surrounding promise. The validated client owns bounded recovery.
        client.cancel('Jarl search cancelled');
      } catch {
        // Idle cancellation is a no-op in the validated client.
      }
    };
    op.controller.signal.addEventListener('abort', cancelJarl, { once: true });
    let response: AnyRecord;
    try {
      // Exclude cold worker/Pyodide initialization from the search watchdog. A
      // cancellation may have released the worker, and cold start is bounded by
      // the separate initialization deadline.
      if (client.load) {
        await this.withTimeout(
          client.load(),
          LIMITS.initTimeoutMs,
          'Jarl runtime initialization timed out',
          op.controller.signal,
        );
      }
      response = (await this.withTimeout(
        client.request(
          { action: 'analysis', state: request.state, config: effective },
          { timeoutMs: watchdog, ceilingMs: LIMITS.maxClassicalJobTimeoutMs },
        ),
        watchdog,
        'Jarl search exceeded its watchdog',
        op.controller.signal,
      )) as AnyRecord;
    } catch (error) {
      // A watchdog expiry also cancels the underlying request; otherwise the
      // worker would keep searching after the manager reported failure.
      cancelJarl();
      throw error;
    } finally {
      op.controller.signal.removeEventListener('abort', cancelJarl);
    }
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
    const effectiveSettings = this.validateJarlEffective(effective, event.settings);
    return {
      analysis: event.analysis as Record<string, unknown>,
      algorithm: JARL_ALGORITHM,
      settings: effectiveSettings,
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
    offline = false,
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
    if (!offline) {
      await this.revalidateModel(model, op);
      this.assertOwned(op);
    }
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
    // Reconcile against the actual live session. Remembered manager metadata is
    // never sufficient to skip initialization after a failed replacement, model
    // load cancellation or worker failure.
    const current = neural.sessionIdentity?.() ?? null;
    const healthy =
      current !== null &&
      current.sha256 === model.onnxSha256 &&
      current.ruleset === model.ruleset &&
      current.size === model.boardSize &&
      current.provider === provider;
    if (offline && !this.modelAuthorizations.has(this.modelAuthKey(model))) {
      throw new BrowserAnalysisError(
        'unauthorized',
        'The requested model has no offline authorization',
        false,
      );
    }
    if (!healthy && offline) {
      // Warm offline execution may use only resources that are already
      // initialized. Reconstructing a model session from storage (or worse, the
      // network) is outside the Phase 5 warm-offline contract.
      throw new BrowserAnalysisError(
        'not-initialized',
        'The requested model session is not initialized for offline analysis',
        false,
      );
    }
    if (!healthy) {
      const bytes = await this.ensureModelBytes(model, op);
      this.assertOwned(op);
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
      } finally {
        op.controller.signal.removeEventListener('abort', loadAbort);
      }
    }
    const live = neural.sessionIdentity?.() ?? null;
    if (
      live === null ||
      live.sha256 !== model.onnxSha256 ||
      live.ruleset !== model.ruleset ||
      live.size !== model.boardSize ||
      live.provider !== provider
    ) {
      throw new BrowserAnalysisError(
        'model-integrity',
        'Active inference session does not match the requested model and provider',
      );
    }
    const identity = live;
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
    return this.finalizeNeural(raw, model, provider, effective, started, request.state);
  }

  private validateJarlEffective(expected: AnyRecord, actual: unknown): AnyRecord {
    if (!isRecord(actual)) {
      throw new BrowserAnalysisError('search-failed', 'Jarl effective settings are missing');
    }
    if (actual.algorithm !== 'alpha_beta') {
      throw new BrowserAnalysisError('search-failed', 'Jarl did not run the alpha-beta algorithm');
    }
    for (const key of ['depth', 'time_limit', 'seed', 'heuristic'] as const) {
      if (key === 'seed' && expected[key] === undefined) continue;
      if (actual[key] !== expected[key]) {
        throw new BrowserAnalysisError(
          'search-failed',
          `Jarl effective setting ${key} does not match the requested configuration`,
        );
      }
    }
    if (!isRecord(actual.search)) {
      throw new BrowserAnalysisError('search-failed', 'Jarl effective search settings are missing');
    }
    for (const key of Object.keys(expected.search as AnyRecord)) {
      if (actual.search[key] !== (expected.search as AnyRecord)[key]) {
        throw new BrowserAnalysisError(
          'search-failed',
          `Jarl effective search setting ${key} does not match the request`,
        );
      }
    }
    return actual;
  }

  private validateNeuralEffective(expected: AnyRecord, actual: unknown): AnyRecord {
    if (!isRecord(actual)) {
      throw new BrowserAnalysisError('search-failed', 'Neural effective settings are missing');
    }
    if (actual.algorithm !== 'mcts') {
      throw new BrowserAnalysisError('search-failed', 'Neural search did not run MCTS');
    }
    for (const key of ['iterations', 'time_limit', 'seed', 'exploration'] as const) {
      if (actual[key] !== expected[key]) {
        throw new BrowserAnalysisError(
          'search-failed',
          `Neural effective setting ${key} does not match the requested configuration`,
        );
      }
    }
    if (actual.temperature !== undefined && actual.temperature !== 0) {
      throw new BrowserAnalysisError(
        'search-failed',
        'Neural search reported a non-deterministic temperature',
      );
    }
    return actual;
  }

  private async finalizeNeural(
    raw: AnyRecord,
    model: ModelManifest,
    provider: ProviderRequest,
    requestedSettings: AnyRecord,
    started: number,
    state: AnyRecord,
  ): Promise<SearchResult> {
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
    if (typeof search.search_id !== 'string' || search.search_id.length === 0) {
      throw new BrowserAnalysisError('search-failed', 'Neural search identity is missing');
    }
    const completionReason = search.completion_reason;
    if (typeof completionReason !== 'string' || !NEURAL_COMPLETION_REASONS.has(completionReason)) {
      throw new BrowserAnalysisError('search-failed', 'Neural completion reason is malformed');
    }
    const iterations = Number(requestedSettings.iterations);
    if (!Number.isInteger(iterations) || completed > iterations) {
      throw new BrowserAnalysisError(
        'search-failed',
        'Completed simulations exceed the requested simulation budget',
      );
    }
    if (completionReason === 'simulations' && completed !== iterations) {
      throw new BrowserAnalysisError(
        'search-failed',
        'A simulation-budget completion did not complete the requested simulations',
      );
    }
    const effective = this.validateNeuralEffective(requestedSettings, raw.settings);
    const reported = search.provider;
    let observed: ObservedPlacement = 'none';
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
      if (provider === 'webgpu' && (placement === 'wasm' || placement === 'cpu-fallback')) {
        throw new BrowserAnalysisError(
          'search-failed',
          'Provider evidence reports an unauthorized fallback from the requested WebGPU provider',
        );
      }
      if (typeof reported.gpu_execution_verified !== 'boolean') {
        throw new BrowserAnalysisError(
          'search-failed',
          'Provider GPU verification evidence is malformed',
        );
      }
      if (reported.gpu_execution_verified === true) {
        // The pinned runtime exposes no independent per-operator verification.
        // A session-level claim is never accepted as verified GPU execution.
        throw new BrowserAnalysisError(
          'search-failed',
          'GPU execution verification is unavailable for the pinned inference runtime',
        );
      }
      observed = placement as ObservedPlacement;
    } else if (reported !== null && reported !== undefined) {
      throw new BrowserAnalysisError(
        'search-failed',
        'Provider evidence is present for a search without inference',
      );
    }
    const searchPosition = await searchIdentity(
      state as Record<string, unknown>,
      effective,
      model,
      provider,
    );
    return {
      analysis: raw.analysis as Record<string, unknown>,
      algorithm: NEURAL_ALGORITHM,
      settings: effective,
      evidence: {
        executionRuntime: 'pyodide',
        requestedProvider: provider,
        observedPlacement: observed,
        gpuExecutionVerified: false,
        inferenceObserved: inferences > 0,
        inferences,
        completedSimulations: completed,
        completionReason,
      },
      model: { sha256: model.onnxSha256, ruleset: model.ruleset, size: model.boardSize },
      positionIdentity: searchPosition,
      searchId: search.search_id,
      elapsedMs: now() - started,
      authoritative: false,
    };
  }

  /**
   * Record that the server authorized this account and runtime while online.
   * The lease is deliberately local, short-lived and never treated as
   * server-verifiable proof of permission. Model identity is still checked by
   * the normal session/manifest contract on every offline search.
   */
  noteOnlineAuthorization(accountId: string, seconds: number): void {
    if (this.disposed || typeof accountId !== 'string' || accountId.length === 0) return;
    if (!Number.isFinite(seconds) || seconds <= 0) return;
    const bounded = Math.min(seconds * 1000, LIMITS.maxOfflineAuthorizationMs);
    this.offlineLease = {
      accountId,
      until: now() + bounded,
      runtimeId: this.runtimeId ?? '',
    };
  }

  private assertOfflineLease(accountId: string): void {
    const lease = this.offlineLease;
    if (
      !lease ||
      lease.accountId !== accountId ||
      lease.runtimeId !== (this.runtimeId ?? '') ||
      now() > lease.until
    ) {
      throw new BrowserAnalysisError(
        'unauthorized',
        'Offline analysis authorization has expired or does not match this account',
        false,
      );
    }
  }

  clearOfflineAuthorization(): void {
    this.offlineLease = null;
    this.modelAuthorizations.clear();
  }

  // ------------------------------------------------------------ cancellation & lifecycle
  /** Cooperative cancellation of the active search/initialization and inference work. */
  async cancel(reason = 'Search cancelled'): Promise<void> {
    const context = this.context;
    const search = this.activeSearch;
    const captured = {
      jarl: this.jarl,
      neural: this.neural,
      coordinator: this.coordinator,
    };
    const retirementId = this.registerRetirement({
      jarl: null,
      neural: captured.neural,
      coordinator: captured.coordinator,
    });
    if (!search) {
      // A cancelled initialization must not be joined by a replacement call.
      this.initState = null;
    }
    if (search) {
      search.op.active = false;
      search.op.controller.abort();
      try {
        captured.coordinator?.abort?.(new Error(reason));
      } catch {
        // The validated coordinator owns bounded shutdown recovery.
      }
    }
    try {
      captured.jarl?.cancel(reason);
    } catch {
      // Idle cancellation is a no-op in the validated client.
    }
    this.abortOperations((op) => op !== search?.op);
    const tasks: Promise<unknown>[] = [];
    if (captured.neural?.cancelWork) {
      tasks.push(captured.neural.cancelWork({ reason }).catch(() => undefined));
    }
    if (search) tasks.push(search.promise.catch(() => undefined));
    const prior = this.cleanupBarrier;
    const barrier = prior.then(() => Promise.allSettled(tasks)).then(() => undefined);
    this.cleanupBarrier = barrier;
    const completed = await this.withTimeout(
      barrier,
      LIMITS.shutdownTimeoutMs,
      'Cancellation cleanup did not finish',
    )
      .then(() => true)
      .catch(() => false);
    if (completed) {
      this.settleRetirement(retirementId);
      if (
        this.context === context &&
        this.activeSearch === search &&
        (this.status.state === 'searching' || this.status.state === 'initializing')
      ) {
        this.setState('cancelled', { error: null });
      }
      return;
    }
    // The caller's wait is bounded, but that is not proof the underlying work
    // stopped. Quarantine only the exact clients captured from this context and
    // release the admission barrier so future operations are not permanently
    // blocked on an obsolete unresolved promise.
    if (this.context === context) {
      this.terminateRetirements(context);
      this.settleRetirement(retirementId);
      if (this.coordinator === captured.coordinator) this.coordinator = null;
      if (this.neural === captured.neural) this.neural = null;
      if (this.activeSearch === search) this.activeSearch = null;
      if (this.cleanupBarrier === barrier) this.cleanupBarrier = Promise.resolve();
      this.setState('error', {
        error: {
          code: 'search-failed',
          message:
            'Cancellation cleanup did not finish; affected inference workers were terminated',
          retryable: true,
        },
      });
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

  private takeClients(): {
    jarl: LocalAnalysisClientLike | null;
    neural: NeuralClientLike | null;
    coordinator: CoordinatorLike | null;
  } {
    const captured = {
      jarl: this.jarl,
      neural: this.neural,
      coordinator: this.coordinator,
    };
    this.jarl = null;
    this.neural = null;
    this.coordinator = null;
    return captured;
  }

  private disposeCaptured(captured: {
    jarl: LocalAnalysisClientLike | null;
    neural: NeuralClientLike | null;
    coordinator: CoordinatorLike | null;
  }): void {
    for (const client of [captured.coordinator, captured.neural, captured.jarl]) {
      if (!client) continue;
      if (this.terminatedClients.has(client as object)) continue;
      this.terminatedClients.add(client as object);
      try {
        (client as { dispose?: () => void }).dispose?.();
      } catch {
        // Bounded disposal is owned by the validated client.
      }
    }
  }

  private registerRetirement(clients: {
    jarl: LocalAnalysisClientLike | null;
    neural: NeuralClientLike | null;
    coordinator: CoordinatorLike | null;
  }): number {
    const id = ++this.retirementSequence;
    this.retirements.set(id, { clients, context: this.context });
    return id;
  }

  private settleRetirement(id: number): void {
    this.retirements.delete(id);
  }

  /** Terminate only captured resources from this or older contexts. */
  private terminateRetirements(contextLimit: number): void {
    for (const [id, retirement] of [...this.retirements]) {
      if (retirement.context > contextLimit) continue;
      this.disposeCaptured(retirement.clients);
      this.retirements.delete(id);
    }
  }

  /**
   * Invalidate all work bound to the previous account context. The admission
   * boundary is atomic: references to the old workers are removed before the
   * first await, new operations wait on the cleanup barrier, and delayed cleanup
   * only ever disposes the exact resources captured from the invalidated context.
   */
  async invalidateAccountContext(): Promise<void> {
    this.context += 1;
    this.offlineLease = null;
    this.modelAuthorizations.clear();
    const context = this.context;
    this.initState = null;
    this.abortOperations(() => true);
    this.downloads.cancelAll('Account context changed');
    const search = this.activeSearch;
    this.activeSearch = null;
    const captured = this.takeClients();
    const retirementId = this.registerRetirement(captured);
    this.result = null;
    this.setState('idle', {
      runtimeId: this.runtimeId,
      engine: null,
      evidence: null,
      lastSettings: null,
      error: null,
    });
    const tasks: Promise<unknown>[] = [];
    if (search) {
      search.op.active = false;
      search.op.controller.abort();
      try {
        captured.coordinator?.abort?.(new Error('Account context changed'));
        captured.jarl?.cancel('Account context changed');
      } catch {
        // Bounded cleanup is owned by the validated clients.
      }
      tasks.push(search.promise.catch(() => undefined));
    }
    if (captured.neural?.cancelWork) {
      tasks.push(
        captured.neural.cancelWork({ reason: 'Account context changed' }).catch(() => undefined),
      );
    }
    const prior = this.cleanupBarrier;
    const barrier = prior.then(() => Promise.allSettled(tasks)).then(() => undefined);
    this.cleanupBarrier = barrier;
    const completed = await this.withTimeout(
      barrier,
      LIMITS.shutdownTimeoutMs,
      'Account-context cleanup did not finish',
    )
      .then(() => true)
      .catch(() => false);
    this.disposeCaptured(captured);
    this.settleRetirement(retirementId);
    if (!completed && this.context === context) {
      this.terminateRetirements(context);
      if (this.cleanupBarrier === barrier) this.cleanupBarrier = Promise.resolve();
      this.setState('error', {
        error: {
          code: 'search-failed',
          message: 'Account-context cleanup did not finish; workers were terminated',
          retryable: true,
        },
      });
    }
  }

  private releaseManagedInstance(): void {
    if (!this.managedInstance || this.managedReleased) return;
    this.managedReleased = true;
    managedRuntimeInstances = Math.max(0, managedRuntimeInstances - 1);
  }

  /**
   * Release workers and abort transfers. The caller-facing outcome is bounded,
   * but managed-instance admission is retained until retiring workers, hashing
   * and storage admissions genuinely settle. Repeated calls share one outcome.
   */
  async dispose(): Promise<RuntimeDisposalOutcome> {
    if (this.disposeOutcome) return this.disposeOutcome;
    if (this.disposalPromise) return this.disposalPromise;
    this.disposed = true;
    this.context += 1;
    this.offlineLease = null;
    this.modelAuthorizations.clear();
    this.modelAuthorizations.clear();
    this.abortOperations(() => true);
    this.downloads.cancelAll('Runtime disposed');
    const search = this.activeSearch;
    this.activeSearch = null;
    const captured = this.takeClients();
    const context = this.context;
    const retirementId = this.registerRetirement(captured);
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
    const tasks: Promise<unknown>[] = [];
    if (search) {
      search.op.active = false;
      search.op.controller.abort();
      try {
        captured.coordinator?.abort?.(new Error('Runtime disposed'));
        captured.jarl?.cancel('Runtime disposed');
      } catch {
        // Bounded cleanup is owned by the validated clients.
      }
      tasks.push(search.promise.catch(() => undefined));
    }
    if (captured.neural?.cancelWork) {
      tasks.push(captured.neural.cancelWork({ reason: 'Runtime disposed' }).catch(() => undefined));
    }
    const prior = this.cleanupBarrier;
    const barrier = prior.then(() => Promise.allSettled(tasks)).then(() => undefined);
    this.cleanupBarrier = barrier;
    this.disposalPromise = (async () => {
      let outcome: RuntimeDisposalOutcome;
      try {
        const completed = await this.withTimeout(
          barrier,
          LIMITS.shutdownTimeoutMs,
          'Runtime disposal did not finish',
        )
          .then(() => true)
          .catch(() => false);
        this.disposeCaptured(captured);
        this.settleRetirement(retirementId);
        if (!completed) {
          // Terminate any earlier captured retirements and replace the obsolete
          // barrier only while it is still ours.
          this.terminateRetirements(context);
          if (this.cleanupBarrier === barrier) this.cleanupBarrier = Promise.resolve();
        }
        const drain = await this.downloads.drain(LIMITS.shutdownTimeoutMs);
        outcome = {
          drained: drain.drained,
          activeTransfers: drain.activeTransfers,
          reservedBytes: drain.reservedBytes,
          transfers: drain.transfers,
          queued: drain.queued,
          cacheVerifications: drain.cacheVerifications,
        };
      } catch {
        // Never leak the managed reservation on an unexpected disposal failure.
        outcome = {
          drained: false,
          ...this.downloads.occupancy,
        };
      }
      this.disposeOutcome = outcome;
      if (this.managedInstance) {
        if (outcome.drained) {
          this.releaseManagedInstance();
        } else {
          // Non-interruptible work still owns resources: retain admission until
          // it genuinely settles, then release exactly once.
          void this.downloads.whenIdle().then(
            () => this.releaseManagedInstance(),
            () => this.releaseManagedInstance(),
          );
        }
      }
      return outcome;
    })();
    return this.disposalPromise;
  }

  getLastResult(): SearchResult | null {
    return this.result;
  }

  /** True only when the live inference session already matches this model/provider. */
  modelReady(model: ModelManifest, provider: ProviderRequest): boolean {
    const live = this.neural?.sessionIdentity?.() ?? null;
    return (
      live !== null &&
      live.sha256 === model.onnxSha256 &&
      live.ruleset === model.ruleset &&
      live.size === model.boardSize &&
      live.provider === provider
    );
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
