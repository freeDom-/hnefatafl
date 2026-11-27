import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
import type { AnalyticsSettings } from '../analysisTypes';
import type { Game } from '../types';
import type { ExecutionPolicy } from '../analysisPolicy';
import { BrowserAnalysisError } from './contracts';

const createBrowserAnalysis = vi.fn();

vi.mock('./entry', async () => {
  const contracts = await vi.importActual<typeof import('./contracts')>('./contracts');
  return {
    ...contracts,
    createBrowserAnalysis: (...args: unknown[]) => createBrowserAnalysis(...args),
  };
});

import {
  LocalAnalysisError,
  __testing,
  disposeLocalAnalysis,
  runLocalAnalysis,
  setAnalysisAccount,
  setAnalysisPolicy,
  type LocalAnalysisInput,
} from './integration';

const state = {
  ruleset: 'brandubh',
  board: Array.from({ length: 49 }, (_, index) => (index === 24 ? 'K' : '.')),
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
  history: ['attackers:' + '.'.repeat(49)],
};

function canonical(value: unknown, floatKeys?: Set<string>, keyName?: string): string {
  if (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    keyName !== undefined &&
    floatKeys?.has(keyName)
  )
    return `${value}.0`;
  if (Array.isArray(value)) return `[${value.map((item) => canonical(item, floatKeys)).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${canonical(
            (value as Record<string, unknown>)[key],
            floatKeys,
            key,
          )}`,
      )
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

function digest(value: unknown, floatKeys?: Set<string>): string {
  return createHash('sha256').update(canonical(value, floatKeys)).digest('hex');
}

function positionIdentity(state: Record<string, unknown>, config: Record<string, unknown>): string {
  return digest(
    { state, settings: { algorithm: 'alpha_beta', ...config }, protocol: 1 },
    new Set(['time_limit', 'exploration', 'temperature']),
  );
}

function game(): Game {
  return {
    id: 'game-1',
    ruleset: 'brandubh',
    size: 7,
    board: state.board,
    turn: 'attackers',
    ply: 0,
    winner: null,
    reason: null,
    moves: [],
    cursor: 0,
    total_moves: 0,
    legal_moves: [],
    mode: 'human-ai',
    players: {
      attackers: { name: 'Jarl', kind: 'ai', config: {} },
      defenders: { name: 'Human', kind: 'human', config: {} },
    },
    version: 0,
    captured: { attackers: 0, defenders: 0 },
    created_at: '',
    seed: 51,
  };
}

function policy(overrides: Partial<ExecutionPolicy> = {}): ExecutionPolicy {
  return {
    mode: 'browser_preferred',
    browserDelivery: true,
    browserExecution: true,
    serverComputation: true,
    fallback: true,
    webgpu: false,
    offlineAuthorizationSeconds: 900,
    limits: { maxConcurrent: 16, maxPerUser: 2, requestsPerMinute: 30 },
    ...overrides,
  };
}

function settings(config: Record<string, unknown>): AnalyticsSettings {
  return {
    advisor: { name: 'Jarl advisor', profile_id: 'alpha_beta', config: config as any },
    time_limit: 5,
    show_win_chances: true,
    revision: 2,
  };
}

const model = {
  schema: 1,
  publicationId: '0123456789abcdef01234567',
  artifactVersion: 'a'.repeat(64),
  displayName: 'Published Brandubh',
  ruleset: 'brandubh',
  boardSize: 7,
  architecture: { name: 'tafl-policy-value-v1', channels: 32, blocks: 2 },
  encoderVersion: 'six-plane-v1',
  sourceWeightsSha256: 'b'.repeat(64),
  onnxSha256: 'a'.repeat(64),
  artifactBytes: 1024,
  runtime: {
    runtimeManifestSchema: 1,
    ortVersion: '1.22.0',
    minOpset: 13,
    maxOpset: 21,
    encoderVersion: 'six-plane-v1',
    searchProtocol: 1,
    bridgeProtocol: 1,
  },
  io: { input: null, outputs: null },
  status: 'published' as const,
  publishedAt: '',
  registeredModelIds: ['model-registered'],
};

function jarlResult(request?: {
  state: Record<string, unknown>;
  settings: Record<string, unknown>;
}) {
  const settings = request
    ? { algorithm: 'alpha_beta', ...request.settings }
    : {
        algorithm: 'alpha_beta',
        depth: 3,
        time_limit: 1,
        seed: 51,
        heuristic: 'shield_maiden',
      };
  return {
    analysis: {
      value: 0.4,
      source: 'heuristic',
      best_move: { source: 24, target: 17 },
      completed_depth: 3,
    },
    algorithm: 'jarl' as const,
    settings,
    evidence: {
      executionRuntime: 'pyodide' as const,
      requestedProvider: null,
      observedPlacement: 'none' as const,
      gpuExecutionVerified: false,
      inferenceObserved: false,
      inferences: 0,
      completedSimulations: null,
      completionReason: null,
    },
    model: null,
    positionIdentity: request
      ? positionIdentity(request.state, request.settings)
      : positionIdentity(state, settings),
    searchId: null,
    elapsedMs: 10,
    authoritative: false as const,
  };
}

