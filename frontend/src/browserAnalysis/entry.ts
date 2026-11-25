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
  BrowserAnalysisRuntime,
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
  RuntimeStatus,
  SearchRequest,
  SearchResult,
} from './contracts';
import { BrowserAnalysisRuntime, type RuntimeOptions } from './runtime';

/** Production switch: Phase 5 must keep browser analysis opt-in. */
export const BROWSER_ANALYSIS_DEFAULT_ENABLED = false;

export interface BrowserAnalysisApi {
  availability(): Promise<Availability>;
  initialize(options?: { signal?: AbortSignal; skipProbe?: boolean }): Promise<RuntimeStatus>;
  listModels(): Promise<ModelManifest[]>;
  downloadModel(
    model: ModelManifest,
    hooks?: { onProgress?: (progress: DownloadProgress) => void; signal?: AbortSignal },
  ): Promise<ModelDownloadResult>;
  runSearch(request: SearchRequest, options?: { signal?: AbortSignal }): Promise<SearchResult>;
  cancel(reason?: string): Promise<void>;
  invalidateAccountContext(): Promise<void>;
  dispose(): Promise<void>;
  status(): RuntimeStatus;
  lastResult(): SearchResult | null;
  storageUsage(): Promise<{ persistent: boolean; bytes: number }>;
}

export async function createBrowserAnalysis(
  options: RuntimeOptions = {},
): Promise<BrowserAnalysisApi> {
  const runtime = await BrowserAnalysisRuntime.create(options);
  return {
    availability: () => runtime.availability(),
    initialize: (initOptions) => runtime.initialize(initOptions),
    listModels: () => runtime.listModels(),
    downloadModel: (model, hooks) => runtime.downloadModel(model, hooks),
    runSearch: (request, searchOptions) => runtime.runSearch(request, searchOptions),
    cancel: (reason) => runtime.cancel(reason),
    invalidateAccountContext: () => runtime.invalidateAccountContext(),
    dispose: () => runtime.dispose(),
    status: () => runtime.getStatus(),
    lastResult: () => runtime.getLastResult(),
    storageUsage: () => runtime.storageUsage(),
  };
}
