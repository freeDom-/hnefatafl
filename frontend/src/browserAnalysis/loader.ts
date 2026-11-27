/**
 * Deferred loader for the browser-first analysis integration.
 *
 * The loader itself is tiny and contains no runtime, worker or engine import.
 * Vite keeps the target module in its own asynchronous chunk, so server-only and
 * disabled deployments never fetch or execute it.
 */
export type AnalysisIntegrationModule = typeof import('./integration');

let pending: Promise<AnalysisIntegrationModule> | null = null;

/** True once this tab has requested (or completed) the deferred integration. */
export function browserAnalysisLoaded(): boolean {
  return pending !== null;
}

export function loadAnalysisIntegration(): Promise<AnalysisIntegrationModule> {
  if (!pending) {
    const request = import('./integration');
    pending = request;
    request.catch(() => {
      if (pending === request) pending = null;
    });
  }
  return pending;
}
