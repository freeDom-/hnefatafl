/**
 * Transparent browser-first analysis orchestration (Phase 5).
 *
 * This module is loaded only from a deferred/dynamic boundary. It normalizes an
 * existing analysis request, resolves the server-owned execution policy, owns the
 * managed Phase 4 runtime, selects a validated provider, executes locally and
 * adapts the advisory result to the existing presentation contract.
 *
 * It never changes the requested agent, model, depth, simulation count or time
 * budget. When a request cannot be executed locally it returns a classified
 * failure; only the caller may decide whether the server policy permits fallback.
 */
import type { AgentEvaluation, AnalyticsSettings, PositionAnalysis } from '../analysisTypes';
import type { Game, Move, Side } from '../types';
import { normalizeExecutionPolicy, type ExecutionPolicy } from '../analysisPolicy';
import {
  BrowserAnalysisError,
  createBrowserAnalysis,
  type BrowserAnalysisApi,
  type ModelManifest,
  type SearchExecutionEvidence,
  type SearchResult,
} from './entry';

export { normalizeExecutionPolicy } from '../analysisPolicy';
export type { ExecutionPolicy } from '../analysisPolicy';

export type LocalAnalysisStage =
  | 'preparing'
  | 'downloading'
  | 'initializing'
  | 'loading-model'
  | 'searching';

export type LocalFailureCategory =
  | 'unsupported-agent'
  | 'unsupported-budget'
  | 'model-unavailable'
  | 'browser-unsupported'
  | 'initialization'
  | 'download'
  | 'integrity'
  | 'cancelled'
  | 'unauthorized'
  | 'offline'
  | 'disabled'
  | 'search'
  | 'unknown';

export class LocalAnalysisError extends Error {
  constructor(
    public readonly category: LocalFailureCategory,
    message: string,
    public readonly fallbackEligible: boolean,
    public readonly code: string | null = null,
    public readonly retryable = false,
  ) {
    super(message);
    this.name = 'LocalAnalysisError';
  }
}

export interface LocalAnalysisOutcome {
  report: PositionAnalysis;
  evidence: SearchExecutionEvidence;
  provider: 'wasm' | 'webgpu';
  offline: boolean;
  model: { sha256: string; ruleset: string; size: number } | null;
  fromCache: boolean;
}

export interface LocalAnalysisInput {
  accountId: string | null;
  game: Game;
  basePath: string;
  cursor: number;
  settings: AnalyticsSettings;
  policy: ExecutionPolicy;
  signal: AbortSignal;
  offline?: boolean;
  onStage?: (stage: LocalAnalysisStage) => void;
  onDownloadProgress?: (progress: { transferredBytes: number; totalBytes: number }) => void;
}

interface AdvisorContext {
  effective_config: Record<string, unknown>;
  requested_config?: Record<string, unknown>;
  show_win_chances?: boolean;
  revision?: number;
  seed_with_ply?: boolean;
}

interface ContextPayload {
  schema: number;
  game_id: string;
  version: number;
  ruleset: string;
  cursor: number;
  position_key: string;
  position_keys?: string[];
  history_keys?: string[];
  states: Record<string, unknown>[];
  advisor?: AdvisorContext | null;
  execution?: unknown;
}

interface CachedContext {
  states: Record<string, unknown>[];
  historyKeys: string[];
  positionKey: string;
  positionKeys: string[];
  advisor: AdvisorContext | null;
  version: number;
  scope: string;
  bytes: number;
}

interface ContextLease {
  at: number;
  account: string | null;
  version: number;
  gameId: string;
}

interface SharedContextRequest {
  key: string;
  generation: number;
  controller: AbortController;
  consumers: Set<AbortSignal>;
  promise: Promise<CachedContext>;
}

const CONTEXT_LIMIT = 64;
const CONTEXT_BYTE_LIMIT = 8 * 1024 * 1024;
const MODEL_CACHE_MS = 5 * 60_000;

let account: string | null = null;
let generation = 0;
let binding: Promise<void> = Promise.resolve();
let bindSequence = 0;
let runtimePromise: Promise<BrowserAnalysisApi> | null = null;
let runtimeGeneration = -1;
let modelCache: {
  account: string | null;
  generation: number;
  at: number;
  models: ModelManifest[];
} | null = null;
const contexts = new Map<string, CachedContext>();
let contextBytes = 0;
const contextRequests = new Map<string, SharedContextRequest>();
const lastProviders = new Map<string, 'wasm' | 'webgpu'>();
const contextLeases = new Map<string, ContextLease>();
const modelLeases = new Map<string, { at: number; account: string | null; sha256: string }>();
let onlineValidationRequired = true;
let lastOnlineValidationAt = 0;

function now(): number {
  return Date.now();
}

