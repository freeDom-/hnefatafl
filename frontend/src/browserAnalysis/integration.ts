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

interface ContextPayload {
  schema: number;
  game_id: string;
  version: number;
  ruleset: string;
  cursor: number;
  position_key: string;
  position_keys?: string[];
  states: Record<string, unknown>[];
  execution?: unknown;
}

interface CachedContext {
  states: Record<string, unknown>[];
  positionKey: string;
  positionKeys: string[];
  version: number;
  fetchedAt: number;
}

const CONTEXT_LIMIT = 64;
const MODEL_CACHE_MS = 5 * 60_000;

let account: string | null = null;
let binding: Promise<void> = Promise.resolve();
let bindSequence = 0;
let runtimePromise: Promise<BrowserAnalysisApi> | null = null;
let modelCache: { account: string | null; at: number; models: ModelManifest[] } | null = null;
const contexts = new Map<string, CachedContext>();
const contextRequests = new Map<string, Promise<CachedContext>>();
const lastProviders = new Map<string, 'wasm' | 'webgpu'>();
let onlineValidationRequired = true;
let lastOnlineValidationAt = 0;

function contextKey(input: Pick<LocalAnalysisInput, 'game' | 'basePath'>): string {
  return `${input.basePath}:${input.game.id}:${input.game.version}`;
}

/**
 * Bind the runtime to the current account. A change invalidates every worker,
 * cached position and offline lease before a new search can start; logout
 * disposes the runtime entirely.
 */
export function setAnalysisAccount(next: string | null): Promise<void> {
  if (next === account) return binding;
  const sequence = ++bindSequence;
  const previous = runtimePromise;
  account = next;
  contexts.clear();
  contextRequests.clear();
  lastProviders.clear();
  modelCache = null;
  onlineValidationRequired = true;
  lastOnlineValidationAt = 0;
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
    if (bindSequence === sequence) {
      await api.invalidateAccountContext();
    }
  })();
  if (next === null) runtimePromise = null;
  return binding;
}

/** Test/logout hook: release the managed runtime and every cached context. */
export async function disposeLocalAnalysis(): Promise<void> {
  bindSequence += 1;
  account = null;
  contexts.clear();
  contextRequests.clear();
  lastProviders.clear();
  modelCache = null;
  const previous = runtimePromise;
  runtimePromise = null;
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

async function getRuntime(accountId: string | null): Promise<BrowserAnalysisApi> {
  if (account !== accountId) await setAnalysisAccount(accountId);
  await binding;
  if (!runtimePromise) {
    const creation = createBrowserAnalysis();
    runtimePromise = creation;
    creation.catch(() => {
      if (runtimePromise === creation) runtimePromise = null;
    });
  }
  return runtimePromise;
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
      // Runtime absence and malformed identity share an error code; identity
      // failures are integrity failures and must fail closed.
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
      // Unsupported settings/agents are normalized before the worker; this is a
      // request incompatibility, never an integrity failure.
      return failure('unsupported-agent', text, true, code, false);
    }
    if (code === 'search-failed') {
      const memory = /memory|resource|exhaust/i.test(text);
      return failure('search', text, memory, code, error.retryable);
    }
    return failure('unknown', text, false, code, false);
  }
  if (error instanceof TypeError) {
    return failure('offline', 'The network is unavailable', false, 'network-unavailable', true);
  }
  return failure('unknown', (error as Error)?.message ?? 'Local analysis failed', false);
}

function executionPolicy(raw: unknown, fallback: ExecutionPolicy): ExecutionPolicy {
  try {
    return normalizeExecutionPolicy(raw, { browserDelivery: fallback.browserDelivery });
  } catch {
    throw failure('integrity', 'The analysis execution policy is invalid', false);
  }
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
  } catch (error) {
    if (signal.aborted) throw failure('cancelled', 'Analysis was cancelled', false, 'cancelled');
    throw error;
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
        'analysis_not_enabled',
      );
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

function validateContext(raw: unknown): ContextPayload {
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
  if (payload.states.length > 4096) {
    throw failure('integrity', 'The analysis context is larger than the supported bound', false);
  }
  if (
    payload.position_keys !== undefined &&
    (!Array.isArray(payload.position_keys) ||
      payload.position_keys.length !== payload.states.length ||
      payload.position_keys.some((key) => typeof key !== 'string' || key.length === 0))
  ) {
    throw failure('integrity', 'The analysis position keys are malformed', false);
  }
  for (const state of payload.states) {
    if (
      !state ||
      typeof state !== 'object' ||
      !Array.isArray((state as { board?: unknown }).board) ||
      typeof (state as { ruleset?: unknown }).ruleset !== 'string'
    ) {
      throw failure('integrity', 'A serialized analysis position is malformed', false);
    }
  }
  return payload;
}

