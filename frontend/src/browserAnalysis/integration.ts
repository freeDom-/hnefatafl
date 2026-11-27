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
export {
  addRetirement,
  retirementFor,
  retirementKey,
  settleRetirement,
  validReceipt,
  validStatus,
} from '../analysisRetirements';
export type { AnalysisReceipt, AnalysisStatus, RetirementRecord } from '../analysisRetirements';

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
  | 'busy'
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
  name?: string;
  profile_id?: string;
  model_name?: string;
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
  account: string | null;
  gameId: string;
  version: number;
  revision: number;
  policyMode: ExecutionPolicy['mode'];
  offlineSeconds: number;
  runtimeId: string;
  expiresAt: number;
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
let retirement: Promise<void> = Promise.resolve();
let bindSequence = 0;
let policyRevision = 0;
let currentPolicy: ExecutionPolicy | null = null;
let searchQueue: Promise<unknown> = Promise.resolve();
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
const modelLeases = new Map<
  string,
  {
    account: string | null;
    sha256: string;
    publicationId: string;
    runtimeId: string;
    expiresAt: number;
  }
>();
let lastPolicySignature: string | null = null;
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
  lastPolicySignature = null;
  onlineValidationRequired = true;
  lastOnlineValidationAt = 0;
}

function chainRetirement(task: () => Promise<void>): Promise<void> {
  const previous = retirement;
  retirement = previous.catch(() => undefined).then(task);
  return retirement;
}

function retireRuntime(reason: string): Promise<void> {
  const previous = runtimePromise;
  runtimePromise = null;
  runtimeGeneration = -1;
  generation += 1;
  clearAccountState();
  policyRevision += 1;
  searchQueue = Promise.resolve();
  return chainRetirement(async () => {
    if (!previous) return;
    try {
      const api = await previous;
      await api.clearOfflineAuthorization();
      await api.dispose();
    } catch {
      // A failed creation has nothing left to retire.
    }
    void reason;
  });
}

function exclusiveSearch<T>(task: () => Promise<T>, signal: AbortSignal): Promise<T> {
  const run = searchQueue
    .catch(() => undefined)
    .then(() => {
      if (signal.aborted) {
        throw failure('cancelled', 'Analysis was cancelled', false, 'cancelled');
      }
      return task();
    });
  searchQueue = run.catch(() => undefined);
  return run;
}

/**
 * Apply a server-authoritative policy change outside a request. Known policy
 * changes must invalidate continuation authorizations even when no further local
 * search runs. An obsolete account request is ignored and cannot mutate the
 * current account's policy state.
 */
export function setAnalysisPolicy(
  policy: ExecutionPolicy,
  accountId: string | null,
): Promise<void> {
  if (account !== accountId) return Promise.resolve();
  policyRevision += 1;
  currentPolicy = policy;
  const signature = policySignature(policy);
  if (lastPolicySignature === signature) return binding;
  lastPolicySignature = signature;
  contextLeases.clear();
  modelLeases.clear();
  lastProviders.clear();
  onlineValidationRequired = true;
  lastOnlineValidationAt = 0;
  if (!policy.browserExecution) {
    return retireRuntime('Execution policy no longer permits browser analysis');
  }
  const current = runtimePromise;
  if (current) {
    void current.then((api) => api.clearOfflineAuthorization()).catch(() => undefined);
  }
  return binding;
}

