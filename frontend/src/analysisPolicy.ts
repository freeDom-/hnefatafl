/**
 * Server-owned analysis execution policy shared by the initial application and
 * the lazily loaded browser-analysis chunk.
 *
 * This module is intentionally tiny and dependency-free so including it in the
 * initial bundle does not pull any runtime, worker or engine code. The policy is
 * always read from the server; nothing here is persisted as a player preference.
 */

export type ExecutionMode = 'browser_only' | 'browser_preferred' | 'server_only' | 'disabled';

export interface ExecutionPolicy {
  mode: ExecutionMode;
  browserDelivery: boolean;
  browserExecution: boolean;
  serverComputation: boolean;
  /** Server fallback is permitted only in browser_preferred mode. */
  fallback: boolean;
  webgpu: boolean;
  offlineAuthorizationSeconds: number;
  limits: {
    maxConcurrent: number;
    maxPerUser: number;
    requestsPerMinute: number;
  };
}

export const LEGACY_EXECUTION_POLICY: ExecutionPolicy = {
  mode: 'server_only',
  browserDelivery: false,
  browserExecution: false,
  serverComputation: true,
  fallback: false,
  webgpu: false,
  offlineAuthorizationSeconds: 900,
  limits: { maxConcurrent: 16, maxPerUser: 4, requestsPerMinute: 240 },
};

const MODES = new Set<ExecutionMode>([
  'browser_only',
  'browser_preferred',
  'server_only',
  'disabled',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function nonNegativeInt(value: unknown, maximum: number): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= maximum
    ? value
    : null;
}

/**
 * Validate the server projection. A missing projection is treated as the legacy
 * server-only policy so older servers remain compatible; explicit contradictions
 * are rejected rather than guessed.
 */
export function normalizeExecutionPolicy(
  raw: unknown,
  fallback: { browserDelivery: boolean } = { browserDelivery: false },
): ExecutionPolicy {
  if (raw === undefined || raw === null) {
    return { ...LEGACY_EXECUTION_POLICY, browserDelivery: fallback.browserDelivery };
  }
  if (!isRecord(raw) || !MODES.has(String(raw.mode) as ExecutionMode)) {
    throw new Error('Analysis execution policy is invalid');
  }
  const mode = raw.mode as ExecutionMode;
  const browserExecution = mode === 'browser_only' || mode === 'browser_preferred';
  const serverComputation = mode === 'server_only' || mode === 'browser_preferred';
  if (raw.browser_execution !== undefined && raw.browser_execution !== browserExecution) {
    throw new Error('Analysis browser execution flag contradicts its mode');
  }
  if (raw.server_computation !== undefined && raw.server_computation !== serverComputation) {
    throw new Error('Analysis server computation flag contradicts its mode');
  }
  if (raw.fallback !== undefined && raw.fallback !== (mode === 'browser_preferred')) {
    throw new Error('Analysis fallback flag contradicts its mode');
  }
  if (raw.browser_delivery !== undefined && typeof raw.browser_delivery !== 'boolean') {
    throw new Error('Analysis delivery flag is invalid');
  }
  if (raw.webgpu !== undefined && typeof raw.webgpu !== 'boolean') {
    throw new Error('Analysis WebGPU flag is invalid');
  }
  const offline = nonNegativeInt(raw.offline_authorization_seconds, 24 * 60 * 60);
  if (offline === null || offline < 60) {
    throw new Error('Analysis offline authorization window is invalid');
  }
  const limits = raw.limits;
  if (!isRecord(limits)) throw new Error('Analysis execution limits are missing');
  const maxConcurrent = nonNegativeInt(limits.max_concurrent, 1024);
  const maxPerUser = nonNegativeInt(limits.max_per_user, 1024);
  const requestsPerMinute = nonNegativeInt(limits.requests_per_minute, 10_000);
  if (maxConcurrent === null || maxPerUser === null || requestsPerMinute === null) {
    throw new Error('Analysis execution limits are invalid');
  }
  if (browserExecution === false && raw.browser_delivery === true && mode !== 'server_only') {
    throw new Error('Analysis delivery flag contradicts its mode');
  }
  return {
    mode,
    browserDelivery:
      typeof raw.browser_delivery === 'boolean' ? raw.browser_delivery : browserExecution,
    browserExecution,
    serverComputation,
    fallback: mode === 'browser_preferred',
    webgpu: raw.webgpu === true,
    offlineAuthorizationSeconds: offline,
    limits: { maxConcurrent, maxPerUser, requestsPerMinute },
  };
}