function scopeKey(
  input: Pick<LocalAnalysisInput, 'game' | 'basePath' | 'accountId' | 'settings'>,
): string {
  // Advisor/strength changes are their own context identity: cached analysis,
  // effective configuration and offline authorization must not cross revisions.
  return `${input.accountId ?? ''}:${input.basePath}:${input.game.id}:${input.game.version}:${input.settings.revision}`;
}

function assertGeneration(expected: number): void {
  if (generation !== expected) {
    throw failure('cancelled', 'Analysis was superseded by an account change', false, 'cancelled');
  }
}

function abortContextRequests(): void {
  for (const request of contextRequests.values()) request.controller.abort();
  contextRequests.clear();
}

function clearAccountState(): void {
  contexts.clear();
  contextBytes = 0;
  abortContextRequests();
  lastProviders.clear();
  contextLeases.clear();
  modelLeases.clear();
  modelCache = null;
  onlineValidationRequired = true;
  lastOnlineValidationAt = 0;
}

/**
 * Bind the runtime to the current account. A change invalidates every worker,
 * cached position, catalogue and lease before a new search can start; logout
 * disposes the runtime entirely.
 */
export function setAnalysisAccount(next: string | null): Promise<void> {
  if (next === account) return binding;
  const sequence = ++bindSequence;
  generation += 1;
  const targetGeneration = generation;
  const previous = runtimePromise;
  account = next;
  clearAccountState();
  binding = (async () => {
    if (!previous) return;
    let api: BrowserAnalysisApi;
    try {
      api = await previous;
    } catch {
      return;
    }
    if (next === null) {
      await api.clearOfflineAuthorization();
      await api.dispose();
      return;
    }
    if (bindSequence === sequence && generation === targetGeneration) {
      await api.invalidateAccountContext();
      if (runtimePromise === previous) runtimeGeneration = targetGeneration;
    }
  })();
  if (next === null) runtimePromise = null;
  return binding;
}

/** Test/logout hook: release the managed runtime and every cached context. */
export async function disposeLocalAnalysis(): Promise<void> {
  bindSequence += 1;
  generation += 1;
  account = null;
  clearAccountState();
  const previous = runtimePromise;
  runtimePromise = null;
  runtimeGeneration = -1;
  binding = Promise.resolve();
  if (previous) {
    try {
      const api = await previous;
      await api.clearOfflineAuthorization();
      await api.dispose();
    } catch {
      // A failed creation has nothing to retire.
    }
  }
}

async function getRuntime(accountId: string | null, expected: number): Promise<BrowserAnalysisApi> {
  if (account !== accountId) await setAnalysisAccount(accountId);
  await binding;
  assertGeneration(expected);
  if (!runtimePromise) {
    const creation = createBrowserAnalysis();
    runtimePromise = creation;
    const creationGeneration = expected;
    creation.then(
      (api) => {
        if (generation !== creationGeneration || account !== accountId) {
          void api.clearOfflineAuthorization();
          void api.dispose();
          if (runtimePromise === creation) {
            runtimePromise = null;
            runtimeGeneration = -1;
          }
          return;
        }
        runtimeGeneration = creationGeneration;
      },
      () => {
        if (runtimePromise === creation) runtimePromise = null;
      },
    );
  }
  const api = await runtimePromise;
  assertGeneration(expected);
  if (runtimeGeneration !== expected) {
    throw failure('cancelled', 'The runtime belongs to an obsolete account context', false);
  }
  return api;
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof BrowserAnalysisError && error.code === 'cancelled')
  );
}

function failure(
  category: LocalFailureCategory,
  message: string,
  fallbackEligible: boolean,
  code: string | null = null,
  retryable = false,
): LocalAnalysisError {
  return new LocalAnalysisError(category, message, fallbackEligible, code, retryable);
}