async function ensureContext(
  input: LocalAnalysisInput,
  offline: boolean,
  force = false,
): Promise<CachedContext> {
  const key = contextKey(input);
  if (force) contexts.delete(key);
  const cached = contexts.get(key);
  if (cached) return cached;
  if (offline) {
    throw failure(
      'offline',
      'This position was not prepared for offline analysis',
      false,
      'offline-context-missing',
    );
  }
  const pending = contextRequests.get(key);
  if (pending) return pending;
  const request = (async () => {
    const path =
      input.basePath === '/history/online'
        ? `/api/history/online/${input.game.id}/analysis-context?cursor=${input.cursor}`
        : `/api/games/${input.game.id}/analysis-context?cursor=${input.cursor}`;
    const payload = validateContext(await fetchJson(path, input.signal));
    if (payload.game_id !== input.game.id || payload.ruleset !== input.game.ruleset) {
      throw failure('integrity', 'The analysis context does not match this game', false);
    }
    if (payload.execution !== undefined && payload.execution !== null) {
      const next = executionPolicy(payload.execution, input.policy);
      if (next.mode !== input.policy.mode) {
        throw failure(
          'disabled',
          'The analysis execution policy changed. Retry analysis.',
          false,
          'policy-changed',
          true,
        );
      }
    }
    const context: CachedContext = {
      states: payload.states,
      positionKey: payload.position_key,
      positionKeys:
        Array.isArray(payload.position_keys) &&
        payload.position_keys.length === payload.states.length
          ? payload.position_keys
          : payload.states.map(() => payload.position_key),
      version: payload.version,
      fetchedAt: Date.now(),
    };
    contexts.set(key, context);
    while (contexts.size > CONTEXT_LIMIT) {
      const oldest = contexts.keys().next().value;
      if (oldest === undefined || oldest === key) break;
      contexts.delete(oldest);
    }
    return context;
  })();
  contextRequests.set(key, request);
  try {
    return await request;
  } finally {
    if (contextRequests.get(key) === request) contextRequests.delete(key);
  }
}

async function ensureModels(
  api: BrowserAnalysisApi,
  input: LocalAnalysisInput,
  offline: boolean,
): Promise<ModelManifest[]> {
  if (modelCache && modelCache.account === input.accountId) {
    if (offline || Date.now() - modelCache.at < MODEL_CACHE_MS) return modelCache.models;
  }
  if (offline) {
    throw failure(
      'offline',
      'The analysis model catalogue is not available offline',
      false,
      'offline-models-missing',
    );
  }
  const models = await api.listModels();
  modelCache = { account: input.accountId, at: Date.now(), models };
  return models;
}

function neuralRequested(config: Record<string, unknown>): boolean {
  return (
    (config.algorithm === 'neural' && (config.search_wrapper ?? 'mcts') === 'mcts') ||
    (config.algorithm === 'mcts' && Boolean(config.model_id))
  );
}

function jarlRequested(config: Record<string, unknown>): boolean {
  return config.algorithm === 'alpha_beta' || config.algorithm === 'alphabeta';
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function effectiveSeed(input: LocalAnalysisInput, config: Record<string, unknown>): number {
  const base = Number.isInteger(config.seed)
    ? Number(config.seed)
    : input.game.seed !== undefined
      ? input.game.seed
      : 7;
  return (base + (input.game.seed !== undefined ? input.game.ply : 0)) >>> 0;
}

function jarlSettings(input: LocalAnalysisInput, config: Record<string, unknown>) {
  const depth = numeric(config.depth) ?? 5;
  const seconds = numeric(config.time_limit) ?? 2;
  if (!Number.isInteger(depth) || depth < 1 || depth > 5 || seconds > 3 || seconds < 0.01) {
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
    seed: effectiveSeed(input, config),
  };
  if (typeof config.heuristic === 'string') settings.heuristic = config.heuristic;
  if (config.search && typeof config.search === 'object') settings.search = config.search;
  return settings;
}

function neuralSettings(input: LocalAnalysisInput, config: Record<string, unknown>) {
  const iterations = numeric(config.iterations) ?? 128;
  const seconds = numeric(config.time_limit) ?? 1;
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
    seed: effectiveSeed(input, config),
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
      // Bounded recovery: the validated WASM baseline is attempted before any
      // server fallback. Integrity and cancellation never take this path.
      return attempt('wasm');
    }
    throw classified;
  }
}

