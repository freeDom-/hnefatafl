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
export const ONNX_RUNTIME_WEB_VERSION = '1.22.0';
export const PYODIDE_VERSION = '0.27.7';

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
  initTimeoutMs: 45_000,
  maxJobTimeoutMs: 5_000,
  shutdownTimeoutMs: 5_000,
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

export interface RuntimeManifest {
  schema: number;
  runtimeId: string;
  pins: {
    pyodide: string;
    onnxruntimeWeb: string;
    encoderVersion: string;
    driverVersion: number;
    searchProtocol: number;
    bridgeProtocol: number;
    applicationVersion: string;
  };
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
}

export interface Availability {
  schema: number;
  enabled: boolean;
  runtimeAvailable: boolean;
  runtime: RuntimeManifest | null;
  reason: string | null;
  publishedModels: number;
}

export type ProviderRequest = 'wasm' | 'webgpu';

export interface SearchRequest {
  state: Record<string, unknown>;
  settings?: Record<string, unknown>;
  model?: ModelManifest;
  provider?: ProviderRequest;
  trace?: boolean;
}

export interface SearchResult {
  analysis: Record<string, unknown>;
  provider: {
    requested: ProviderRequest;
    placement: 'wasm' | 'webgpu-session-unverified' | 'cpu-fallback' | 'placement-unknown';
    gpuExecutionVerified: false;
  };
  model: { sha256: string; ruleset: string; size: number } | null;
  elapsedMs: number;
  authoritative: false;
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
  provider: SearchResult['provider'] | null;
  error: { code: BrowserAnalysisErrorCode; message: string; retryable: boolean } | null;
}