function classify(error: unknown): LocalAnalysisError {
  if (error instanceof LocalAnalysisError) return error;
  if (isAbort(error)) return failure('cancelled', 'Analysis was cancelled', false, 'cancelled');
  if (error instanceof BrowserAnalysisError) {
    const code = error.code;
    const text = error.message;
    if (code === 'unauthorized') return failure('unauthorized', text, false, code, false);
    if (
      code === 'runtime-integrity' ||
      code === 'model-integrity' ||
      code === 'digest-mismatch' ||
      code === 'size-mismatch'
    ) {
      return failure('integrity', text, false, code, false);
    }
    if (code === 'model-not-published') {
      return failure('model-unavailable', text, true, code, false);
    }
    if (code === 'capability-missing') {
      return failure('browser-unsupported', text, true, code, false);
    }
    if (code === 'runtime-missing') {
      const integrity = /integrity|identity|digest|manifest/i.test(text);
      return failure(
        integrity ? 'integrity' : 'browser-unsupported',
        text,
        !integrity,
        code,
        !integrity,
      );
    }
    if (code === 'download-aborted') {
      return failure('cancelled', text, false, code, false);
    }
    if (code === 'download-failed' || code === 'download-timeout' || code === 'quota-exceeded') {
      return failure('download', text, true, code, error.retryable);
    }
    if (code === 'storage-unavailable') {
      return failure('initialization', text, false, code, false);
    }
    if (code === 'not-initialized') {
      return failure('initialization', text, true, code, false);
    }
    if (code === 'disabled') return failure('disabled', text, false, code, false);
    if (code === 'superseded') return failure('cancelled', text, false, code, false);
    if (code === 'unsupported') {
      return failure('unsupported-agent', text, true, code, false);
    }
    if (code === 'search-failed') {
      const memory = /memory|resource|exhaust/i.test(text);
      return failure('search', text, memory, code, error.retryable);
    }
    return failure('unknown', text, false, code, false);
  }
  // A generic TypeError is an engine/protocol fault, not proof of a network
  // outage. Network failures are converted at the fetch boundary.
  return failure('unknown', (error as Error)?.message ?? 'Local analysis failed', false);
}

function executionPolicy(raw: unknown, fallback: ExecutionPolicy): ExecutionPolicy {
  try {
    return normalizeExecutionPolicy(raw, { browserDelivery: fallback.browserDelivery });
  } catch {
    throw failure('integrity', 'The analysis execution policy is invalid', false);
  }
}

function policiesEqual(left: ExecutionPolicy, right: ExecutionPolicy): boolean {
  return (
    left.mode === right.mode &&
    left.browserDelivery === right.browserDelivery &&
    left.browserExecution === right.browserExecution &&
    left.serverComputation === right.serverComputation &&
    left.fallback === right.fallback &&
    left.webgpu === right.webgpu &&
    left.offlineAuthorizationSeconds === right.offlineAuthorizationSeconds &&
    left.limits.maxConcurrent === right.limits.maxConcurrent &&
    left.limits.maxPerUser === right.limits.maxPerUser &&
    left.limits.requestsPerMinute === right.limits.requestsPerMinute
  );
}

async function fetchJson(path: string, signal: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal,
    });
  } catch {
    if (signal.aborted) throw failure('cancelled', 'Analysis was cancelled', false, 'cancelled');
    throw failure(
      'offline',
      'The analysis server is unreachable',
      false,
      'network-unavailable',
      true,
    );
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw failure('unauthorized', 'Sign in to analyze this position', false, 'unauthorized');
    }
    if (response.status === 409) {
      throw failure(
        'disabled',
        'Analysis is not available for this position',
        false,
        'analysis_context_changed',
      );
    }
    if (response.status === 429) {
      throw failure('search', 'Analysis is rate limited', false, 'analysis_rate_limited', true);
    }
    throw failure(
      'search',
      `Analysis context request failed (HTTP ${response.status})`,
      false,
      'context-failed',
      response.status >= 500,
    );
  }
  return response.json();
}

function validateContext(raw: unknown, input: LocalAnalysisInput): ContextPayload {
  if (
    !raw ||
    typeof raw !== 'object' ||
    (raw as ContextPayload).schema !== 1 ||
    typeof (raw as ContextPayload).game_id !== 'string' ||
    !Array.isArray((raw as ContextPayload).states) ||
    (raw as ContextPayload).states.length === 0 ||
    !Number.isInteger((raw as ContextPayload).cursor) ||
    typeof (raw as ContextPayload).position_key !== 'string'
  ) {
    throw failure('integrity', 'The analysis position context is malformed', false);
  }
  const payload = raw as ContextPayload;
  if (
    payload.game_id !== input.game.id ||
    payload.version !== input.game.version ||
    payload.ruleset !== input.game.ruleset ||
    payload.cursor !== input.cursor
  ) {
    throw failure('integrity', 'The analysis context does not match this request', false);
  }
  if (payload.states.length > 4096) {
    throw failure('integrity', 'The analysis context is larger than the supported bound', false);
  }
  if (
    !Array.isArray(payload.history_keys) ||
    payload.history_keys.length !== payload.states.length
  ) {
    throw failure('integrity', 'The analysis history keys are malformed', false);
  }
  if (
    !Array.isArray(payload.position_keys) ||
    payload.position_keys.length !== payload.states.length ||
    payload.position_keys.some((key) => typeof key !== 'string' || key.length === 0)
  ) {
    throw failure('integrity', 'The analysis position keys are malformed', false);
  }
  if (payload.history_keys.some((key) => typeof key !== 'string' || key.length === 0)) {
    throw failure('integrity', 'The analysis history keys are malformed', false);
  }
  for (const [index, state] of payload.states.entries()) {
    if (
      !state ||
      typeof state !== 'object' ||
      !Array.isArray((state as { board?: unknown }).board) ||
      typeof (state as { ruleset?: unknown }).ruleset !== 'string' ||
      (state as { ply?: unknown }).ply !== index
    ) {
      throw failure('integrity', 'A serialized analysis position is malformed', false);
    }
  }
  const advisor = payload.advisor;
  if (advisor !== null && advisor !== undefined) {
    if (
      typeof advisor !== 'object' ||
      !advisor.effective_config ||
      typeof advisor.effective_config !== 'object' ||
      (advisor.show_win_chances !== undefined && typeof advisor.show_win_chances !== 'boolean') ||
      (advisor.seed_with_ply !== undefined && typeof advisor.seed_with_ply !== 'boolean')
    ) {
      throw failure('integrity', 'The analysis advisor configuration is malformed', false);
    }
  }
  if (payload.execution !== undefined && payload.execution !== null) {
    const next = executionPolicy(payload.execution, input.policy);
    if (!policiesEqual(next, input.policy)) {
      throw failure(
        'disabled',
        'The analysis execution policy changed. Retry analysis.',
        false,
        'policy-changed',
        true,
      );
    }
  }
  return payload;
}