function neuralResult() {
  return {
    analysis: {
      value: -0.2,
      source: 'neural_search',
      best_move: { source: 24, target: 17 },
      simulations: 32,
    },
    algorithm: 'neural_mcts' as const,
    settings: { iterations: 32, time_limit: 1, seed: 51, exploration: 1.4 },
    evidence: {
      executionRuntime: 'pyodide' as const,
      requestedProvider: 'wasm' as const,
      observedPlacement: 'wasm' as const,
      gpuExecutionVerified: false,
      inferenceObserved: true,
      inferences: 40,
      completedSimulations: 32,
      completionReason: 'simulations',
    },
    model: { sha256: model.onnxSha256, ruleset: model.ruleset, size: 7 },
    positionIdentity: 'a'.repeat(64),
    searchId: 'search',
    elapsedMs: 10,
    authoritative: false as const,
  };
}

function api(overrides: Record<string, unknown> = {}) {
  return {
    availability: vi.fn(async () => ({ enabled: true })),
    initialize: vi.fn(async () => ({ state: 'ready' })),
    listModels: vi.fn(async () => [model]),
    downloadModel: vi.fn(async () => ({
      sha256: model.onnxSha256,
      bytes: 1024,
      fromCache: false,
      retained: true,
      persistent: true,
    })),
    runSearch: vi.fn(async (request: any) => jarlResult(request)),
    cancel: vi.fn(async () => undefined),
    invalidateAccountContext: vi.fn(async () => undefined),
    noteOnlineAuthorization: vi.fn(),
    clearOfflineAuthorization: vi.fn(),
    dispose: vi.fn(async () => ({
      drained: true,
      activeTransfers: 0,
      reservedBytes: 0,
      transfers: 0,
      queued: 0,
      cacheVerifications: 0,
    })),
    status: vi.fn(() => ({ state: 'ready' })),
    lastResult: vi.fn(() => null),
    modelReady: vi.fn(() => false),
    storageUsage: vi.fn(async () => ({ persistent: true, bytes: 0 })),
    ...overrides,
  };
}

function contextPayload(
  cursor = 0,
  config: Record<string, unknown> = {},
  execution: Record<string, unknown> = {},
  gameId = 'game-1',
) {
  const next = { ...state, ply: cursor };
  const compact = [state, next].slice(0, cursor + 1).map(({ history: _history, ...rest }) => rest);
  const history = compact.map((item) => `${item.turn}:${(item.board as string[]).join('')}`);
  const full = compact.map((item) => ({
    ...item,
    history: history.slice(0, Math.max(1, Number(item.ply) + 1)),
  }));
  const positionKeys = full.map((item) => digest(item));
  const neural = config.algorithm === 'neural';
  const effective = {
    seed: 51,
    time_limit: 1,
    evaluation: neural ? 'neural' : 'heuristic',
    ...(neural
      ? {
          search_wrapper: 'mcts',
          iterations: 32,
          rollout_depth: 8,
          exploration: 1.4,
          temperature: 0,
        }
      : {}),
    ...config,
  };
  return {
    schema: 1,
    game_id: gameId,
    version: 0,
    ruleset: 'brandubh',
    cursor,
    position_key: positionKeys[cursor],
    position_keys: positionKeys,
    history_keys: history,
    states: compact,
    advisor: {
      effective_config:
        config.algorithm !== undefined
          ? effective
          : {
              algorithm: 'alpha_beta',
              seed: 51,
              time_limit: 1,
              evaluation: 'heuristic',
              heuristic: 'shield_maiden',
              depth: 3,
            },
      requested_config:
        config.algorithm !== undefined
          ? { ...config }
          : { algorithm: 'alpha_beta', depth: 3, time_limit: 1 },
      show_win_chances: true,
      revision: 2,
      seed_with_ply: true,
    },
    execution: {
      mode: 'browser_preferred',
      browser_delivery: true,
      browser_execution: true,
      server_computation: true,
      fallback: true,
      webgpu: false,
      offline_authorization_seconds: 900,
      limits: { max_concurrent: 16, max_per_user: 2, requests_per_minute: 30 },
      ...execution,
    },
  };
}

