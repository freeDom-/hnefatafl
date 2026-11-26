import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AnalyticsSettings, PositionAnalysis } from './analysisTypes';
import type { Game } from './types';

const apiMock = vi.fn();
vi.mock('./api', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  },
}));

const local = vi.hoisted(() => ({
  runLocalAnalysis: vi.fn(),
  setAnalysisAccount: vi.fn(async () => undefined),
}));
vi.mock('./browserAnalysis/loader', () => ({
  loadAnalysisIntegration: async () => ({
    runLocalAnalysis: (...args: unknown[]) =>
      (local.runLocalAnalysis as (...values: unknown[]) => unknown)(...args),
    setAnalysisAccount: (...args: unknown[]) =>
      (local.setAnalysisAccount as (...values: unknown[]) => unknown)(...args),
  }),
}));

import { useGameAnalysis } from './useGameAnalysis';

function game(): Game {
  return {
    id: 'g1',
    ruleset: 'brandubh',
    size: 7,
    board: Array.from({ length: 49 }, () => '.'),
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
  };
}

function execution(mode: string, fallback = false) {
  const browser = mode === 'browser_only' || mode === 'browser_preferred';
  return {
    mode,
    browser_delivery: browser,
    browser_execution: browser,
    server_computation: mode === 'server_only' || mode === 'browser_preferred',
    fallback,
    webgpu: false,
    offline_authorization_seconds: 900,
    limits: { max_concurrent: 16, max_per_user: 2, requests_per_minute: 30 },
  };
}

function settings(mode: string): AnalyticsSettings {
  return {
    advisor: {
      name: 'Jarl advisor',
      profile_id: 'alpha_beta',
      config: { algorithm: 'alpha_beta', depth: 3, time_limit: 1 },
    },
    time_limit: 5,
    show_win_chances: true,
    revision: 1,
    execution: execution(mode, mode === 'browser_preferred'),
  };
}

function localReport(): PositionAnalysis {
  return {
    cursor: 0,
    ply: 0,
    side_to_move: 'attackers',
    position_key: 'key',
    created_at: new Date().toISOString(),
    evaluations: [
      {
        agent_id: 'advisor',
        role: 'advisor',
        name: 'Jarl advisor',
        config: { algorithm: 'alpha_beta', depth: 3 },
        source: 'heuristic',
        win_probability: 0.6,
        estimated: true,
        calibrated: false,
      },
    ],
    hint: null,
    status: 'complete',
    advisor_revision: 1,
  };
}