function contextFromPayload(payload: ContextPayload, scope: string): CachedContext {
  const bytes = new TextEncoder().encode(JSON.stringify(payload)).length;
  return {
    states: payload.states,
    historyKeys: payload.history_keys ?? [],
    positionKey: payload.position_key,
    positionKeys: payload.position_keys ?? [payload.position_key],
    advisor: payload.advisor ?? null,
    version: payload.version,
    scope,
    bytes,
  };
}

function cacheContext(key: string, context: CachedContext): void {
  const existing = contexts.get(key);
  if (existing) {
    contextBytes -= existing.bytes;
    contexts.delete(key);
  }
  contexts.set(key, context);
  contextBytes += context.bytes;
  while (
    contexts.size > CONTEXT_LIMIT ||
    (contextBytes > CONTEXT_BYTE_LIMIT && contexts.size > 1)
  ) {
    const oldest = contexts.keys().next().value;
    if (oldest === undefined || oldest === key) break;
    const removed = contexts.get(oldest);
    if (removed) contextBytes -= removed.bytes;
    contexts.delete(oldest);
  }
}

function subscribe(request: SharedContextRequest, signal: AbortSignal): Promise<CachedContext> {
  if (signal.aborted) {
    return Promise.reject(failure('cancelled', 'Analysis was cancelled', false, 'cancelled'));
  }
  request.consumers.add(signal);
  return new Promise<CachedContext>((resolve, reject) => {
    const cleanup = () => signal.removeEventListener('abort', onAbort);
    const onAbort = () => {
      cleanup();
      request.consumers.delete(signal);
      if (request.consumers.size === 0) request.controller.abort();
      reject(failure('cancelled', 'Analysis was cancelled', false, 'cancelled'));
    };
    signal.addEventListener('abort', onAbort, { once: true });
    request.promise.then(
      (value) => {
        cleanup();
        request.consumers.delete(signal);
        resolve(value);
      },
      (error) => {
        cleanup();
        request.consumers.delete(signal);
        reject(error);
      },
    );
  });
}

async function ensureContext(
  input: LocalAnalysisInput,
  offline: boolean,
  expectedGeneration: number,
  force = false,
): Promise<{ context: CachedContext; fromNetwork: boolean }> {
  const key = scopeKey(input);
  // A forced revalidation fetches fresh authorization but keeps the previous
  // prepared context until the replacement genuinely succeeds. An unreachable
  // origin must not erase an otherwise valid warm position.
  const cached = contexts.get(key);
  if (cached && !force) return { context: cached, fromNetwork: false };
  if (offline) {
    throw failure(
      'offline',
      'This position was not prepared for offline analysis',
      false,
      'offline-context-missing',
    );
  }
  const requestKey = `${key}:${force ? 'revalidated' : 'initial'}`;
  let shared = contextRequests.get(requestKey);
  if (!shared || shared.generation !== expectedGeneration) {
    const controller = new AbortController();
    const promise = (async () => {
      const path =
        input.basePath === '/history/online'
          ? `/api/history/online/${input.game.id}/analysis-context?cursor=${input.cursor}&version=${input.game.version}`
          : `/api/games/${input.game.id}/analysis-context?cursor=${input.cursor}&version=${input.game.version}`;
      const payload = validateContext(await fetchJson(path, controller.signal), input);
      assertGeneration(expectedGeneration);
      const context = contextFromPayload(payload, key);
      cacheContext(key, context);
      return context;
    })();
    shared = {
      key: requestKey,
      generation: expectedGeneration,
      controller,
      consumers: new Set(),
      promise,
    };
    contextRequests.set(requestKey, shared);
    promise.then(
      () => {
        if (contextRequests.get(requestKey) === shared) contextRequests.delete(requestKey);
      },
      () => {
        if (contextRequests.get(requestKey) === shared) contextRequests.delete(requestKey);
      },
    );
  }
  return { context: await subscribe(shared, input.signal), fromNetwork: true };
}

