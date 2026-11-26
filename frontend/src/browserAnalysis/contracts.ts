/**
 * Versioned contracts for browser-local analysis delivery (Phase 4).
 *
 * These types are the boundary between the Phase 4 runtime foundation and the
 * Phase 5 analysis UI. They deliberately contain no React or design-system code
 * so the runtime can be lazily loaded without affecting the initial bundle.
 */

export const RUNTIME_MANIFEST_SCHEMA = 1;
export const MODEL_MANIFEST_SCHEMA = 1;
export const BROWSER_ANALYSIS_SCHEMA = 1;
export const ENCODER_VERSION = 'six-plane-v1';
export const SEARCH_PROTOCOL = 1;
export const BRIDGE_PROTOCOL = 1;
export const DRIVER_VERSION = 1;
export const SEARCH_ALGORITHM = 'neural-mcts-puct-v1';
export const ARCHITECTURE = 'tafl-policy-value-v1';
export const ONNX_RUNTIME_WEB_VERSION = '1.22.0';
export const PYODIDE_VERSION = '0.27.7';
export const MAX_RUNTIME_FILES = 4096;
/** Classical Jarl execution and neural MCTS are distinct algorithms. */
export type AnalysisAlgorithm = 'jarl' | 'neural_mcts';
export const JARL_ALGORITHM: AnalysisAlgorithm = 'jarl';
export const NEURAL_ALGORITHM: AnalysisAlgorithm = 'neural_mcts';

/** Conservative application-level limits (bytes unless noted). */
export const LIMITS = {
  maxRuntimeBytes: 160 * 1024 * 1024,
  maxModelBytes: 64 * 1024 * 1024,
  maxAggregateDownloadBytes: 192 * 1024 * 1024,
  maxConcurrentDownloads: 2,
  maxStoredModelBytes: 96 * 1024 * 1024,
  maxStoredModels: 4,
  downloadTimeoutMs: 120_000,
  downloadRetries: 1,
  maxSearches: 1,
  maxSimulations: 4096,
  maxTimeLimitSeconds: 10,
  /** Classical bridge/client ceiling; neural MCTS has its own cooperative limit. */
  maxClassicalBudgetSeconds: 5,
  maxNeuralBudgetSeconds: 10,
  initTimeoutMs: 45_000,
  maxJobTimeoutMs: 5_000,
  neuralWatchdogMs: 20_000,
  shutdownTimeoutMs: 5_000,
  /** Conservative client-side bound for warm offline authorization. */
  maxOfflineAuthorizationMs: 60 * 60 * 1000,
} as const;

export const RUNTIME_JS = {
  analysisClient: 'analysis-client.js',
  searchClient: 'search-client.js',
  neuralClient: 'neural-client.js',
  neuralWorker: 'neural-worker.js',
} as const;

/** Entries without which a runtime manifest is not usable. */
export const REQUIRED_RUNTIME_FILES = [
  'runtime/pyodide.js',
  'runtime/pyodide.asm.js',
  'runtime/pyodide.asm.wasm',
  'runtime/python_stdlib.zip',
  'runtime/pyodide-lock.json',
  'ort/ort.min.js',
  'ort/ort-wasm-simd-threaded.mjs',
  'ort/ort-wasm-simd-threaded.wasm',
  'ort/ort-wasm-simd-threaded.jsep.mjs',
  'ort/ort-wasm-simd-threaded.jsep.wasm',
  'python/hnefatafl/engine.py',
  'python/hnefatafl/encoding.py',
  'python/hnefatafl/mcts_driver.py',
  'bridge.py',
  'search_bridge.py',
  'assets.json',
  'search-assets.json',
  'worker.js',
  'analysis-client.js',
  'search-worker.js',
  'search-client.js',
  'neural-client.js',
  'neural-worker.js',
] as const;

export type BrowserAnalysisState =
  | 'disabled'
  | 'unavailable'
  | 'idle'
  | 'downloading'
  | 'initializing'
  | 'ready'
  | 'searching'
  | 'cancelled'
  | 'error';

export type BrowserAnalysisErrorCode =
  | 'disabled'
  | 'unauthorized'
  | 'runtime-missing'
  | 'runtime-incompatible'
  | 'runtime-integrity'
  | 'model-not-published'
  | 'model-integrity'
  | 'storage-unavailable'
  | 'quota-exceeded'
  | 'download-aborted'
  | 'download-failed'
  | 'download-timeout'
  | 'size-mismatch'
  | 'digest-mismatch'
  | 'cancelled'
  | 'not-initialized'
  | 'search-failed'
  | 'capability-missing'
  | 'unsupported'
  | 'superseded';

export class BrowserAnalysisError extends Error {
  readonly code: BrowserAnalysisErrorCode;
  readonly retryable: boolean;

  constructor(code: BrowserAnalysisErrorCode, message: string, retryable = false) {
    super(message);
    this.name = 'BrowserAnalysisError';
    this.code = code;
    this.retryable = retryable;
  }
}