function valueForSide(state: Record<string, unknown>, value: number): number {
  const turn = state.turn;
  return turn === 'defenders' ? value : -value;
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
  const current = valueForSide(state, value);
  const advisor = input.settings.advisor;
  if (!advisor) throw failure('unsupported-agent', 'No advisor is selected', false);
  const evaluation: AgentEvaluation = {
    rune: advisor.rune,
    agent_id: 'advisor',
    name: advisor.name,
    role: 'advisor',
    profile_id: advisor.profile_id,
    profile_name: advisor.profile_name,
    model_name: advisor.model_name,
    config: {
      ...config,
      ...result.settings,
    } as AgentEvaluation['config'],
    source: source as AgentEvaluation['source'],
    value,
    win_probability: Math.max(0, Math.min(1, (current + 1) / 2)),
    estimated: source !== 'terminal',
    calibrated: source === 'terminal',
    best_move: best,
  };
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

async function localAttempt(
  input: LocalAnalysisInput,
  offline: boolean,
): Promise<LocalAnalysisOutcome> {
  const api = await getRuntime(input.accountId);
  const stage = (value: LocalAnalysisStage) => input.onStage?.(value);
  stage('preparing');
  stage('initializing');
  await api.initialize();
  if (!offline && input.accountId) {
    api.noteOnlineAuthorization(input.accountId, input.policy.offlineAuthorizationSeconds);
  }
  let context: CachedContext;
  if (!offline) {
    const stale =
      onlineValidationRequired ||
      Date.now() - lastOnlineValidationAt > input.policy.offlineAuthorizationSeconds * 1000;
    if (stale) {
      // Revalidate the authenticated session and the runtime identity at the
      // origin before further local work after reconnection or account changes.
      await api.availability();
      context = await ensureContext(input, false, true);
      lastOnlineValidationAt = Date.now();
      onlineValidationRequired = false;
    } else {
      context = await ensureContext(input, false);
    }
    if (input.accountId) {
      // A successful authorized position fetch is the narrow online boundary
      // for warm-offline work. The lease is bound to this account and runtime.
      api.noteOnlineAuthorization(input.accountId, input.policy.offlineAuthorizationSeconds);
    }
  } else {
    context = await ensureContext(input, true);
  }

  const advisor = input.settings.advisor;
  if (!advisor) throw failure('unsupported-agent', 'No advisor is selected', false);
  const config = advisor.config as Record<string, unknown>;
  let algorithm: 'jarl' | 'neural_mcts';
  let settings: Record<string, unknown>;
  let model: ModelManifest | null = null;
  let provider: 'wasm' | 'webgpu' = 'wasm';
  let fromCache = true;

  if (jarlRequested(config)) {
    algorithm = 'jarl';
    settings = jarlSettings(input, config);
  } else if (neuralRequested(config) && Boolean(config.model_id)) {
    algorithm = 'neural_mcts';
    settings = neuralSettings(input, config);
    const models = await ensureModels(api, input, offline);
    const requestedId = String(config.model_id);
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
        `No compatible published model is available for the selected model (${requestedId}; published: ${
          models
            .map(
              (candidate) =>
                `${candidate.ruleset}/${candidate.boardSize}:${candidate.registeredModelIds.join(',') || 'unmapped'}`,
            )
            .join(' | ') || 'none'
        })`,
        true,
        'model-not-published',
      );
    }
    provider = providerFor(input, model);
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
      fromCache = downloaded.fromCache;
      stage('loading-model');
    }
  } else if (config.algorithm === 'mcts') {
    throw failure(
      'unsupported-agent',
      'Classical MCTS is not available in local execution; no weaker agent was substituted',
      true,
      'unsupported-agent',
    );
  } else {
    throw failure(
      'unsupported-agent',
      'The selected agent is not available in local execution',
      true,
      'unsupported-agent',
    );
  }

  const state = context.states[input.cursor];
  if (!state) {
    throw failure('integrity', 'The analyzed position is unavailable', false);
  }
  stage('searching');
  const result = await runWithProvider(
    api,
    input,
    {
      state,
      algorithm,
      settings,
      model: model ?? undefined,
    },
    provider,
    offline,
    model,
  );
  if (!offline && input.accountId) {
    api.noteOnlineAuthorization(input.accountId, input.policy.offlineAuthorizationSeconds);
  }
  if (model) {
    lastProviders.set(
      `${model.onnxSha256}:${input.accountId ?? ''}`,
      result.evidence.requestedProvider ?? provider,
    );
  }
  const outcome = adaptReport(input, context, result, config, offline);
  return { ...outcome, fromCache };
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
  const startOffline =
    input.offline === true || (typeof navigator !== 'undefined' && navigator.onLine === false);
  try {
    return await localAttempt(input, startOffline);
  } catch (error) {
    const classified = classify(error);
    if (classified.category === 'cancelled') throw classified;
    if (startOffline) {
      if (classified.category === 'offline') throw classified;
      // A warm offline attempt may fail for a non-network reason (integrity,
      // missing model session). Preserve that classification; none of them is
      // eligible for server fallback while disconnected.
      throw new LocalAnalysisError(
        classified.category,
        classified.message,
        false,
        classified.code,
        classified.retryable,
      );
    }
    if (classified.category === 'offline' && navigatorOnline() === false) {
      throw classified;
    }
    if (classified.category === 'offline') {
      // The browser reported online but every request failed at the transport
      // layer. Try the already-initialized warm context once without network.
      try {
        return await localAttempt(input, true);
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
  // Loaded only after local analysis was used, so server-only and disabled
  // deployments never register this listener or import the runtime.
  window.addEventListener('hnefatafl:session-ended', () => {
    void disposeLocalAnalysis();
  });
  window.addEventListener('online', () => {
    // The next local search must revalidate authorization and runtime identity
    // before further work; the cached offline lease is not proof of permission.
    onlineValidationRequired = true;
  });
}

/** Exposed for deterministic tests without weakening production behavior. */
export const __testing = {
  resetCaches() {
    contexts.clear();
    contextRequests.clear();
    lastProviders.clear();
    modelCache = null;
  },
};