beforeEach(() => {
  apiMock.mockReset();
  local.runLocalAnalysis.mockReset();
  local.setAnalysisAccount.mockClear();
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  apiMock.mockImplementation(async (path: string) => {
    if (path.endsWith('/analytics')) return settings('server_only');
    if (path.endsWith('/analysis')) {
      return { ...localReport(), evaluations: [] };
    }
    throw new Error(`unexpected ${path}`);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function render(mode: string, visible = true) {
  apiMock.mockImplementation(async (path: string) => {
    if (path.endsWith('/analytics')) return settings(mode);
    if (path.endsWith('/analysis')) return { ...localReport(), evaluations: [] };
    throw new Error(`unexpected ${path}`);
  });
  return renderHook(() => useGameAnalysis(game(), visible, false, '/games', false, 'account-1'));
}

describe('useGameAnalysis execution-policy integration', () => {
  it('keeps the legacy server path and never imports local execution in server_only', async () => {
    const hook = render('server_only');
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(true),
    );
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
    expect(local.runLocalAnalysis).not.toHaveBeenCalled();
    expect(local.setAnalysisAccount).not.toHaveBeenCalled();
    expect(hook.result.current.local).toBe(false);
  });

  it('executes locally in browser_only without calling the server analysis endpoint', async () => {
    local.runLocalAnalysis.mockImplementation(async ({ onStage }: any) => {
      onStage('searching');
      return {
        report: localReport(),
        evidence: {},
        provider: 'wasm',
        offline: false,
        model: null,
        fromCache: true,
      };
    });
    const hook = render('browser_only');
    await waitFor(() => expect(local.runLocalAnalysis).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(hook.result.current.report?.position_key).toBe('key'));
    expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(false);
    expect(hook.result.current).toMatchObject({
      local: true,
      pending: false,
      failed: false,
      executionMode: 'browser_only',
    });
  });

  it('falls back to the server only for an eligible failure in browser_preferred', async () => {
    local.runLocalAnalysis.mockRejectedValue(
      Object.assign(new Error('unsupported browser'), {
        category: 'browser-unsupported',
        fallbackEligible: true,
      }),
    );
    const hook = render('browser_preferred');
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(true),
    );
    const call = apiMock.mock.calls.find(([path]) => String(path).endsWith('/analysis'))!;
    expect(call[4]).toEqual({ 'X-Analysis-Fallback': '1' });
    await waitFor(() => expect(hook.result.current.fallback).toBe(true));
  });

  it('does not fall back for a prohibited local failure or in browser_only', async () => {
    local.runLocalAnalysis.mockRejectedValue(
      Object.assign(new Error('integrity'), {
        category: 'integrity',
        fallbackEligible: false,
      }),
    );
    const hook = render('browser_only');
    await waitFor(() => expect(hook.result.current.failed).toBe(true));
    expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(false);
  });

  it('suppresses analysis computation in disabled mode while keeping stored results', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings('disabled');
      throw new Error('analysis endpoint must not be called');
    });
    const hook = renderHook(() =>
      useGameAnalysis(game(), true, false, '/games', false, 'account-1'),
    );
    await waitFor(() => expect(hook.result.current.settings.execution).toBeDefined());
    expect(hook.result.current.pending).toBe(false);
    expect(local.runLocalAnalysis).not.toHaveBeenCalled();
    expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(false);
  });

  it('rejects a stale local result after rapid navigation', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings('browser_only');
      throw new Error('analysis endpoint must not be called');
    });
    const first = new Promise<never>(() => undefined);
    local.runLocalAnalysis.mockImplementationOnce(() => first);
    local.runLocalAnalysis.mockImplementationOnce(async () => ({
      report: { ...localReport(), position_key: 'newer' },
      evidence: {},
      provider: 'wasm',
      offline: false,
      model: null,
      fromCache: true,
    }));
    let cursor = 0;
    const base = game();
    const hook = renderHook(() =>
      useGameAnalysis({ ...base, cursor }, true, false, '/games', false, 'account-1'),
    );
    await waitFor(() => expect(local.runLocalAnalysis).toHaveBeenCalledTimes(1));
    cursor = 1;
    hook.rerender();
    await waitFor(() => expect(local.runLocalAnalysis).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(hook.result.current.report?.position_key).toBe('newer'));
    expect(hook.result.current.report?.position_key).not.toBe('key');
  });

  it('retries local execution without retaining a stale fallback decision', async () => {
    local.runLocalAnalysis
      .mockRejectedValueOnce(
        Object.assign(new Error('unsupported browser'), {
          category: 'browser-unsupported',
          fallbackEligible: true,
        }),
      )
      .mockResolvedValueOnce({
        report: localReport(),
        evidence: {},
        provider: 'wasm',
        offline: false,
        model: null,
        fromCache: true,
      });
    const hook = render('browser_preferred');
    await waitFor(() => expect(hook.result.current.fallback).toBe(true));
    await act(async () => {
      hook.result.current.retry();
    });
    await waitFor(() => expect(local.runLocalAnalysis).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(hook.result.current.report?.position_key).toBe('key'));
    expect(hook.result.current.failed).toBe(false);
  });

  it('does not resurrect an older fallback when navigating A → B → A', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings('browser_preferred');
      if (path.endsWith('/analysis')) return { ...localReport(), status: 'complete' };
      throw new Error(`unexpected ${path}`);
    });
    let localCalls = 0;
    local.runLocalAnalysis.mockImplementation(async ({ game }: any) => {
      localCalls += 1;
      if (game.id === 'g1' && localCalls === 1) {
        throw Object.assign(new Error('unsupported browser'), {
          category: 'browser-unsupported',
          fallbackEligible: true,
        });
      }
      return {
        report: { ...localReport(), position_key: `${game.id}-${localCalls}` },
        evidence: {},
        provider: 'wasm',
        offline: false,
        model: null,
        fromCache: true,
      };
    });
    const base = game();
    const second = { ...base, id: 'g2' };
    const hook = renderHook(
      ({ current }) => useGameAnalysis(current, true, false, '/games', false, 'account-1'),
      { initialProps: { current: base } },
    );
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(true),
    );
    await waitFor(() => expect(hook.result.current.fallback).toBe(true));
    hook.rerender({ current: second });
    await waitFor(() => expect(hook.result.current.report?.position_key).toBe('g2-2'));
    hook.rerender({ current: base });
    await waitFor(() => expect(hook.result.current.report?.position_key).toBe('g1-3'));
    expect(apiMock.mock.calls.filter(([path]) => String(path).endsWith('/analysis'))).toHaveLength(
      1,
    );
    expect(localCalls).toBe(3);
  });

  it('merges local advice with immutable stored playing-agent evaluations', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings('browser_only');
      throw new Error(`unexpected ${path}`);
    });
    const stored: PositionAnalysis = {
      cursor: 0,
      ply: 0,
      side_to_move: 'attackers',
      position_key: 'key',
      created_at: 'now',
      status: 'complete',
      advisor_revision: 0,
      evaluations: [
        {
          agent_id: 'attackers',
          role: 'attackers',
          name: 'Hugin',
          config: {},
          source: 'heuristic',
          win_probability: 0.7,
          estimated: true,
          calibrated: false,
        },
        {
          agent_id: 'defenders',
          role: 'defenders',
          name: 'Munin',
          config: {},
          source: 'heuristic',
          win_probability: 0.3,
          estimated: true,
          calibrated: false,
        },
      ],
      hint: null,
    };
    local.runLocalAnalysis.mockResolvedValue({
      report: localReport(),
      evidence: {},
      provider: 'wasm',
      offline: false,
      model: null,
      fromCache: true,
    });
    const hook = renderHook(() =>
      useGameAnalysis({ ...game(), analysis: stored }, true, false, '/games', false, 'account-1'),
    );
    await waitFor(() => expect(hook.result.current.report?.evaluations).toHaveLength(3));
    expect(hook.result.current.report?.evaluations.map((entry) => entry.agent_id)).toEqual([
      'attackers',
      'defenders',
      'advisor',
    ]);
  });

  it('fails closed when the server execution policy projection is malformed', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) {
        return { ...settings('browser_only'), execution: { mode: 'local' } };
      }
      throw new Error('analysis endpoint must not be called');
    });
    const hook = renderHook(() =>
      useGameAnalysis(game(), true, false, '/games', false, 'account-1'),
    );
    await waitFor(() => expect(hook.result.current.settings.execution).toBeDefined());
    expect(hook.result.current.executionMode).toBe('disabled');
    expect(hook.result.current.pending).toBe(false);
    expect(local.runLocalAnalysis).not.toHaveBeenCalled();
    expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(false);
  });

  it('refreshes changed policy and does not fall back under a stale browser_preferred decision', async () => {
    let mode = 'browser_preferred';
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings(mode);
      if (path.endsWith('/analysis')) return { ...localReport(), status: 'complete' };
      throw new Error(`unexpected ${path}`);
    });
    local.runLocalAnalysis.mockImplementation(async () => {
      mode = 'server_only';
      throw Object.assign(new Error('policy changed'), {
        category: 'disabled',
        code: 'policy-changed',
        fallbackEligible: false,
      });
    });
    const hook = renderHook(() =>
      useGameAnalysis(game(), true, false, '/games', false, 'account-1'),
    );
    await waitFor(() => expect(local.runLocalAnalysis).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(hook.result.current.executionMode).toBe('server_only'));
    await waitFor(() => expect(hook.result.current.report?.status).toBe('complete'));
    expect(apiMock.mock.calls.filter(([path]) => String(path).endsWith('/analysis'))).toHaveLength(
      1,
    );
  });

  it('does not restore a stale stored hint when new advice has no hint', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings('browser_only');
      throw new Error('unexpected analysis endpoint');
    });
    local.runLocalAnalysis.mockResolvedValue({
      report: { ...localReport(), hint: null },
      evidence: {},
      provider: 'wasm',
      offline: false,
      model: null,
      fromCache: true,
    });
    const stored: PositionAnalysis = {
      ...localReport(),
      advisor_revision: 1,
      hint: { agent_id: 'advisor', move: { source: 1, target: 2 } },
      evaluations: [
        {
          ...localReport().evaluations[0],
          agent_id: 'advisor',
          source: 'unavailable',
          unavailable_reason: 'model_unavailable',
        },
      ],
    };
    const hook = renderHook(() =>
      useGameAnalysis({ ...game(), analysis: stored }, true, false, '/games', false, 'account-1'),
    );
    await waitFor(() => expect(hook.result.current.report?.status).toBe('complete'));
    expect(hook.result.current.report?.hint).toBeNull();
  });

  it('does not invalidate a running local search on an unchanged visibility refresh', async () => {
    let finish!: (value: any) => void;
    local.runLocalAnalysis.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings('browser_only');
      throw new Error(`unexpected ${path}`);
    });
    const hook = renderHook(() =>
      useGameAnalysis(game(), true, false, '/games', false, 'account-1'),
    );
    await waitFor(() => expect(local.runLocalAnalysis).toHaveBeenCalledTimes(1));
    const analyticsReads = apiMock.mock.calls.filter(([path]) =>
      String(path).endsWith('/analytics'),
    ).length;
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() =>
      expect(
        apiMock.mock.calls.filter(([path]) => String(path).endsWith('/analytics')).length,
      ).toBeGreaterThan(analyticsReads),
    );
    expect(local.runLocalAnalysis).toHaveBeenCalledTimes(1);
    expect(hook.result.current.pending).toBe(true);
    finish({
      report: { ...localReport(), position_key: 'server-position-key-0' },
      evidence: {},
      provider: 'wasm',
      offline: false,
      model: null,
      fromCache: true,
    });
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
  });

  it('observes a genuinely pending retired server task instead of starting a new fallback', async () => {
    let statusReads = 0;
    let localA = 0;
    local.runLocalAnalysis.mockImplementation(async ({ game: current }: any) => {
      if (current.id === 'g1') {
        localA += 1;
        if (localA === 1) {
          throw Object.assign(new Error('unsupported browser'), {
            category: 'browser-unsupported',
            fallbackEligible: true,
          });
        }
      }
      return {
        report: { ...localReport(), position_key: `${current.id}-${localA}` },
        evidence: {},
        provider: 'wasm',
        offline: false,
        model: null,
        fromCache: true,
      };
    });
    apiMock.mockImplementation(async (path: string) => {
      if (path.endsWith('/analytics')) return settings('browser_preferred');
      if (String(path).includes('/analysis-status')) return { active: ++statusReads < 2 };
      if (String(path).endsWith('/analysis')) return { ...localReport(), status: 'pending' };
      throw new Error(`unexpected ${path}`);
    });
    const base = game();
    const other = { ...base, id: 'g2' };
    const hook = renderHook(
      ({ current }) => useGameAnalysis(current, true, false, '/games', false, 'account-1'),
      { initialProps: { current: base } },
    );
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([path]) => String(path).endsWith('/analysis'))).toBe(true),
    );
    await waitFor(() => expect(hook.result.current.fallback).toBe(true));
    hook.rerender({ current: other });
    await waitFor(() => expect(hook.result.current.report?.position_key).toBe('g2-1'));
    hook.rerender({ current: base });
    await waitFor(() => expect(statusReads).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(hook.result.current.report?.position_key).toBe('g1-2'));
    expect(apiMock.mock.calls.filter(([path]) => String(path).endsWith('/analysis'))).toHaveLength(
      1,
    );
  });
});