export interface RuntimeFile {
  path: string;
  bytes: number;
  sha256: string;
}

export interface RuntimePins {
  pyodide: string;
  onnxruntimeWeb: string;
  encoderVersion: string;
  driverVersion: number;
  searchProtocol: number;
  searchAlgorithm: string;
  bridgeProtocol: number;
  architecture: string;
  applicationVersion: string;
}

export interface RuntimeManifest {
  schema: number;
  runtimeId: string;
  pins: RuntimePins;
  files: RuntimeFile[];
  totalBytes: number;
  basePath: string;
}

export interface ModelRuntimeCompatibility {
  runtimeManifestSchema: number;
  ortVersion: string;
  minOpset: number;
  maxOpset: number;
  encoderVersion: string;
  searchProtocol: number;
  bridgeProtocol: number;
}

export interface ExecutionPolicy {
  mode: 'browser_only' | 'browser_preferred' | 'server_only' | 'disabled';
  browserDelivery: boolean;
  browserExecution: boolean;
  serverComputation: boolean;
  fallback: boolean;
  webgpu: boolean;
  offlineAuthorizationSeconds: number;
  limits: {
    maxConcurrent: number;
    maxPerUser: number;
    requestsPerMinute: number;
  };
}

export interface AnalysisPositionContext {
  schema: number;
  gameId: string;
  version: number;
  ruleset: string;
  cursor: number;
  positionKey: string;
  states: Record<string, unknown>[];
  execution: ExecutionPolicy;
}

export interface ModelManifest {
  schema: number;
  publicationId: string;
  artifactVersion: string;
  displayName: string;
  ruleset: string;
  boardSize: number;
  architecture: { name: string; channels: number; blocks: number };
  encoderVersion: string;
  sourceWeightsSha256: string;
  onnxSha256: string;
  artifactBytes: number;
  runtime: ModelRuntimeCompatibility;
  io: { input: unknown; outputs: unknown };
  status: string;
  publishedAt: string;
  /** Registered model ids whose verified weights produced this publication. */
  registeredModelIds: string[];
}

export interface Availability {
  schema: number;
  enabled: boolean;
  runtimeAvailable: boolean;
  runtime: RuntimeManifest | null;
  reason: string | null;
  publishedModels: number;
  analysisMode: ExecutionPolicy['mode'];
  execution: ExecutionPolicy;
}

export type ProviderRequest = 'wasm' | 'webgpu';
export type ObservedPlacement =
  | 'wasm'
  | 'webgpu-session-unverified'
  | 'webgpu-mixed'
  | 'cpu-fallback'
  | 'placement-unknown'
  | 'none';

export interface SearchRequest {
  state: Record<string, unknown>;
  /** Explicit algorithm selection: Jarl alpha-beta or neural MCTS. */
  algorithm: AnalysisAlgorithm;
  /** Optional model-free compatible settings; effective settings are returned. */
  settings?: Record<string, unknown>;
  model?: ModelManifest;
  provider?: ProviderRequest;
  trace?: boolean;
}

export interface SearchExecutionEvidence {
  /** Classical Pyodide execution is distinct from neural inference placement. */
  executionRuntime: 'pyodide';
  requestedProvider: ProviderRequest | null;
  observedPlacement: ObservedPlacement;
  gpuExecutionVerified: boolean;
  /** `none` when the search completed without any neural inference. */
  inferenceObserved: boolean;
  inferences: number;
  completedSimulations: number | null;
  completionReason: string | null;
}

export interface SearchResult {
  analysis: Record<string, unknown>;
  algorithm: AnalysisAlgorithm;
  /** Effective validated settings actually handed to the engine. */
  settings: Record<string, unknown>;
  evidence: SearchExecutionEvidence;
  model: { sha256: string; ruleset: string; size: number } | null;
  positionIdentity: string | null;
  searchId: string | null;
  elapsedMs: number;
  authoritative: false;
}

export interface RuntimeDisposalOutcome {
  /** True only when every download/hashing/admission operation has settled. */
  drained: boolean;
  activeTransfers: number;
  reservedBytes: number;
  transfers: number;
  queued: number;
  cacheVerifications: number;
}

export interface ModelDownloadResult {
  sha256: string;
  bytes: number;
  fromCache: boolean;
  retained: boolean;
  persistent: boolean;
}

export interface DownloadProgress {
  key: string;
  transferredBytes: number;
  totalBytes: number;
}

export interface RuntimeStatus {
  state: BrowserAnalysisState;
  runtimeId: string | null;
  /** Effective engine identity reported by the initialized worker. */
  engine: Record<string, unknown> | null;
  evidence: SearchExecutionEvidence | null;
  /** Effective settings from the most recent completed search. */
  lastSettings: Record<string, unknown> | null;
  error: { code: BrowserAnalysisErrorCode; message: string; retryable: boolean } | null;
}