function contextFetch(
  calls: string[] = [],
  config: Record<string, unknown> = {},
  execution: Record<string, unknown> = {},
  advisor: Record<string, unknown> = {},
) {
  return vi.fn(async (url: string) => {
    calls.push(url);
    const parsed = new URL(url, 'https://example.test');
    const cursor = Number(parsed.searchParams.get('cursor') ?? '0');
    const gameId = parsed.pathname.match(/\/games\/([^/]+)\//)?.[1] ?? 'game-1';
    return {
      ok: true,
      json: async () =>
        ({
          ...contextPayload(cursor, config, execution, gameId),
          advisor: {
            ...contextPayload(cursor, config, execution, gameId).advisor,
            ...advisor,
          },
        }) as unknown,
    } as Response;
  });
}

function input(overrides: Partial<LocalAnalysisInput> = {}): LocalAnalysisInput {
  return {
    accountId: 'account-1',
    game: game(),
    basePath: '/games',
    cursor: 0,
    settings: settings({ algorithm: 'alpha_beta', depth: 3, time_limit: 1 }),
    policy: policy(),
    signal: new AbortController().signal,
    ...overrides,
  };
}

beforeEach(async () => {
  createBrowserAnalysis.mockReset();
  __testing.resetCaches();
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  vi.stubGlobal('fetch', contextFetch());
  await setAnalysisAccount('account-1');
});

afterEach(async () => {
  await disposeLocalAnalysis();
  delete (navigator as { gpu?: unknown }).gpu;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('transparent local analysis integration', () => {
  it('executes Jarl locally and adapts the advisory result without a model', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const outcome = await runLocalAnalysis(input());
    expect(runtime.runSearch).toHaveBeenCalledTimes(1);
    const request = (runtime.runSearch.mock.calls[0] as any[])[0];
    expect(request.algorithm).toBe('jarl');
    expect(request.model).toBeUndefined();
    expect(request.settings).toMatchObject({ depth: 3, time_limit: 1, seed: 51 });
    expect(outcome.report.evaluations[0]).toMatchObject({
      agent_id: 'advisor',
      source: 'heuristic',
      value: 0.4,
      win_probability: 0.3,
      completed_depth: 3,
      config: { algorithm: 'alpha_beta', depth: 3 },
    });
    expect(outcome.report.hint?.move).toEqual({ source: 24, target: 17 });
    expect(outcome.report.status).toBe('complete');
    expect(outcome.report.position_key).toBe(contextPayload(0).position_key);
    expect(outcome.provider).toBe('wasm');
    expect(runtime.downloadModel).not.toHaveBeenCalled();
  });

  it('executes neural MCTS with the published model bound through the registry', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch([], {
        algorithm: 'neural',
        model_id: 'model-registered',
        search_wrapper: 'mcts',
        iterations: 32,
        time_limit: 1,
        temperature: 0,
      }),
    );
    const runtime = api({ runSearch: vi.fn(async () => neuralResult()) });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const outcome = await runLocalAnalysis(
      input({
        settings: settings({
          algorithm: 'neural',
          model_id: 'model-registered',
          iterations: 32,
          time_limit: 1,
        }),
      }),
    );
    expect(runtime.listModels).toHaveBeenCalled();
    expect(runtime.downloadModel).toHaveBeenCalledWith(model, expect.any(Object));
    const request = (runtime.runSearch.mock.calls[0] as any[])[0];
    expect(request.algorithm).toBe('neural_mcts');
    expect(request.model.onnxSha256).toBe(model.onnxSha256);
    expect(request.provider).toBe('wasm');
    expect(outcome.report.evaluations[0]).toMatchObject({
      source: 'neural_search',
      simulations: 32,
      value: -0.2,
      win_probability: 0.6,
    });
    expect(outcome.model?.sha256).toBe(model.onnxSha256);
  });

  it('reuses a healthy neural session without another model transfer', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch([], {
        algorithm: 'neural',
        model_id: 'model-registered',
        search_wrapper: 'mcts',
        iterations: 32,
        time_limit: 1,
        temperature: 0,
      }),
    );
    const runtime = api({
      runSearch: vi.fn(async () => neuralResult()),
      modelReady: vi.fn(() => true),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(
      input({
        settings: settings({ algorithm: 'neural', model_id: 'model-registered', iterations: 32 }),
      }),
    );
    expect(runtime.downloadModel).not.toHaveBeenCalled();
  });

  it('classifies unsupported agents and budgets without substituting a weaker agent', async () => {
    vi.stubGlobal('fetch', contextFetch([], { algorithm: 'baseline' }));
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const baseline = await runLocalAnalysis(
      input({ settings: settings({ algorithm: 'baseline' }) }),
    ).catch((error) => error);
    expect(baseline).toBeInstanceOf(LocalAnalysisError);
    expect(baseline.category).toBe('unsupported-agent');
    expect(baseline.fallbackEligible).toBe(true);
    __testing.resetCaches();
    vi.stubGlobal('fetch', contextFetch([], { algorithm: 'alpha_beta', depth: 6, time_limit: 1 }));
    const budget = await runLocalAnalysis(
      input({ settings: settings({ algorithm: 'alpha_beta', depth: 6, time_limit: 1 }) }),
    ).catch((error) => error);
    expect(budget.category).toBe('unsupported-budget');
    expect(budget.fallbackEligible).toBe(true);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('reports a missing published model without substituting another model', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch([], {
        algorithm: 'neural',
        model_id: 'not-published',
        search_wrapper: 'mcts',
        iterations: 32,
        time_limit: 1,
        temperature: 0,
      }),
    );
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(
      input({
        settings: settings({ algorithm: 'neural', model_id: 'not-published', iterations: 32 }),
      }),
    ).catch((error) => error);
    expect(failure.category).toBe('model-unavailable');
    expect(failure.fallbackEligible).toBe(true);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('recovers a recoverable WebGPU failure with WASM before any fallback', async () => {
    Object.defineProperty(navigator, 'gpu', { configurable: true, value: {} });
    vi.stubGlobal(
      'fetch',
      contextFetch(
        [],
        {
          algorithm: 'neural',
          model_id: 'model-registered',
          search_wrapper: 'mcts',
          iterations: 32,
          time_limit: 1,
          temperature: 0,
        },
        { webgpu: true },
      ),
    );
    const runSearch = vi
      .fn()
      .mockRejectedValueOnce(new BrowserAnalysisError('capability-missing', 'WebGPU failed'))
      .mockResolvedValueOnce({
        ...neuralResult(),
        evidence: {
          ...neuralResult().evidence,
          requestedProvider: 'wasm',
          observedPlacement: 'wasm',
        },
      });
    const runtime = api({ runSearch });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const outcome = await runLocalAnalysis(
      input({
        policy: policy({ webgpu: true }),
        settings: settings({ algorithm: 'neural', model_id: 'model-registered', iterations: 32 }),
      }),
    );
    expect(runSearch).toHaveBeenCalledTimes(2);
    expect((runSearch.mock.calls[0] as any[])[0].provider).toBe('webgpu');
    expect((runSearch.mock.calls[1] as any[])[0].provider).toBe('wasm');
    expect(outcome.provider).toBe('wasm');
    Object.defineProperty(navigator, 'gpu', { configurable: true, value: undefined });
  });

  it('fails closed on integrity errors and cancellation without fallback', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch([], {
        algorithm: 'neural',
        model_id: 'model-registered',
        search_wrapper: 'mcts',
        iterations: 32,
        time_limit: 1,
        temperature: 0,
      }),
    );
    const integrity = api({
      runSearch: vi.fn(async () => {
        throw new BrowserAnalysisError('model-integrity', 'digest mismatch');
      }),
    });
    createBrowserAnalysis.mockResolvedValue(integrity);
    const failed = await runLocalAnalysis(
      input({
        settings: settings({ algorithm: 'neural', model_id: 'model-registered', iterations: 32 }),
      }),
    ).catch((error) => error);
    expect(failed.category).toBe('integrity');
    expect(failed.fallbackEligible).toBe(false);

    await disposeLocalAnalysis();
    const abort = new AbortController();
    const cancelled = api({
      runSearch: vi.fn(async () => {
        abort.abort();
        throw new DOMException('cancelled', 'AbortError');
      }),
    });
    createBrowserAnalysis.mockResolvedValue(cancelled);
    const stopped = await runLocalAnalysis(input({ signal: abort.signal })).catch((error) => error);
    expect(stopped.category).toBe('cancelled');
    expect(stopped.fallbackEligible).toBe(false);
  });

  it('runs warm offline Jarl and neural searches from prepared contexts', async () => {
    const runtime = api({ runSearch: vi.fn(async (request: any) => jarlResult(request)) });
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    const calls = (globalThis.fetch as any).mock.calls.length;
    const offline = await runLocalAnalysis(input());
    expect(offline.offline).toBe(true);
    expect((globalThis.fetch as any).mock.calls.length).toBe(calls);
    const options = (runtime.runSearch.mock.calls[1] as any[])[1];
    expect(options.offline).toEqual({ accountId: 'account-1' });
    expect(runtime.initialize).toHaveBeenCalledTimes(2);
  });

  it('requires origin revalidation after reconnection before further local work', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    const validationCalls = runtime.availability.mock.calls.length;
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    await runLocalAnalysis(input());
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
    window.dispatchEvent(new Event('online'));
    await runLocalAnalysis(input());
    expect(runtime.availability.mock.calls.length).toBeGreaterThan(validationCalls);
    expect(runtime.runSearch).toHaveBeenCalledTimes(3);
  });

  it('does not fall back when an offline position was never prepared', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('offline');
    expect(failure.fallbackEligible).toBe(false);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('invalidates the runtime on account change and disposes on sign-out', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    await setAnalysisAccount('account-2');
    expect(runtime.invalidateAccountContext).toHaveBeenCalled();
    const switched = await runLocalAnalysis(input({ accountId: 'account-2' }));
    expect(switched.report.status).toBe('complete');
    await setAnalysisAccount(null);
    expect(runtime.dispose).toHaveBeenCalled();
    expect(runtime.clearOfflineAuthorization).toHaveBeenCalled();
  });

  it('rejects a changed server policy mid-context rather than using a stale policy', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          ...contextPayload(),
          execution: {
            mode: 'server_only',
            browser_delivery: false,
            browser_execution: false,
            server_computation: true,
            fallback: false,
            webgpu: false,
            offline_authorization_seconds: 900,
            limits: { max_concurrent: 16, max_per_user: 2, requests_per_minute: 30 },
          },
        }),
      })),
    );
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('disabled');
    expect(failure.fallbackEligible).toBe(false);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('uses the server-resolved analytics time limit over the advisor config default', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch([], { algorithm: 'alpha_beta', depth: 3, time_limit: 5, seed: 51 }),
    );
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(
      input({ settings: settings({ algorithm: 'alpha_beta', depth: 3, time_limit: 0.1 }) }),
    );
    const request = (runtime.runSearch.mock.calls[0] as any[])[0];
    expect(request.settings.time_limit).toBe(5);
    expect(runtime.runSearch).toHaveBeenCalledTimes(1);
  });

  it('derives the search seed from the analyzed position ply, not the current game ply', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch([], { algorithm: 'alpha_beta', depth: 3, time_limit: 1, seed: 51 }),
    );
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const base = game();
    const outcome = await runLocalAnalysis(
      input({ game: { ...base, cursor: 1, ply: 0 }, cursor: 1 }),
    );
    const request = (runtime.runSearch.mock.calls[0] as any[])[0];
    expect(request.settings.seed).toBe(52);
    expect((request.state as { history: string[] }).history).toHaveLength(2);
    expect(outcome.report.position_key).toBe(contextPayload(1).position_key);
  });

  it('honours the server show_win_chances setting while retaining advice', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch(
        [],
        { algorithm: 'alpha_beta', depth: 3, time_limit: 1, seed: 51 },
        {},
        { show_win_chances: false },
      ),
    );
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const outcome = await runLocalAnalysis(input());
    expect(outcome.report.evaluations[0].win_probability).toBeNull();
    expect(outcome.report.evaluations[0].value).toBeUndefined();
    expect(outcome.report.hint).not.toBeNull();
  });

  it('does not renew offline authorization after successful offline searches', async () => {
    const clock = { value: 1_000_000 };
    vi.spyOn(Date, 'now').mockImplementation(() => clock.value);
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    clock.value += 800_000;
    await runLocalAnalysis(input());
    await runLocalAnalysis(input());
    clock.value += 101_000;
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('unauthorized');
    expect(failure.fallbackEligible).toBe(false);
    expect(runtime.runSearch).toHaveBeenCalledTimes(3);
  });

  it('does not renew a lease when revalidation is unreachable but can reuse a valid warm lease', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('origin unreachable');
      }),
    );
    window.dispatchEvent(new Event('online'));
    const outcome = await runLocalAnalysis(input());
    expect(outcome.offline).toBe(true);
  });

  it('fails closed when revalidation is explicitly denied', async () => {
    const runtime = api({
      availability: vi.fn(async () => {
        throw new BrowserAnalysisError('unauthorized', 'denied');
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    window.dispatchEvent(new Event('online'));
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('unauthorized');
    expect(failure.fallbackEligible).toBe(false);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('does not cache a delayed context response after an account switch', async () => {
    let rejectFetch!: (error: unknown) => void;
    const fetchMock = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          rejectFetch = reject;
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const pending = runLocalAnalysis(input());
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await setAnalysisAccount('account-2');
    rejectFetch(new Error('late response'));
    const failure = await pending.catch((error) => error);
    expect(failure.category).toBe('cancelled');
  });

  it('keeps concurrent context consumers independent when one caller cancels', async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const firstController = new AbortController();
    const secondController = new AbortController();
    const first = runLocalAnalysis(input({ signal: firstController.signal }));
    const second = runLocalAnalysis(input({ signal: secondController.signal }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    firstController.abort();
    await expect(first).rejects.toMatchObject({ category: 'cancelled' });
    resolveFetch({
      ok: true,
      json: async () => contextPayload(0, { algorithm: 'alpha_beta', depth: 3, time_limit: 1 }),
    } as Response);
    await expect(second).resolves.toMatchObject({ report: { status: 'complete' } });
  });

  it('cancels a neural catalogue lookup without installing an obsolete runtime', async () => {
    vi.stubGlobal(
      'fetch',
      contextFetch([], {
        algorithm: 'neural',
        model_id: 'model-registered',
        search_wrapper: 'mcts',
        iterations: 32,
        time_limit: 1,
        temperature: 0,
      }),
    );
    let resolveModels!: (value: unknown[]) => void;
    const runtime = api({
      listModels: vi.fn(
        () =>
          new Promise<unknown[]>((resolve) => {
            resolveModels = resolve;
          }),
      ),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const pending = runLocalAnalysis(
      input({
        settings: settings({ algorithm: 'neural', model_id: 'model-registered', iterations: 32 }),
      }),
    );
    await vi.waitFor(() => expect(runtime.listModels).toHaveBeenCalledTimes(1));
    await setAnalysisAccount(null);
    resolveModels([model]);
    const failure = await pending.catch((error) => error);
    expect(failure.category).toBe('cancelled');
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('treats an unexpected TypeError as an unknown engine fault, not a network outage', async () => {
    const runtime = api({
      runSearch: vi.fn(async () => {
        throw new TypeError('unexpected protocol shape');
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('unknown');
    expect(failure.fallbackEligible).toBe(false);
  });

  it('rejects mismatched context identity and missing canonical position metadata', async () => {
    const valid = contextPayload(0, {
      algorithm: 'alpha_beta',
      depth: 3,
      time_limit: 1,
      seed: 51,
    });
    const variants = [
      { ...valid, version: 1 },
      { ...valid, cursor: 1 },
      { ...valid, history_keys: [] },
      {
        ...valid,
        history_keys: valid.history_keys.map((key, index) =>
          index === 0 ? 'attackers:wrong' : key,
        ),
      },
      { ...valid, position_keys: undefined },
      { ...valid, states: [{ ...valid.states[0], ply: 2 }] },
      {
        ...valid,
        position_key: 'a'.repeat(64),
        position_keys: ['a'.repeat(64)],
      },
    ];
    for (const variant of variants) {
      __testing.resetCaches();
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => ({ ok: true, json: async () => variant })) as unknown as typeof fetch,
      );
      const runtime = api();
      createBrowserAnalysis.mockResolvedValue(runtime);
      const failure = await runLocalAnalysis(input()).catch((error) => error);
      expect(failure.category).toBe('integrity');
      expect(failure.fallbackEligible).toBe(false);
      expect(runtime.runSearch).not.toHaveBeenCalled();
    }
  });

  it('bounds the client context cache by entry count across game versions', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const parsed = new URL(url, 'https://example.test');
        const version = Number(parsed.searchParams.get('version') ?? '0');
        return {
          ok: true,
          json: async () => ({
            ...contextPayload(0, { algorithm: 'alpha_beta', depth: 3, time_limit: 1 }),
            version,
          }),
        } as Response;
      }) as unknown as typeof fetch,
    );
    for (let version = 1; version <= 70; version += 1) {
      await runLocalAnalysis(input({ game: { ...game(), version } }));
    }
    const stats = __testing.contextStats();
    expect(stats.entries).toBeLessThanOrEqual(64);
    expect(stats.bytes).toBeLessThanOrEqual(8 * 1024 * 1024);
  });

  it('propagates cancellation into runtime initialization without later stages', async () => {
    const controller = new AbortController();
    const runtime = api({
      initialize: vi.fn(
        ({ signal }: { signal?: AbortSignal }) =>
          new Promise((_resolve, reject) => {
            signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            );
          }),
      ),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const pending = runLocalAnalysis(input({ signal: controller.signal }));
    await vi.waitFor(() => expect(runtime.initialize).toHaveBeenCalledTimes(1));
    controller.abort();
    const failure = await pending.catch((error) => error);
    expect(failure.category).toBe('cancelled');
    expect(runtime.runSearch).not.toHaveBeenCalled();
    expect(runtime.downloadModel).not.toHaveBeenCalled();
  });

  it('rejects stale or conflicting canonical advisor identity', async () => {
    const base = contextPayload(0, {
      algorithm: 'alpha_beta',
      depth: 3,
      time_limit: 1,
      seed: 51,
    });
    const variants = [
      { ...base, advisor: { ...base.advisor, revision: input().settings.revision + 1 } },
      { ...base, advisor: null },
      { ...base, advisor: { ...base.advisor, effective_config: undefined } },
      { ...base, advisor: { ...base.advisor, profile_id: 'other-profile' } },
      {
        ...base,
        advisor: {
          ...base.advisor,
          effective_config: { ...base.advisor.effective_config, algorithm: 'mcts' },
        },
      },
    ];
    for (const variant of variants) {
      __testing.resetCaches();
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => ({ ok: true, json: async () => variant })) as unknown as typeof fetch,
      );
      const runtime = api();
      createBrowserAnalysis.mockResolvedValue(runtime);
      const failure = await runLocalAnalysis(input()).catch((error) => error);
      expect(failure.category).toBe('integrity');
      expect(failure.fallbackEligible).toBe(false);
      expect(runtime.runSearch).not.toHaveBeenCalled();
    }
  });

  it('rejects missing or malformed canonical configuration instead of using defaults', async () => {
    const variants: Array<[Record<string, unknown>, string]> = [
      [{ algorithm: 'alpha_beta', depth: undefined, time_limit: 1 }, 'depth'],
      [{ algorithm: 'alpha_beta', depth: 3, time_limit: undefined }, 'time limit'],
      [{ algorithm: 'alpha_beta', depth: '3', time_limit: 1 }, 'numeric depth'],
      [{ algorithm: 'alpha_beta', depth: 3, time_limit: 1, seed: -1 }, 'seed'],
      [
        {
          algorithm: 'neural',
          model_id: 'model-registered',
          search_wrapper: 'mcts',
          iterations: undefined,
          time_limit: 1,
        },
        'iterations',
      ],
      [
        {
          algorithm: 'neural',
          model_id: 'model-registered',
          search_wrapper: 'mcts',
          iterations: 32,
          time_limit: 1,
          exploration: 11,
        },
        'exploration',
      ],
    ];
    for (const [config, label] of variants) {
      __testing.resetCaches();
      vi.stubGlobal('fetch', contextFetch([], config));
      const runtime = api();
      createBrowserAnalysis.mockResolvedValue(runtime);
      const failure = await runLocalAnalysis(input({ settings: settings(config) })).catch(
        (error) => error,
      );
      expect(failure.category, label).toBe('integrity');
      expect(failure.fallbackEligible, label).toBe(false);
      expect(runtime.runSearch, label).not.toHaveBeenCalled();
    }
  });

  it('requires the modern context execution-policy projection', async () => {
    const payload = contextPayload(0, { algorithm: 'alpha_beta', depth: 3, time_limit: 1 });
    delete (payload as { execution?: unknown }).execution;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => payload }) as Response),
    );
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('integrity');
    expect(failure.fallbackEligible).toBe(false);
  });

  it('rejects a runtime result whose canonical identity belongs to another position', async () => {
    const runtime = api({
      runSearch: vi.fn(async (request: any) => {
        const other = { ...request.state, board: [...request.state.board] };
        other.board[0] = 'K';
        return {
          ...jarlResult(request),
          positionIdentity: positionIdentity(other, request.settings),
        };
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('integrity');
    expect(failure.fallbackEligible).toBe(false);
  });

  it('rejects a canonical model identity that differs from the requested model', async () => {
    __testing.resetCaches();
    vi.stubGlobal(
      'fetch',
      contextFetch([], {
        algorithm: 'neural',
        model_id: 'different-model',
        search_wrapper: 'mcts',
        iterations: 32,
        time_limit: 1,
        temperature: 0,
      }),
    );
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(
      input({
        settings: settings({ algorithm: 'neural', model_id: 'model-registered', iterations: 32 }),
      }),
    ).catch((error) => error);
    expect(failure.category).toBe('integrity');
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('does not install a runtime created for an obsolete account generation', async () => {
    let resolveCreation!: (value: ReturnType<typeof api>) => void;
    createBrowserAnalysis.mockReturnValue(
      new Promise((resolve) => {
        resolveCreation = resolve;
      }),
    );
    const pending = runLocalAnalysis(input());
    await vi.waitFor(() => expect(createBrowserAnalysis).toHaveBeenCalledTimes(1));
    const switching = setAnalysisAccount('account-2');
    const obsolete = api();
    resolveCreation(obsolete);
    await switching;
    const failure = await pending.catch((error) => error);
    expect(failure.category).toBe('cancelled');
    await vi.waitFor(() => expect(obsolete.dispose).toHaveBeenCalled());
  });

  it('does not extend an existing context lease when a later policy duration increases', async () => {
    const clock = { value: 2_000_000 };
    vi.spyOn(Date, 'now').mockImplementation(() => clock.value);
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input({ policy: policy({ offlineAuthorizationSeconds: 900 }) }));
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    clock.value += 800_000;
    const failure = await runLocalAnalysis(
      input({ offline: true, policy: policy({ offlineAuthorizationSeconds: 3600 }) }),
    ).catch((error) => error);
    expect(failure.category).toBe('unauthorized');
    expect(runtime.runSearch).toHaveBeenCalledTimes(1);
  });

  it('invalidates a context lease when the runtime identity changes', async () => {
    let runtimeId = 'runtime-a';
    const runtime = api({
      status: vi.fn(() => ({ state: 'ready', runtimeId })),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    runtimeId = 'runtime-b';
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    const failure = await runLocalAnalysis(input({ offline: true })).catch((error) => error);
    expect(failure.category).toBe('unauthorized');
  });

  it('fails explicitly when runtime initialization returns unavailable', async () => {
    const runtime = api({
      initialize: vi.fn(async () => ({
        state: 'unavailable',
        error: { code: 'runtime-missing', message: 'runtime missing', retryable: true },
      })),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('browser-unsupported');
    expect(failure.fallbackEligible).toBe(true);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('classifies runtime availability transport failure as offline, not an engine fault', async () => {
    const runtime = api({
      availability: vi.fn(async () => {
        throw new BrowserAnalysisError('network-unavailable', 'origin unreachable', true);
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('offline');
    expect(failure.fallbackEligible).toBe(false);
  });

  it('does not authorize fallback for runtime-internal unsupported states', async () => {
    const runtime = api({
      runSearch: vi.fn(async () => {
        throw new BrowserAnalysisError('unsupported', 'runtime disposed');
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('initialization');
    expect(failure.fallbackEligible).toBe(false);
  });

  it('awaits genuine logout retirement before creating a replacement runtime', async () => {
    let resolveDispose!: (value: unknown) => void;
    let disposed = false;
    const outcome = {
      drained: true,
      activeTransfers: 0,
      reservedBytes: 0,
      transfers: 0,
      queued: 0,
      cacheVerifications: 0,
    };
    const runtime = api({
      dispose: vi.fn(() => {
        if (disposed) return Promise.resolve(outcome);
        return new Promise((resolve) => {
          resolveDispose = (value) => {
            disposed = true;
            resolve(value);
          };
        });
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    void setAnalysisAccount(null);
    const login = setAnalysisAccount('account-2');
    const pending = runLocalAnalysis(input({ accountId: 'account-2' }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(createBrowserAnalysis).toHaveBeenCalledTimes(1);
    resolveDispose(outcome);
    await login;
    await pending;
    expect(createBrowserAnalysis).toHaveBeenCalledTimes(2);
  });

  it('does not install a runtime created during A → B → A navigation', async () => {
    let resolveFirst!: (value: ReturnType<typeof api>) => void;
    createBrowserAnalysis.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirst = resolve;
      }),
    );
    const first = runLocalAnalysis(input());
    await vi.waitFor(() => expect(createBrowserAnalysis).toHaveBeenCalledTimes(1));
    const second = setAnalysisAccount('account-2');
    const back = setAnalysisAccount('account-1');
    const obsolete = api();
    const replacement = api();
    createBrowserAnalysis.mockResolvedValue(replacement);
    resolveFirst(obsolete);
    await second;
    await back;
    await expect(first).rejects.toMatchObject({ category: 'cancelled' });
    await runLocalAnalysis(input());
    expect(obsolete.dispose).toHaveBeenCalled();
    expect(createBrowserAnalysis).toHaveBeenCalledTimes(2);
  });

  it('treats a healthy searching runtime as busy instead of a browser failure', async () => {
    const runtime = api({
      initialize: vi.fn(async () => ({ state: 'searching' })),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('busy');
    expect(failure.fallbackEligible).toBe(false);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('reinitializes a runtime whose identity changes during availability revalidation', async () => {
    let runtimeId = 'runtime-a';
    let state = 'ready';
    const runtime = api({
      initialize: vi.fn(async () => {
        state = 'ready';
        return { state, runtimeId };
      }),
      status: vi.fn(() => ({ state, runtimeId })),
      availability: vi.fn(async () => {
        runtimeId = 'runtime-b';
        state = 'idle';
        return { enabled: true };
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    const outcome = await runLocalAnalysis(input());
    expect(outcome.report.status).toBe('complete');
    expect(runtime.initialize).toHaveBeenCalledTimes(2);
    expect(runtime.availability).toHaveBeenCalledTimes(1);
    expect(runtime.runSearch).toHaveBeenCalledTimes(1);
  });

  it('invalidates the loaded runtime when a known policy change removes browser execution', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    await setAnalysisPolicy(
      policy({
        mode: 'server_only',
        browserDelivery: false,
        browserExecution: false,
        serverComputation: true,
        fallback: false,
      }),
      'account-1',
    );
    expect(runtime.dispose).toHaveBeenCalled();
    const failure = await runLocalAnalysis(input()).catch((error) => error);
    expect(failure.category).toBe('disabled');
  });

  it('ignores an obsolete account policy change instead of mutating newer state', async () => {
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    await runLocalAnalysis(input());
    await setAnalysisAccount('account-2');
    await runLocalAnalysis(input({ accountId: 'account-2' }));
    const searches = runtime.runSearch.mock.calls.length;
    await setAnalysisPolicy(
      policy({
        mode: 'server_only',
        browserDelivery: false,
        browserExecution: false,
        serverComputation: true,
        fallback: false,
      }),
      'account-1',
    );
    expect(runtime.dispose).not.toHaveBeenCalled();
    await runLocalAnalysis(input({ accountId: 'account-2' }));
    expect(runtime.runSearch.mock.calls.length).toBe(searches + 1);
    expect(createBrowserAnalysis).toHaveBeenCalledTimes(1);
  });

  it('reuses a successful WASM recovery instead of retrying WebGPU', async () => {
    Object.defineProperty(navigator, 'gpu', { configurable: true, value: {} });
    let modelReady = false;
    const runtime = api({
      modelReady: vi.fn(() => modelReady),
      downloadModel: vi.fn(async () => {
        modelReady = true;
        return {
          sha256: model.onnxSha256,
          bytes: 1024,
          fromCache: false,
          retained: true,
          persistent: true,
        };
      }),
      runSearch: vi.fn(async (request: any) => {
        if (request.provider === 'webgpu') {
          throw new BrowserAnalysisError('capability-missing', 'no adapter');
        }
        return neuralResult();
      }),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    vi.stubGlobal(
      'fetch',
      contextFetch(
        [],
        {
          algorithm: 'neural',
          model_id: 'model-registered',
          search_wrapper: 'mcts',
          iterations: 32,
          time_limit: 1,
          temperature: 0,
        },
        { webgpu: true },
      ),
    );
    const neural = input({
      settings: settings({ algorithm: 'neural', model_id: 'model-registered', iterations: 32 }),
      policy: policy({ webgpu: true }),
    });
    await runLocalAnalysis(neural);
    await runLocalAnalysis(neural);
    const providers = (runtime.runSearch.mock.calls as any[]).map(([request]) => request.provider);
    expect(providers).toEqual(['webgpu', 'wasm', 'wasm']);
  });

  it('does not reuse a cached WebGPU decision after the policy disables it', async () => {
    Object.defineProperty(navigator, 'gpu', { configurable: true, value: {} });
    const runtime = api({
      runSearch: vi.fn(async (request: any) =>
        request.provider === 'webgpu' ? neuralResult() : { ...neuralResult(), provider: 'wasm' },
      ),
      modelReady: vi.fn(() => true),
    });
    createBrowserAnalysis.mockResolvedValue(runtime);
    vi.stubGlobal(
      'fetch',
      contextFetch(
        [],
        {
          algorithm: 'neural',
          model_id: 'model-registered',
          search_wrapper: 'mcts',
          iterations: 32,
          time_limit: 1,
          temperature: 0,
        },
        { webgpu: true },
      ),
    );
    const neural = input({
      settings: settings({ algorithm: 'neural', model_id: 'model-registered', iterations: 32 }),
      policy: policy({ webgpu: true }),
    });
    await runLocalAnalysis(neural);
    await setAnalysisPolicy(policy({ webgpu: false }), 'account-1');
    vi.stubGlobal(
      'fetch',
      contextFetch(
        [],
        {
          algorithm: 'neural',
          model_id: 'model-registered',
          search_wrapper: 'mcts',
          iterations: 32,
          time_limit: 1,
          temperature: 0,
        },
        { webgpu: false },
      ),
    );
    await runLocalAnalysis({ ...neural, policy: policy({ webgpu: false }) });
    const providers = (runtime.runSearch.mock.calls as any[]).map(([request]) => request.provider);
    expect(providers).toEqual(['webgpu', 'wasm']);
  });
});
