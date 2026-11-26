/**
 * Browser-analysis Phase 4 integration contract for Phase 5.
 *
 * This entry point is built as its own lazy chunk and is not imported by any
 * ordinary application route. Browser analysis is disabled by default; nothing
 * here runs, downloads or initializes a worker until Phase 5 (or a diagnostic
 * harness) explicitly calls `createBrowserAnalysis()` and `initialize()`.
 */
export {
  BrowserAnalysisError,
  LIMITS,
  RUNTIME_MANIFEST_SCHEMA,
  MODEL_MANIFEST_SCHEMA,
  type Availability,
  type BrowserAnalysisErrorCode,
  type BrowserAnalysisState,
  type DownloadProgress,
  type ModelDownloadResult,
  type ModelManifest,
  type ProviderRequest,
  type RuntimeManifest,
  type RuntimeStatus,
  type SearchExecutionEvidence,
  type SearchRequest,
  type SearchResult,
} from './contracts';
export type { AnalysisAlgorithm };
export { JARL_ALGORITHM, NEURAL_ALGORITHM } from './contracts';
export {
  JARL_SETTINGS,
  modelFromCatalog,
  normalizeJarlSettings,
  normalizeNeuralSettings,
} from './runtime';
export { ArtifactCache, openArtifactStore } from './storage';
export { validateModelManifest, validateRuntimeManifest } from './manifests';

import type {
  Availability,
  AnalysisAlgorithm,
  DownloadProgress,
  ModelDownloadResult,
  ModelManifest,
  RuntimeDisposalOutcome,
  RuntimeStatus,
  SearchRequest,
  SearchResult,
} from './contracts';
import { BrowserAnalysisRuntime, type RuntimeOptions } from './runtime';

/** Production switch: Phase 5 must keep browser analysis opt-in. */
export const BROWSER_ANALYSIS_DEFAULT_ENABLED = false;

export interface BrowserAnalysisApi {
  availability(options?: { signal?: AbortSignal }): Promise<Availability>;
  initialize(options?: { signal?: AbortSignal; skipProbe?: boolean }): Promise<RuntimeStatus>;
  listModels(options?: { signal?: AbortSignal }): Promise<ModelManifest[]>;
  downloadModel(
    model: ModelManifest,
    hooks?: { onProgress?: (progress: DownloadProgress) => void; signal?: AbortSignal },
  ): Promise<ModelDownloadResult>;
  runSearch(
    request: SearchRequest,
    options?: { signal?: AbortSignal; offline?: { accountId: string } },
  ): Promise<SearchResult>;
  cancel(reason?: string): Promise<void>;
  invalidateAccountContext(): Promise<void>;
  /** Record a successful online authorization for bounded warm-offline work. */
  noteOnlineAuthorization(accountId: string, seconds: number): void;
  clearOfflineAuthorization(): void;
  dispose(): Promise<RuntimeDisposalOutcome>;
  status(): RuntimeStatus;
  lastResult(): SearchResult | null;
  modelReady(model: ModelManifest, provider: 'wasm' | 'webgpu'): boolean;
  storageUsage(): Promise<{ persistent: boolean; bytes: number }>;
}

export async function createBrowserAnalysis(
  options: RuntimeOptions = {},
): Promise<BrowserAnalysisApi> {
  const runtime = await BrowserAnalysisRuntime.create(options);
  return {
    availability: (options) => runtime.availability(options),
    initialize: (initOptions) => runtime.initialize(initOptions),
    listModels: (options) => runtime.listModels(options),
    downloadModel: (model, hooks) => runtime.downloadModel(model, hooks),
    runSearch: (request, searchOptions) => runtime.runSearch(request, searchOptions),
    cancel: (reason) => runtime.cancel(reason),
    invalidateAccountContext: () => runtime.invalidateAccountContext(),
    noteOnlineAuthorization: (accountId, seconds) =>
      runtime.noteOnlineAuthorization(accountId, seconds),
    clearOfflineAuthorization: () => runtime.clearOfflineAuthorization(),
    dispose: () => runtime.dispose(),
    status: () => runtime.getStatus(),
    lastResult: () => runtime.getLastResult(),
    modelReady: (model, provider) => runtime.modelReady(model, provider),
    storageUsage: () => runtime.storageUsage(),
  };
}
