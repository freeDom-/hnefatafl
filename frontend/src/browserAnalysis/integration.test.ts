import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

function jarlResult() {
  return {
    analysis: {
      value: 0.4,
      source: 'heuristic',
      best_move: { source: 24, target: 17 },
      completed_depth: 3,
    },
    algorithm: 'jarl' as const,
    settings: {
      algorithm: 'alpha_beta',
      depth: 3,
      time_limit: 1,
      seed: 51,
      heuristic: 'shield_maiden',
    },
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
    positionIdentity: 'position',
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
    positionIdentity: 'position',
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
    runSearch: vi.fn(async () => jarlResult()),
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

function contextPayload(cursor = 0) {
  const next = { ...state, ply: cursor };
  return {
    schema: 1,
    game_id: 'game-1',
    version: 0,
    ruleset: 'brandubh',
    cursor,
    position_key: 'server-position-key',
    states: [state, next].slice(0, cursor + 1),
    execution: {
      mode: 'browser_preferred',
      browser_delivery: true,
      browser_execution: true,
      server_computation: true,
      fallback: true,
      webgpu: false,
      offline_authorization_seconds: 900,
      limits: { max_concurrent: 16, max_per_user: 2, requests_per_minute: 30 },
    },
  };
}

function contextFetch(calls: string[] = []) {
  return vi.fn(async (url: string) => {
    calls.push(url);
    const parsed = new URL(url, 'https://example.test');
    const cursor = Number(parsed.searchParams.get('cursor') ?? '0');
    return {
      ok: true,
      json: async () => contextPayload(cursor),
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

beforeEach(() => {
  createBrowserAnalysis.mockReset();
  __testing.resetCaches();
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  vi.stubGlobal('fetch', contextFetch());
});

afterEach(async () => {
  await disposeLocalAnalysis();
  vi.unstubAllGlobals();
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
    expect(outcome.report.position_key).toBe('server-position-key');
    expect(outcome.provider).toBe('wasm');
    expect(runtime.downloadModel).not.toHaveBeenCalled();
  });

  it('executes neural MCTS with the published model bound through the registry', async () => {
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
    const runtime = api();
    createBrowserAnalysis.mockResolvedValue(runtime);
    const baseline = await runLocalAnalysis(
      input({ settings: settings({ algorithm: 'baseline' }) }),
    ).catch((error) => error);
    expect(baseline).toBeInstanceOf(LocalAnalysisError);
    expect(baseline.category).toBe('unsupported-agent');
    expect(baseline.fallbackEligible).toBe(true);
    const budget = await runLocalAnalysis(
      input({ settings: settings({ algorithm: 'alpha_beta', depth: 6, time_limit: 1 }) }),
    ).catch((error) => error);
    expect(budget.category).toBe('unsupported-budget');
    expect(budget.fallbackEligible).toBe(true);
    expect(runtime.runSearch).not.toHaveBeenCalled();
  });

  it('reports a missing published model without substituting another model', async () => {
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
    const runtime = api({ runSearch: vi.fn(async () => jarlResult()) });
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
});