function contextLeaseValid(input: LocalAnalysisInput): boolean {
  const lease = contextLeases.get(scopeKey(input));
  return Boolean(
    lease &&
      lease.account === input.accountId &&
      lease.gameId === input.game.id &&
      lease.version === input.game.version &&
      now() - lease.at <= input.policy.offlineAuthorizationSeconds * 1000,
  );
}

function modelLeaseValid(input: LocalAnalysisInput, sha256: string): boolean {
  const lease = modelLeases.get(`${input.accountId ?? ''}:${sha256}`);
  return Boolean(
    lease &&
      lease.account === input.accountId &&
      lease.sha256 === sha256 &&
      now() - lease.at <= input.policy.offlineAuthorizationSeconds * 1000,
  );
}

async function ensureModels(
  api: BrowserAnalysisApi,
  input: LocalAnalysisInput,
  offline: boolean,
  expectedGeneration: number,
): Promise<ModelManifest[]> {
  if (
    modelCache &&
    modelCache.account === input.accountId &&
    modelCache.generation === expectedGeneration
  ) {
    if (offline || now() - modelCache.at < MODEL_CACHE_MS) return modelCache.models;
  }
  if (offline) {
    throw failure(
      'offline',
      'The analysis model catalogue is not available offline',
      false,
      'offline-models-missing',
    );
  }
  const models = await api.listModels({ signal: input.signal });
  assertGeneration(expectedGeneration);
  modelCache = {
    account: input.accountId,
    generation: expectedGeneration,
    at: now(),
    models,
  };
  return models;
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function effectiveAlgorithm(config: Record<string, unknown>): 'jarl' | 'neural_mcts' | null {
  if (config.algorithm === 'alpha_beta' || config.algorithm === 'alphabeta') return 'jarl';
  if (
    config.algorithm === 'neural' &&
    (config.search_wrapper ?? 'mcts') === 'mcts' &&
    Boolean(config.model_id)
  ) {
    return 'neural_mcts';
  }
  return null;
}

const JARL_CONFIG_FIELDS = new Set([
  'algorithm',
  'seed',
  'time_limit',
  'evaluation',
  'heuristic',
  'depth',
  'search',
  'heuristic_version',
]);
const NEURAL_CONFIG_FIELDS = new Set([
  'algorithm',
  'seed',
  'time_limit',
  'evaluation',
  'iterations',
  'rollout_depth',
  'exploration',
  'temperature',
  'search_wrapper',
  'model_id',
]);

function rejectUnsupportedConfig(config: Record<string, unknown>, allowed: Set<string>): void {
  for (const key of Object.keys(config)) {
    if (!allowed.has(key) && config[key] !== undefined && config[key] !== null) {
      throw failure(
        'unsupported-agent',
        `Unsupported local analysis setting: ${key}`,
        true,
        'unsupported-setting',
      );
    }
  }
}

function seedForState(
  config: Record<string, unknown>,
  state: Record<string, unknown>,
  seedWithPly: boolean,
): number {
  const base = Number.isInteger(config.seed) ? Number(config.seed) : 0;
  return (base + (seedWithPly ? Number(state.ply ?? 0) : 0)) >>> 0;
}

function jarlSettings(
  config: Record<string, unknown>,
  state: Record<string, unknown>,
  seedWithPly: boolean,
) {
  rejectUnsupportedConfig(config, JARL_CONFIG_FIELDS);
  const depth = numeric(config.depth) ?? 2;
  const seconds = numeric(config.time_limit) ?? 2;
  if (!Number.isInteger(depth) || depth < 1 || depth > 5 || seconds > 5 || seconds < 0.01) {
    throw failure(
      'unsupported-budget',
      'The requested Jarl depth or time budget is not supported by local execution',
      true,
      'local-budget',
    );
  }
  const settings: Record<string, unknown> = {
    depth,
    time_limit: seconds,
    seed: seedForState(config, state, seedWithPly),
  };
  if (typeof config.heuristic === 'string') settings.heuristic = config.heuristic;
  if (config.search && typeof config.search === 'object') settings.search = config.search;
  return settings;
}

function neuralSettings(
  config: Record<string, unknown>,
  state: Record<string, unknown>,
  seedWithPly: boolean,
) {
  rejectUnsupportedConfig(config, NEURAL_CONFIG_FIELDS);
  if ((config.search_wrapper ?? 'mcts') !== 'mcts') {
    throw failure(
      'unsupported-agent',
      'The requested neural search wrapper is not available locally',
      true,
      'unsupported-agent',
    );
  }
  const iterations = numeric(config.iterations) ?? 80;
  const seconds = numeric(config.time_limit) ?? 2;
  if (
    !Number.isInteger(iterations) ||
    iterations < 1 ||
    iterations > 4096 ||
    seconds < 0.05 ||
    seconds > 10
  ) {
    throw failure(
      'unsupported-budget',
      'The requested simulation count or time budget is not supported by local execution',
      true,
      'local-budget',
    );
  }
  if (config.temperature !== undefined && config.temperature !== 0) {
    throw failure(
      'unsupported-budget',
      'Local analysis requires deterministic temperature zero',
      true,
      'local-budget',
    );
  }
  return {
    iterations,
    time_limit: seconds,
    seed: seedForState(config, state, seedWithPly),
    exploration: numeric(config.exploration) ?? 1.4,
  };
}

function providerFor(input: LocalAnalysisInput, model: ModelManifest | null): 'wasm' | 'webgpu' {
  if (!model) return 'wasm';
  const previous = lastProviders.get(`${model.onnxSha256}:${input.accountId ?? ''}`);
  if (previous) return previous;
  return input.policy.webgpu &&
    typeof navigator !== 'undefined' &&
    Boolean((navigator as { gpu?: unknown }).gpu)
    ? 'webgpu'
    : 'wasm';
}

async function runWithProvider(
  api: BrowserAnalysisApi,
  input: LocalAnalysisInput,
  request: Parameters<BrowserAnalysisApi['runSearch']>[0],
  provider: 'wasm' | 'webgpu',
  offline: boolean,
  model: ModelManifest | null,
): Promise<SearchResult> {
  const attempt = (next: 'wasm' | 'webgpu') =>
    api.runSearch(
      { ...request, provider: model ? next : undefined },
      {
        signal: input.signal,
        offline: offline ? { accountId: input.accountId ?? '' } : undefined,
      },
    );
  try {
    return await attempt(provider);
  } catch (error) {
    const classified = classify(error);
    if (
      !offline &&
      provider === 'webgpu' &&
      (classified.category === 'browser-unsupported' ||
        classified.category === 'initialization' ||
        classified.code === 'capability-missing')
    ) {
      return attempt('wasm');
    }
    throw classified;
  }
}

function valueForSide(state: Record<string, unknown>, value: number): number {
  return state.turn === 'defenders' ? value : -value;
}

function adaptReport(
  input: LocalAnalysisInput,
  context: CachedContext,
  result: SearchResult,
  config: Record<string, unknown>,
  offline: boolean,
): LocalAnalysisOutcome {
  const state = context.states[input.cursor] as Record<string, unknown> | undefined;
  if (!state) {
    throw failure('integrity', 'The analyzed position is unavailable', false);
  }
  const raw = result.analysis;
  const value = numeric(raw.value);
  const source = raw.source;
  const sources = new Set([
    'heuristic',
    'mcts_rollout',
    'neural_value',
    'neural_search',
    'terminal',
  ]);
  if (
    value === null ||
    value < -1 ||
    value > 1 ||
    typeof source !== 'string' ||
    !sources.has(source)
  ) {
    throw failure('integrity', 'The local analysis result is inconsistent', false);
  }
  let best: Move | null = null;
  if (raw.best_move && typeof raw.best_move === 'object') {
    const move = raw.best_move as { source?: unknown; target?: unknown };
    if (
      !Number.isInteger(move.source) ||
      !Number.isInteger(move.target) ||
      Number(move.source) < 0 ||
      Number(move.target) < 0 ||
      Number(move.source) >= input.game.board.length ||
      Number(move.target) >= input.game.board.length
    ) {
      throw failure('integrity', 'The local best move is malformed', false);
    }
    best = { source: Number(move.source), target: Number(move.target) };
  }
  const advisor = input.settings.advisor;
  if (!advisor) throw failure('unsupported-agent', 'No advisor is selected', false);
  const showChances = context.advisor?.show_win_chances ?? true;
  const current = valueForSide(state, value);
  const evaluation: AgentEvaluation = {
    rune: advisor.rune,
    agent_id: 'advisor',
    name: advisor.name,
    role: 'advisor',
    profile_id: advisor.profile_id,
    profile_name: advisor.profile_name,
    model_name: advisor.model_name,
    config: { ...config, ...result.settings } as AgentEvaluation['config'],
    source: source as AgentEvaluation['source'],
    value,
    win_probability: showChances ? Math.max(0, Math.min(1, (current + 1) / 2)) : null,
    estimated: source !== 'terminal',
    calibrated: source === 'terminal',
    best_move: best,
  };
  if (!showChances) delete (evaluation as { value?: number }).value;
  const numbers = [
    'completed_depth',
    'simulations',
    'searched_moves',
    'nodes',
    'evaluations',
    'cutoffs',
    'transposition_hits',
    'symmetry_moves_skipped',
  ] as const;
  for (const key of numbers) {
    if (Number.isInteger(raw[key])) {
      (evaluation as unknown as Record<string, unknown>)[key] = raw[key];
    }
  }
  evaluation.search_seed = Number(result.settings.seed ?? config.seed ?? 0);
  return {
    report: {
      cursor: input.cursor,
      ply: Number(state.ply ?? input.game.ply),
      side_to_move: (state.turn === 'defenders' ? 'defenders' : 'attackers') as Side,
      position_key: context.positionKeys[input.cursor] ?? context.positionKey,
      created_at: new Date().toISOString(),
      evaluations: [evaluation],
      hint: best ? { agent_id: 'advisor', move: best } : null,
      status: 'complete',
      advisor_revision: input.settings.revision,
    },
    evidence: result.evidence,
    provider: result.evidence.requestedProvider ?? 'wasm',
    offline,
    model: result.model,
    fromCache: false,
  };
}

function searchState(context: CachedContext, cursor: number): Record<string, unknown> {
  const state = context.states[cursor];
  if (!state) throw failure('integrity', 'The analyzed position is unavailable', false);
  const history = context.historyKeys.slice(0, Math.max(1, Number(state.ply ?? 0) + 1));
  return { ...state, history };
}

async function localAttempt(
  input: LocalAnalysisInput,
  offline: boolean,
  expectedGeneration: number,
): Promise<LocalAnalysisOutcome> {
  const api = await getRuntime(input.accountId, expectedGeneration);
  const stage = (value: LocalAnalysisStage) => input.onStage?.(value);
  stage('preparing');
  stage('initializing');
  await api.initialize({ signal: input.signal });
  assertGeneration(expectedGeneration);

  let context: CachedContext;
  let freshAuthorization = false;
  if (!offline) {
    const stale =
      onlineValidationRequired ||
      now() - lastOnlineValidationAt > input.policy.offlineAuthorizationSeconds * 1000 ||
      !contextLeaseValid(input);
    if (stale) {
      await api.availability({ signal: input.signal });
      assertGeneration(expectedGeneration);
      ({ context } = await ensureContext(input, false, expectedGeneration, true));
      freshAuthorization = true;
    } else {
      ({ context } = await ensureContext(input, false, expectedGeneration));
    }
    if (freshAuthorization) {
      if (input.accountId) {
        api.noteOnlineAuthorization(input.accountId, input.policy.offlineAuthorizationSeconds);
      }
      contextLeases.set(scopeKey(input), {
        at: now(),
        account: input.accountId,
        version: input.game.version,
        gameId: input.game.id,
      });
      lastOnlineValidationAt = now();
      onlineValidationRequired = false;
    }
  } else {
    ({ context } = await ensureContext(input, true, expectedGeneration));
    if (!contextLeaseValid(input)) {
      throw failure(
        'unauthorized',
        'Offline analysis authorization has expired for this position',
        false,
        'offline-authorization-expired',
      );
    }
  }

  const advisor = input.settings.advisor;
  if (!advisor) throw failure('unsupported-agent', 'No advisor is selected', false);
  const effectiveConfig =
    context.advisor?.effective_config ?? (advisor.config as Record<string, unknown>);
  const seedWithPly = context.advisor?.seed_with_ply ?? input.game.seed !== undefined;
  const state = searchState(context, input.cursor);
  const algorithm = effectiveAlgorithm(effectiveConfig);
  if (!algorithm) {
    throw failure(
      'unsupported-agent',
      effectiveConfig.algorithm === 'mcts'
        ? 'Classical MCTS is not available in local execution; no weaker agent was substituted'
        : 'The selected agent is not available in local execution',
      true,
      'unsupported-agent',
    );
  }
  if (algorithm === 'jarl' && (effectiveConfig.model_id || effectiveConfig.search_wrapper)) {
    throw failure(
      'unsupported-agent',
      'Neural alpha-beta evaluation is not available in local execution',
      true,
      'unsupported-agent',
    );
  }

  let settings: Record<string, unknown>;
  let model: ModelManifest | null = null;
  let provider: 'wasm' | 'webgpu' = 'wasm';
  let fromCache = true;

  if (algorithm === 'jarl') {
    settings = jarlSettings(effectiveConfig, state, seedWithPly);
  } else {
    settings = neuralSettings(effectiveConfig, state, seedWithPly);
    const models = await ensureModels(api, input, offline, expectedGeneration);
    const requestedId = String(effectiveConfig.model_id);
    model =
      models.find(
        (candidate) =>
          candidate.ruleset === input.game.ruleset &&
          candidate.boardSize === input.game.size &&
          candidate.registeredModelIds.includes(requestedId),
      ) ?? null;
    if (!model) {
      throw failure(
        'model-unavailable',
        'No compatible published model is available for the selected model',
        true,
        'model-not-published',
      );
    }
    provider = providerFor(input, model);
    if (offline && !modelLeaseValid(input, model.onnxSha256)) {
      throw failure(
        'unauthorized',
        'The selected model is not authorized for offline analysis',
        false,
        'offline-model-unauthorized',
      );
    }
    if (!api.modelReady(model, provider)) {
      if (offline) {
        throw failure(
          'offline',
          'The selected model session was not initialized before going offline',
          false,
          'offline-model-missing',
        );
      }
      stage('downloading');
      const downloaded = await api.downloadModel(model, {
        signal: input.signal,
        onProgress: input.onDownloadProgress,
      });
      assertGeneration(expectedGeneration);
      fromCache = downloaded.fromCache;
      stage('loading-model');
      modelLeases.set(`${input.accountId ?? ''}:${model.onnxSha256}`, {
        at: now(),
        account: input.accountId,
        sha256: model.onnxSha256,
      });
    }
  }

  stage('searching');
  const result = await runWithProvider(
    api,
    input,
    { state, algorithm, settings, model: model ?? undefined },
    provider,
    offline,
    model,
  );
  assertGeneration(expectedGeneration);
  if (model) {
    lastProviders.set(
      `${model.onnxSha256}:${input.accountId ?? ''}`,
      result.evidence.requestedProvider ?? provider,
    );
    if (!offline) {
      modelLeases.set(`${input.accountId ?? ''}:${model.onnxSha256}`, {
        at: now(),
        account: input.accountId,
        sha256: model.onnxSha256,
      });
    }
  }
  return { ...adaptReport(input, context, result, effectiveConfig, offline), fromCache };
}

/**
 * Execute one logical analysis request locally. Cancellation and stale requests
 * throw `cancelled`; only explicitly classified failures are marked eligible for
 * server fallback.
 */
export async function runLocalAnalysis(input: LocalAnalysisInput): Promise<LocalAnalysisOutcome> {
  if (input.signal.aborted) {
    throw failure('cancelled', 'Analysis was cancelled', false, 'cancelled');
  }
  if (input.policy.mode !== 'browser_only' && input.policy.mode !== 'browser_preferred') {
    throw failure('disabled', 'Browser analysis is not permitted', false, 'disabled');
  }
  if (account !== input.accountId) await setAnalysisAccount(input.accountId);
  const expectedGeneration = generation;
  const startOffline =
    input.offline === true || (typeof navigator !== 'undefined' && navigator.onLine === false);
  try {
    return await localAttempt(input, startOffline, expectedGeneration);
  } catch (error) {
    const classified = classify(error);
    if (classified.category === 'cancelled') throw classified;
    assertGeneration(expectedGeneration);
    if (
      !startOffline &&
      (classified.category === 'unauthorized' ||
        classified.category === 'integrity' ||
        classified.category === 'model-unavailable')
    ) {
      // A known denial, revocation or integrity failure invalidates the affected
      // continuation resources; it must not leave a stale offline lease behind.
      contextLeases.clear();
      modelLeases.clear();
    }
    if (startOffline) {
      // Offline failures never become server fallback or lease renewal.
      throw new LocalAnalysisError(
        classified.category,
        classified.message,
        false,
        classified.code,
        classified.retryable,
      );
    }
    if (classified.category === 'offline' && navigatorOnline() === false) {
      throw new LocalAnalysisError(classified.category, classified.message, false, classified.code);
    }
    if (classified.category === 'offline') {
      // Origin unreachable while the browser still reports online. Reuse only an
      // already-authorized warm context; never treat the failure as fresh auth.
      try {
        return await localAttempt(input, true, expectedGeneration);
      } catch (retry) {
        const second = classify(retry);
        throw second.category === 'cancelled' ? second : classified;
      }
    }
    throw classified;
  }
}

function navigatorOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}

if (typeof window !== 'undefined') {
  window.addEventListener('hnefatafl:session-ended', () => {
    void disposeLocalAnalysis();
  });
  window.addEventListener('online', () => {
    onlineValidationRequired = true;
  });
}

/** Exposed for deterministic tests without weakening production behavior. */
export const __testing = {
  resetCaches() {
    contexts.clear();
    contextBytes = 0;
    abortContextRequests();
    lastProviders.clear();
    contextLeases.clear();
    modelLeases.clear();
    modelCache = null;
  },
  contextStats() {
    return { entries: contexts.size, bytes: contextBytes };
  },
};