/** Explicit authorization denial invalidates every continuation artifact. */
export function invalidateAnalysisAuthorization(accountId: string | null): void {
  if (account !== accountId) return;
  contextLeases.clear();
  modelLeases.clear();
  lastProviders.clear();
  onlineValidationRequired = true;
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
  policyRevision += 1;
  searchQueue = Promise.resolve();
  const targetGeneration = generation;
  const previous = runtimePromise;
  account = next;
  clearAccountState();
  binding = (async () => {
    await retirement;
    if (!previous) return;
    let api: BrowserAnalysisApi;
    try {
      api = await previous;
    } catch {
      return;
    }
    if (next === null) {
      await chainRetirement(async () => {
        await api.clearOfflineAuthorization();
        await api.dispose();
      });
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
export function disposeLocalAnalysis(): Promise<void> {
  bindSequence += 1;
  generation += 1;
  policyRevision += 1;
  searchQueue = Promise.resolve();
  account = null;
  clearAccountState();
  const previous = runtimePromise;
  runtimePromise = null;
  runtimeGeneration = -1;
  const disposal = chainRetirement(async () => {
    if (!previous) return;
    try {
      const api = await previous;
      await api.clearOfflineAuthorization();
      await api.dispose();
    } catch {
      // A failed creation has nothing to retire.
    }
  });
  binding = disposal;
  return disposal;
}

async function getRuntime(accountId: string | null, expected: number): Promise<BrowserAnalysisApi> {
  await binding;
  await retirement;
  assertGeneration(expected);
  if (account !== accountId) {
    throw failure(
      'cancelled',
      'Analysis belongs to an obsolete account context',
      false,
      'account-context-changed',
    );
  }
  if (!runtimePromise) {
    const creation = createBrowserAnalysis();
    runtimePromise = creation;
    const creationGeneration = expected;
    creation.then(
      (api) => {
        if (generation !== creationGeneration || account !== accountId) {
          void chainRetirement(async () => {
            await api.clearOfflineAuthorization();
            await api.dispose();
          });
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
    if (code === 'network-unavailable') {
      return failure('offline', text, false, code, true);
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
    if (code === 'superseded') return failure('busy', text, false, code, true);
    if (code === 'unsupported') {
      // Runtime-internal unsupported/disposed states are not evidence that a
      // server fallback is appropriate; user-requested incompatibilities are
      // rejected before entering the runtime with a structured decision.
      return failure('initialization', text, false, code, false);
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
    let errorCode = '';
    try {
      const payload = (await response.clone().json()) as { code?: unknown };
      errorCode = typeof payload?.code === 'string' ? payload.code : '';
    } catch {
      // A non-JSON error keeps the status-based classification.
    }
    if (response.status === 401 || response.status === 403) {
      throw failure('unauthorized', 'Sign in to analyze this position', false, 'unauthorized');
    }
    if (response.status === 409) {
      throw failure(
        'disabled',
        errorCode === 'analysis_revision_stale'
          ? 'The move advisor changed. Refresh analysis settings.'
          : 'Analysis is not available for this position',
        false,
        errorCode || 'analysis_context_changed',
        true,
      );
    }
    if (response.status === 413 || response.status === 422) {
      throw failure(
        'integrity',
        'The analysis context is too large or invalid',
        false,
        errorCode || 'analysis_context_invalid',
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

function canonicalJson(value: unknown, floatKeys?: Set<string>, keyName?: string): string {
  if (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    keyName !== undefined &&
    floatKeys?.has(keyName)
  ) {
    // Python's json.dumps renders a float such as 1.0 as "1.0"; JavaScript has
    // already lost that distinction, so known canonical float fields are
    // rendered explicitly for cross-language identity compatibility.
    return `${value}.0`;
  }
  if (Array.isArray(value))
    return `[${value.map((item) => canonicalJson(item, floatKeys)).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${canonicalJson(
            (value as Record<string, unknown>)[key],
            floatKeys,
            key,
          )}`,
      )
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

async function sha256Hex(value: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw failure('integrity', 'Canonical position verification is unavailable', false);
  }
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

function completeState(
  state: Record<string, unknown>,
  historyKeys: string[],
  index: number,
): Record<string, unknown> {
  const ply = Number.isInteger(state.ply) ? Number(state.ply) : index;
  return { ...state, history: historyKeys.slice(0, Math.max(1, ply + 1)) };
}

async function validateContext(raw: unknown, input: LocalAnalysisInput): Promise<ContextPayload> {
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
  if (payload.position_keys[input.cursor] !== payload.position_key) {
    throw failure('integrity', 'The analysis position key association is inconsistent', false);
  }
  const boardSize = Math.round(Math.sqrt(input.game.board.length));
  if (
    boardSize * boardSize !== input.game.board.length ||
    payload.states.length !== payload.position_keys.length
  ) {
    throw failure('integrity', 'The analysis board/context shape is invalid', false);
  }
  for (const [index, state] of payload.states.entries()) {
    if (
      !state ||
      typeof state !== 'object' ||
      !Array.isArray((state as { board?: unknown }).board) ||
      (state as { board: unknown[] }).board.length !== input.game.board.length ||
      (state as { board: unknown[] }).board.some((piece) => typeof piece !== 'string') ||
      (state as { ruleset?: unknown }).ruleset !== payload.ruleset ||
      ((state as { turn?: unknown }).turn !== 'attackers' &&
        (state as { turn?: unknown }).turn !== 'defenders') ||
      (state as { ply?: unknown }).ply !== index
    ) {
      throw failure('integrity', 'A serialized analysis position is malformed', false);
    }
    const board = (state as { board: string[] }).board;
    const expectedHistoryKey = `${(state as { turn: string }).turn}:${board.join('')}`;
    if (payload.history_keys[index] !== expectedHistoryKey) {
      throw failure('integrity', 'The analysis repetition history is inconsistent', false);
    }
  }
  const analyzed = payload.states[input.cursor] as Record<string, unknown>;
  if (
    JSON.stringify(analyzed.board) !== JSON.stringify(input.game.board) ||
    analyzed.turn !== input.game.turn ||
    Number(analyzed.ply) !== input.cursor
  ) {
    throw failure(
      'integrity',
      'The analysis context does not describe the displayed position',
      false,
    );
  }
  const canonical = completeState(analyzed, payload.history_keys, input.cursor);
  if ((await sha256Hex(canonicalJson(canonical))) !== payload.position_key) {
    throw failure('integrity', 'The analysis position identity is inconsistent', false);
  }
  const advisor = payload.advisor;
  if (
    advisor === null ||
    advisor === undefined ||
    typeof advisor !== 'object' ||
    !advisor.effective_config ||
    typeof advisor.effective_config !== 'object' ||
    typeof advisor.show_win_chances !== 'boolean' ||
    typeof advisor.seed_with_ply !== 'boolean' ||
    !Number.isInteger(advisor.revision) ||
    advisor.revision !== input.settings.revision
  ) {
    throw failure(
      'integrity',
      'Canonical advisor configuration or revision is missing or stale',
      false,
    );
  }
  const requested = input.settings.advisor;
  const requestedConfig = requested?.config as Record<string, unknown> | undefined;
  const effective = advisor.effective_config;
  if (
    requested &&
    ((advisor.profile_id !== undefined &&
      requested.profile_id !== undefined &&
      advisor.profile_id !== requested.profile_id) ||
      (advisor.name !== undefined &&
        requested.name !== undefined &&
        advisor.name !== requested.name) ||
      (requestedConfig?.model_id !== undefined && effective.model_id !== requestedConfig.model_id))
  ) {
    throw failure('integrity', 'The analysis advisor identity changed', false);
  }
  const normalizeAlgorithm = (value: unknown) => (value === 'alphabeta' ? 'alpha_beta' : value);
  if (
    requestedConfig?.algorithm !== undefined &&
    normalizeAlgorithm(effective.algorithm) !== normalizeAlgorithm(requestedConfig.algorithm)
  ) {
    throw failure('integrity', 'The analysis advisor algorithm changed', false);
  }
  if (!payload.execution || typeof payload.execution !== 'object') {
    throw failure(
      'integrity',
      'The canonical analysis execution-policy projection is missing',
      false,
    );
  }
  {
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

function trimLeases(): void {
  while (contextLeases.size > 128) {
    const oldest = contextLeases.keys().next().value;
    if (oldest === undefined) break;
    contextLeases.delete(oldest);
  }
  while (modelLeases.size > 16) {
    const oldest = modelLeases.keys().next().value;
    if (oldest === undefined) break;
    modelLeases.delete(oldest);
  }
  while (lastProviders.size > 16) {
    const oldest = lastProviders.keys().next().value;
    if (oldest === undefined) break;
    lastProviders.delete(oldest);
  }
}

function cacheContext(key: string, context: CachedContext): void {
  const existing = contexts.get(key);
  if (existing) {
    contextBytes -= existing.bytes;
    contexts.delete(key);
  }
  if (context.bytes > CONTEXT_BYTE_LIMIT) {
    // A single oversized context is usable for this request but never retained
    // in either the byte-bounded cache or its authorization registry.
    contextLeases.delete(key);
    return;
  }
  contexts.set(key, context);
  contextBytes += context.bytes;
  while (contexts.size > CONTEXT_LIMIT || contextBytes > CONTEXT_BYTE_LIMIT) {
    const oldest = contexts.keys().next().value;
    if (oldest === undefined || oldest === key) break;
    const removed = contexts.get(oldest);
    if (removed) contextBytes -= removed.bytes;
    contexts.delete(oldest);
    contextLeases.delete(oldest);
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
  if (!shared || shared.generation !== expectedGeneration || shared.controller.signal.aborted) {
    const controller = new AbortController();
    const promise = (async () => {
      const path =
        input.basePath === '/history/online'
          ? `/api/history/online/${input.game.id}/analysis-context?cursor=${input.cursor}&version=${input.game.version}&revision=${input.settings.revision}`
          : `/api/games/${input.game.id}/analysis-context?cursor=${input.cursor}&version=${input.game.version}&revision=${input.settings.revision}`;
      const raw = await fetchJson(path, controller.signal);
      if (controller.signal.aborted) {
        throw failure('cancelled', 'Analysis was cancelled', false, 'cancelled');
      }
      const payload = await validateContext(raw, input);
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

function policySignature(policy: ExecutionPolicy): string {
  return JSON.stringify({
    mode: policy.mode,
    browserExecution: policy.browserExecution,
    serverComputation: policy.serverComputation,
    fallback: policy.fallback,
    webgpu: policy.webgpu,
    offline: policy.offlineAuthorizationSeconds,
    limits: policy.limits,
  });
}

function contextLeaseValid(input: LocalAnalysisInput, runtimeId: string): boolean {
  const lease = contextLeases.get(scopeKey(input));
  return Boolean(
    lease &&
      lease.account === input.accountId &&
      lease.gameId === input.game.id &&
      lease.version === input.game.version &&
      lease.revision === input.settings.revision &&
      lease.policyMode === input.policy.mode &&
      lease.offlineSeconds === input.policy.offlineAuthorizationSeconds &&
      lease.runtimeId === runtimeId &&
      now() <= lease.expiresAt,
  );
}

function modelLeaseValid(
  input: LocalAnalysisInput,
  model: ModelManifest,
  runtimeId: string,
): boolean {
  const lease = modelLeases.get(
    `${input.accountId ?? ''}:${model.publicationId}:${model.onnxSha256}`,
  );
  return Boolean(
    lease &&
      lease.account === input.accountId &&
      lease.sha256 === model.onnxSha256 &&
      lease.publicationId === model.publicationId &&
      lease.runtimeId === runtimeId &&
      now() <= lease.expiresAt,
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

function canonicalNumber(
  config: Record<string, unknown>,
  field: string,
  bounds: { min: number; max: number; integer?: boolean; unsupported?: boolean },
): number {
  const value = config[field];
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    (bounds.integer === true && !Number.isInteger(value))
  ) {
    throw failure(
      'integrity',
      `Canonical analysis setting ${field} is missing or malformed`,
      false,
      'canonical-config',
    );
  }
  if (value < bounds.min || value > bounds.max) {
    throw failure(
      bounds.unsupported === false ? 'integrity' : 'unsupported-budget',
      `The requested analysis setting ${field} is outside the supported local range`,
      bounds.unsupported !== false,
      bounds.unsupported === false ? 'canonical-config' : 'local-budget',
    );
  }
  return value;
}

function canonicalSeed(config: Record<string, unknown>): number {
  const seed = config.seed;
  if (typeof seed !== 'number' || !Number.isInteger(seed) || seed < 0 || seed > 0xffffffff) {
    throw failure(
      'integrity',
      'Canonical analysis seed is missing or invalid',
      false,
      'canonical-config',
    );
  }
  return seed;
}

function seedForState(
  config: Record<string, unknown>,
  state: Record<string, unknown>,
  seedWithPly: boolean,
): number {
  return (canonicalSeed(config) + (seedWithPly ? Number(state.ply ?? 0) : 0)) >>> 0;
}

function jarlSettings(
  config: Record<string, unknown>,
  state: Record<string, unknown>,
  seedWithPly: boolean,
) {
  rejectUnsupportedConfig(config, JARL_CONFIG_FIELDS);
  if (config.evaluation !== 'heuristic') {
    throw failure(
      'integrity',
      'Canonical Jarl evaluation mode is invalid',
      false,
      'canonical-config',
    );
  }
  const depth = canonicalNumber(config, 'depth', { min: 1, max: 5, integer: true });
  const seconds = canonicalNumber(config, 'time_limit', { min: 0.01, max: 5 });
  canonicalSeed(config);
  if (
    config.heuristic !== undefined &&
    (typeof config.heuristic !== 'string' ||
      !['shield_maiden', 'shield_bearer'].includes(config.heuristic))
  ) {
    throw failure('integrity', 'Canonical Jarl heuristic is invalid', false, 'canonical-config');
  }
  if (
    config.search !== undefined &&
    config.search !== null &&
    (typeof config.search !== 'object' || Array.isArray(config.search))
  ) {
    throw failure(
      'integrity',
      'Canonical Jarl search settings are invalid',
      false,
      'canonical-config',
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
  if (config.evaluation !== 'neural' || typeof config.model_id !== 'string' || !config.model_id) {
    throw failure(
      'integrity',
      'Canonical neural model identity is missing or malformed',
      false,
      'canonical-config',
    );
  }
  const iterations = canonicalNumber(config, 'iterations', { min: 1, max: 4096, integer: true });
  const seconds = canonicalNumber(config, 'time_limit', { min: 0.05, max: 10 });
  const exploration = canonicalNumber(config, 'exploration', {
    min: 0,
    max: 10,
    unsupported: false,
  });
  canonicalSeed(config);
  if (config.rollout_depth !== undefined) {
    canonicalNumber(config, 'rollout_depth', { min: 0, max: 50, integer: true });
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
    exploration,
  };
}

function providerCacheKey(
  model: ModelManifest,
  accountId: string | null,
  runtimeId: string,
): string {
  return `${accountId ?? ''}:${model.publicationId}:${model.onnxSha256}:${runtimeId}`;
}

function rememberProvider(key: string, provider: 'wasm' | 'webgpu'): void {
  lastProviders.delete(key);
  lastProviders.set(key, provider);
  while (lastProviders.size > 16) {
    const oldest = lastProviders.keys().next().value;
    if (oldest === undefined) break;
    lastProviders.delete(oldest);
  }
}

function providerFor(
  input: LocalAnalysisInput,
  model: ModelManifest | null,
  runtimeId: string,
): 'wasm' | 'webgpu' {
  if (!model || !input.policy.webgpu) return 'wasm';
  const previous = lastProviders.get(providerCacheKey(model, input.accountId, runtimeId));
  if (previous) return previous;
  return typeof navigator !== 'undefined' && Boolean((navigator as { gpu?: unknown }).gpu)
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
): Promise<{ result: SearchResult; provider: 'wasm' | 'webgpu' }> {
  const attempt = (next: 'wasm' | 'webgpu') =>
    api.runSearch(
      { ...request, provider: model ? next : undefined },
      {
        signal: input.signal,
        offline: offline ? { accountId: input.accountId ?? '' } : undefined,
      },
    );
  try {
    return { result: await attempt(provider), provider };
  } catch (error) {
    const classified = classify(error);
    if (
      !offline &&
      provider === 'webgpu' &&
      (classified.category === 'browser-unsupported' ||
        classified.category === 'initialization' ||
        classified.code === 'capability-missing')
    ) {
      return { result: await attempt('wasm'), provider: 'wasm' };
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

async function validateResultIdentity(
  algorithm: 'jarl' | 'neural_mcts',
  state: Record<string, unknown>,
  result: SearchResult,
  settings: Record<string, unknown>,
): Promise<void> {
  const identity = result.positionIdentity;
  if (typeof identity !== 'string' || !/^[a-f0-9]{64}$/.test(identity)) {
    throw failure('integrity', 'The local result has no canonical position identity', false);
  }
  if (algorithm === 'jarl') {
    const effective = { algorithm: 'alpha_beta', ...settings, ...result.settings };
    const expected = await sha256Hex(
      canonicalJson(
        { state, settings: effective, protocol: 1 },
        new Set(['time_limit', 'exploration', 'temperature']),
      ),
    );
    if (identity !== expected) {
      throw failure(
        'integrity',
        'The local result belongs to a different position or configuration',
        false,
      );
    }
  }
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
  const requireReady = (status: {
    state: string;
    runtimeId?: string | null;
    error?: { message?: string; code?: string; retryable?: boolean } | null;
  }): string => {
    if (status.state === 'disabled') {
      throw failure('disabled', 'Browser analysis is disabled', false, 'disabled');
    }
    if (status.state === 'unavailable') {
      throw failure(
        'browser-unsupported',
        status.error?.message ?? 'The browser analysis runtime is unavailable',
        true,
        status.error?.code ?? 'runtime-missing',
        true,
      );
    }
    if (status.state === 'error') {
      throw failure(
        'initialization',
        status.error?.message ?? 'The browser analysis runtime failed',
        true,
        status.error?.code ?? 'initialization-failed',
        true,
      );
    }
    if (status.state === 'initializing' || status.state === 'searching') {
      // A healthy runtime busy with compatible work is not unsupported and must
      // never trigger a server fallback merely because it is occupied.
      throw failure('busy', 'The browser analysis runtime is busy', false, 'runtime-busy', true);
    }
    if (status.state !== 'ready') {
      throw failure('integrity', 'The browser analysis runtime state is invalid', false);
    }
    return status.runtimeId ?? api.status().runtimeId ?? '';
  };
  let runtimeId = requireReady(await api.initialize({ signal: input.signal }));
  assertGeneration(expectedGeneration);

  let context: CachedContext;
  let freshAuthorization = false;
  if (!offline) {
    const stale =
      onlineValidationRequired ||
      now() - lastOnlineValidationAt > input.policy.offlineAuthorizationSeconds * 1000 ||
      !contextLeaseValid(input, runtimeId);
    if (stale) {
      await api.availability({ signal: input.signal });
      assertGeneration(expectedGeneration);
      const revalidated = api.status();
      if (String(revalidated.runtimeId ?? '') !== runtimeId || revalidated.state !== 'ready') {
        runtimeId = requireReady(await api.initialize({ signal: input.signal }));
        assertGeneration(expectedGeneration);
      }
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
        account: input.accountId,
        gameId: input.game.id,
        version: input.game.version,
        revision: input.settings.revision,
        policyMode: input.policy.mode,
        offlineSeconds: input.policy.offlineAuthorizationSeconds,
        runtimeId,
        expiresAt: now() + input.policy.offlineAuthorizationSeconds * 1000,
      });
      lastOnlineValidationAt = now();
      onlineValidationRequired = false;
      trimLeases();
    }
  } else {
    ({ context } = await ensureContext(input, true, expectedGeneration));
    if (!contextLeaseValid(input, runtimeId)) {
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
  const effectiveConfig = context.advisor?.effective_config;
  if (!effectiveConfig) {
    throw failure('integrity', 'Canonical advisor configuration is unavailable', false);
  }
  const seedWithPly = context.advisor?.seed_with_ply === true;
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
    provider = providerFor(input, model, runtimeId);
    if (offline && !modelLeaseValid(input, model, runtimeId)) {
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
    }
    if (freshAuthorization) {
      modelLeases.set(`${input.accountId ?? ''}:${model.publicationId}:${model.onnxSha256}`, {
        account: input.accountId,
        sha256: model.onnxSha256,
        publicationId: model.publicationId,
        runtimeId,
        expiresAt: now() + input.policy.offlineAuthorizationSeconds * 1000,
      });
      trimLeases();
    }
  }

  stage('searching');
  const policyBeforeSearch = policyRevision;
  const ran = await exclusiveSearch(() => {
    if (policyRevision !== policyBeforeSearch) {
      throw failure(
        'disabled',
        'The analysis execution policy changed before the local search started',
        false,
        'policy-changed',
        true,
      );
    }
    return runWithProvider(
      api,
      input,
      { state, algorithm, settings, model: model ?? undefined },
      provider,
      offline,
      model,
    );
  }, input.signal);
  assertGeneration(expectedGeneration);
  const result = ran.result;
  await validateResultIdentity(algorithm, state, result, settings);
  if (model) {
    rememberProvider(providerCacheKey(model, input.accountId, runtimeId), ran.provider);
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
  const signature = policySignature(input.policy);
  if (currentPolicy !== null && policySignature(currentPolicy) !== signature) {
    throw failure(
      'disabled',
      'The analysis execution policy changed. Retry analysis.',
      false,
      'policy-changed',
      true,
    );
  }
  const policyAtStart = policyRevision;
  const expectedGeneration = generation;
  if (account !== input.accountId) {
    throw failure(
      'cancelled',
      'Analysis belongs to an obsolete account context',
      false,
      'account-context-changed',
    );
  }
  if (policyRevision !== policyAtStart) {
    throw failure(
      'disabled',
      'The analysis execution policy changed before local analysis started',
      false,
      'policy-changed',
      true,
    );
  }
  assertGeneration(expectedGeneration);
  if (lastPolicySignature !== null && lastPolicySignature !== signature) {
    // A policy passed directly by the caller also invalidates continuation
    // authorizations issued under the older policy.
    contextLeases.clear();
    modelLeases.clear();
    lastProviders.clear();
  }
  lastPolicySignature = signature;
  const startOffline =
    input.offline === true || (typeof navigator !== 'undefined' && navigator.onLine === false);
  try {
    return await localAttempt(input, startOffline, expectedGeneration);
  } catch (error) {
    const classified = classify(error);
    if (classified.category === 'cancelled') throw classified;
    if (classified.category === 'busy') throw classified;
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
    lastPolicySignature = null;
    currentPolicy = null;
    policyRevision = 0;
    searchQueue = Promise.resolve();
    onlineValidationRequired = true;
    lastOnlineValidationAt = 0;
  },
  contextStats() {
    return { entries: contexts.size, bytes: contextBytes };
  },
};
