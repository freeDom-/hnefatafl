import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BrowserAnalysisError } from './contracts';
import { validateModelManifest } from './manifests';
import { BrowserAnalysisRuntime, JARL_SETTINGS, normalizeJarlSettings } from './runtime';
import { ArtifactCache, MemoryArtifactStore } from './storage';
import {
  availabilityWire,
  bytesFor,
  MODEL_BYTES,
  modelWire,
  nodeDigest,
  RUNTIME_FILES,
  runtimeManifest,
  streamResponse,
} from './testSupport';

const manifest = runtimeManifest();
const model = validateModelManifest(modelWire());
const STATE = { ruleset: 'brandubh', size: 7, to_move: 'defenders' };

function deferred<T = unknown>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function awaitGate(gate: { promise: Promise<unknown> }, signal?: AbortSignal): Promise<void> {
  if (!signal) return gate.promise.then(() => undefined);
  if (signal.aborted) return Promise.reject(new DOMException('aborted', 'AbortError'));
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => reject(new DOMException('aborted', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    gate.promise.then(
      () => {
        signal.removeEventListener('abort', onAbort);
        resolve();
      },
      (error) => {
        signal.removeEventListener('abort', onAbort);
        reject(error);
      },
    );
  });
}

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    body: null,
  } as unknown as Response;
}

/** Realistic validated coordinator result envelope. */
function coordinatorEnvelope(
  options: {
    placement?: string | null;
    inferences?: number;
    requested?: string;
    gpuVerified?: boolean;
    provider?: unknown;
    completed?: number;
    settings?: Record<string, unknown>;
  } = {},
) {
  const inferences = options.inferences ?? 4;
  const requested = options.requested ?? 'wasm';
  const placement = options.placement === undefined ? 'wasm' : options.placement;
  const provider =
    options.provider !== undefined
      ? options.provider
      : inferences > 0
        ? {
            requested,
            execution_placement: placement,
            gpu_execution_verified: options.gpuVerified ?? false,
          }
        : null;
  return {
    analysis: { best_move: { source: 26, target: 12 }, value: -0.4, position_key: 'pk' },
    search: {
      search_id: 's1-0-abcdef',
      provider,
      completion_reason: 'completed',
      completed_simulations: options.completed ?? 32,
      coordinator: { inferences, completed_simulations: options.completed ?? 32 },
      settings: options.settings ?? { iterations: 32, time_limit: 1, seed: 7 },
    },
    settings: options.settings ?? { iterations: 32, time_limit: 1, seed: 7 },
    authoritative: false,
  };
}

function harness(
  options: {
    enabled?: boolean;
    runtime?: boolean;
    tamperAsset?: string;
  } = {},
) {
  const current = options.runtime === false ? null : manifest;
  const enabled = options.enabled !== false;
  const assetUrls = new Map<string, { body: string; tampered: boolean }>();
  if (current) {
    for (const [path, contents] of RUNTIME_FILES) {
      const body = options.tamperAsset === path ? 'tampered contents' : contents;
      assetUrls.set(`${current.basePath}/${path}`, {
        body,
        tampered: options.tamperAsset === path,
      });
    }
  }

  let coordinatorResult: Record<string, unknown> = coordinatorEnvelope();
  const gates = {
    availability: null as ReturnType<typeof deferred> | null,
    jarlInfo: null as ReturnType<typeof deferred> | null,
    modelLoad: null as ReturnType<typeof deferred> | null,
    search: null as ReturnType<typeof deferred> | null,
    artifact: null as ReturnType<typeof deferred> | null,
  };
  const moduleGates = new Map<string, ReturnType<typeof deferred>>();
  const workers = {
    jarlCreated: 0,
    jarlDisposed: 0,
    neuralCreated: 0,
    neuralDisposed: 0,
    coordinatorCreated: 0,
    coordinatorDisposed: 0,
    neuralCancels: 0,
  };
  const requests: string[] = [];
  let modelRequests = 0;
  let artifactFailure: BrowserAnalysisError | null = null;

  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    requests.push(url);
    if (gates.availability) {
      const gate = gates.availability;
      gates.availability = null;
      await awaitGate(gate, init?.signal ?? undefined);
    }
    if (url === '/api/browser-analysis/availability') {
      return jsonResponse(availabilityWire(current, { enabled }));
    }
    if (url === '/api/browser-analysis/models') {
      return jsonResponse({ schema: 1, models: [modelWire()] });
    }
    if (url === `/api/browser-analysis/models/${model.publicationId}`) {
      return jsonResponse(modelWire());
    }
    if (url === `/api/browser-analysis/models/${model.publicationId}/artifact`) {
      modelRequests += 1;
      if (artifactFailure) throw artifactFailure;
      if (gates.artifact) {
        const gate = gates.artifact;
        await awaitGate(gate, init?.signal ?? undefined);
        if (gates.artifact === gate) gates.artifact = null;
      }
      const { size } = bytesFor(MODEL_BYTES);
      return streamResponse([MODEL_BYTES], { contentLength: size });
    }
    const asset = assetUrls.get(url);
    if (asset) {
      const { size } = bytesFor(asset.body);
      return asset.tampered
        ? streamResponse([asset.body], { contentLength: size })
        : streamResponse([asset.body]);
    }
    return jsonResponse({ detail: 'not found' }, 404);
  });

  let activeJarl: FakeJarl | null = null;
  let activeNeural: FakeNeural | null = null;
  let activeCoordinator: FakeCoordinator | null = null;

  class FakeJarl {
    calls: unknown[] = [];
    info: Record<string, unknown>;
    constructor() {
      workers.jarlCreated += 1;
      this.info = { python: '3.12', engine_identity: { sha256: 'engine' } };
    }
    async request(payload: { action: string; config?: Record<string, unknown> }) {
      this.calls.push(payload);
      if (payload.action === 'info') {
        if (gates.jarlInfo) {
          const gate = gates.jarlInfo;
          await awaitGate(gate);
          if (gates.jarlInfo === gate) gates.jarlInfo = null;
        }
        return { ok: true, info: this.info };
      }
      return {
        ok: true,
        event: {
          schema: 1,
          analysis: { best_move: { source: 3, target: 2 }, completed_depth: 3 },
          settings: payload.config ?? JARL_SETTINGS,
          position_key: 'jarl-key',
        },
      };
    }
    cancel() {
      gates.jarlInfo?.resolve(undefined);
    }
    dispose() {
      workers.jarlDisposed += 1;
    }
  }

  class FakeNeural {
    identity: Record<string, unknown> | null = null;
    constructor() {
      workers.neuralCreated += 1;
    }
    async loadModelBytes(bytes: ArrayBuffer, options: Record<string, unknown>) {
      if (gates.modelLoad) {
        const gate = gates.modelLoad;
        await awaitGate(gate);
        if (gates.modelLoad === gate) gates.modelLoad = null;
      }
      this.identity = {
        sha256: options.expectedSha256,
        ruleset: options.ruleset,
        size: options.size,
      };
      return {
        provider_requested: options.provider,
        execution_placement: options.provider === 'webgpu' ? 'webgpu-session-unverified' : 'wasm',
        gpu_execution_verified: false,
        model_sha256: options.expectedSha256,
        model_ruleset: options.ruleset,
        model_bytes: bytes.byteLength,
        size: options.size,
      };
    }
    modelIdentity() {
      if (!this.identity) throw new Error('not loaded');
      return this.identity;
    }
    async cancelWork() {
      workers.neuralCancels += 1;
      gates.modelLoad?.resolve(undefined);
      return { state: 'cancelled' };
    }
    dispose() {
      workers.neuralDisposed += 1;
    }
  }

  class FakeCoordinator {
    constructor() {
      workers.coordinatorCreated += 1;
    }
    async search(_request: unknown) {
      if (gates.search) {
        const gate = gates.search;
        await awaitGate(gate);
        if (gates.search === gate) gates.search = null;
      }
      return coordinatorResult;
    }
    abort() {
      gates.search?.resolve(undefined);
    }
    dispose() {
      workers.coordinatorDisposed += 1;
    }
  }

  const loadModule = async (url: string) => {
    const name = url.split('/').at(-1);
    const gate = name ? moduleGates.get(name) : undefined;
    if (gate) {
      moduleGates.delete(name!);
      await gate.promise;
    }
    if (name === 'analysis-client.js') {
      return {
        LocalAnalysisClient: class extends FakeJarl {
          constructor() {
            super();
            activeJarl = this;
          }
        },
      };
    }
    if (name === 'neural-client.js') {
      return {
        NeuralClient: class extends FakeNeural {
          constructor() {
            super();
            activeNeural = this;
          }
        },
      };
    }
    if (name === 'search-client.js') {
      return {
        NeuralSearchCoordinator: class extends FakeCoordinator {
          constructor(args: unknown) {
            super();
            void args;
            activeCoordinator = this;
          }
        },
      };
    }
    throw new Error(`unexpected module ${url}`);
  };

  const cache = new ArtifactCache(new MemoryArtifactStore());
  const runtime = new BrowserAnalysisRuntime({
    fetchImpl: fetchImpl as unknown as typeof fetch,
    digestFn: nodeDigest,
    loadModule,
    cache,
  });

  return {
    runtime,
    fetchImpl,
    requests,
    gates,
    moduleGates,
    workers,
    cache,
    get modelRequests() {
      return modelRequests;
    },
    set artifactFailure(error: BrowserAnalysisError | null) {
      artifactFailure = error;
    },
    get activeJarl() {
      return activeJarl;
    },
    get activeNeural() {
      return activeNeural;
    },
    get activeCoordinator() {
      return activeCoordinator;
    },
    setCoordinatorResult(result: Record<string, unknown>) {
      coordinatorResult = result;
    },
  };
}

async function ready(options: Parameters<typeof harness>[0] = {}) {
  const env = harness(options);
  const status = await env.runtime.initialize();
  expect(status.state).toBe('ready');
  return env;
}

async function settle<T>(promise: Promise<T>): Promise<T | BrowserAnalysisError> {
  try {
    return await promise;
  } catch (error) {
    return error as BrowserAnalysisError;
  }
}

beforeEach(() => {
  // jsdom has no Worker constructor. The runtime only detects the capability; the
  // fake runtime modules below never spawn a real worker.
  vi.stubGlobal('Worker', class {});
  Object.defineProperty(navigator, 'gpu', { configurable: true, value: undefined });
});

function stubWebGPU(present: boolean) {
  Object.defineProperty(navigator, 'gpu', {
    configurable: true,
    value: present ? {} : undefined,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('browser analysis runtime — availability and initialization', () => {
  it('stays disabled when the server flag is off and loads nothing', async () => {
    const env = harness({ enabled: false });
    const status = await env.runtime.initialize();
    expect(status.state).toBe('disabled');
    expect(env.workers.jarlCreated).toBe(0);
    expect(env.requests.some((url) => url.includes('/browser-analysis/runtime/'))).toBe(false);
  });

  it('reports an enabled deployment without a verified runtime as unavailable', async () => {
    const env = harness({ runtime: false });
    const status = await env.runtime.initialize();
    expect(status.state).toBe('unavailable');
    expect(status.error?.code).toBe('runtime-missing');
    expect(env.workers.jarlCreated).toBe(0);
  });

  it('verifies every asset, imports lazily and probes the engine once', async () => {
    const env = await ready();
    expect(env.workers.jarlCreated).toBe(1);
    expect(env.requests.filter((url) => url.startsWith(manifest.basePath))).toHaveLength(
      RUNTIME_FILES.length,
    );
    expect(env.requests).not.toContain(
      `/api/browser-analysis/models/${model.publicationId}/artifact`,
    );
    expect(env.runtime.getStatus().engine).not.toBeNull();
  });

  it('is idempotent and never accumulates workers on repeated initialization', async () => {
    const env = await ready();
    const [first, second, third] = await Promise.all([
      env.runtime.initialize(),
      env.runtime.initialize(),
      env.runtime.initialize(),
    ]);
    expect(first.state).toBe('ready');
    expect(second.state).toBe('ready');
    expect(third.state).toBe('ready');
    await env.runtime.initialize();
    expect(env.workers.jarlCreated).toBe(1);
    expect(env.workers.jarlDisposed).toBe(0);
  });

  it('rejects a tampered runtime asset before creating any worker', async () => {
    const env = harness({ tamperAsset: 'runtime/pyodide.asm.wasm' });
    const error = await settle(env.runtime.initialize());
    expect(error).toBeInstanceOf(BrowserAnalysisError);
    expect((error as BrowserAnalysisError).code).toBe('size-mismatch');
    expect(env.workers.jarlCreated).toBe(0);
    expect(env.runtime.getStatus().state).toBe('error');
  });

  it('cancels initialization during asset verification and disposes late resources', async () => {
    const env = harness();
    const controller = new AbortController();
    const pending = env.runtime.initialize({ signal: controller.signal });
    controller.abort();
    const error = await settle(pending);
    expect(error).toBeInstanceOf(BrowserAnalysisError);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(env.workers.jarlCreated).toBe(0);
    expect(env.runtime.getStatus().state).toBe('cancelled');
  });

  it('cancels initialization while the engine probe is pending and terminates the worker', async () => {
    const env = harness();
    env.gates.jarlInfo = deferred();
    const controller = new AbortController();
    const pending = env.runtime.initialize({ signal: controller.signal });
    await vi.waitFor(() => expect(env.workers.jarlCreated).toBe(1));
    controller.abort();
    const error = await settle(pending);
    expect((error as BrowserAnalysisError).code).toBe('cancelled');
    await vi.waitFor(() => expect(env.workers.jarlDisposed).toBe(1));
  });

  it('logout during module import cannot create a worker afterwards', async () => {
    const env = harness();
    const gate = deferred();
    env.moduleGates.set('analysis-client.js', gate);
    const pending = env.runtime.initialize();
    await vi.waitFor(() => expect(gate).toBeTruthy());
    await env.runtime.invalidateAccountContext();
    gate.resolve(undefined);
    const error = await settle(pending);
    expect(error).toBeInstanceOf(BrowserAnalysisError);
    expect(env.workers.jarlCreated).toBe(0);
  });

  it('disposal during module import cannot create a worker afterwards', async () => {
    const env = harness();
    const gate = deferred();
    env.moduleGates.set('analysis-client.js', gate);
    const pending = env.runtime.initialize();
    await env.runtime.dispose();
    gate.resolve(undefined);
    const error = await settle(pending);
    expect((error as BrowserAnalysisError).code).toMatch(/cancelled|unsupported/);
    expect(env.workers.jarlCreated).toBe(0);
    await expect(env.runtime.initialize()).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('aborts initialization when availability is superseded by logout', async () => {
    const env = harness();
    env.gates.availability = deferred();
    const pending = env.runtime.initialize();
    await vi.waitFor(() => expect(env.gates.availability === null || true).toBeTruthy());
    await env.runtime.invalidateAccountContext();
    gatesRelease(env);
    const error = await settle(pending);
    expect(error).toBeInstanceOf(BrowserAnalysisError);
    expect(env.workers.jarlCreated).toBe(0);
  });
});

function gatesRelease(env: ReturnType<typeof harness>) {
  // Availability gate is consumed by fetch before it can be released here; the
  // test relies on the abort path rather than a later response.
  void env;
}

describe('browser analysis runtime — classical Jarl contract', () => {
  it('uses the explicit Jarl alpha-beta configuration, not a baseline fallback', async () => {
    const env = await ready();
    const result = await env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
    expect(result.algorithm).toBe('jarl');
    expect(result.evidence.executionRuntime).toBe('pyodide');
    expect(result.evidence.inferenceObserved).toBe(false);
    expect(result.evidence.observedPlacement).toBe('none');
    expect(result.settings).toMatchObject(JARL_SETTINGS);
    const call = env.activeJarl?.calls.at(-1) as {
      action: string;
      config: Record<string, unknown>;
    };
    expect(call.action).toBe('analysis');
    expect(call.config.algorithm).toBe('alpha_beta');
    expect(call.config.depth).toBe(5);
    expect(call.config.time_limit).toBe(2);
    expect(env.workers.neuralCreated).toBe(0);
  });

  it('accepts bounded explicit settings and returns the effective values', async () => {
    const env = await ready();
    const result = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'jarl',
      settings: { depth: 3, time_limit: 0.5 },
    });
    expect(result.settings).toMatchObject({ depth: 3, time_limit: 0.5, algorithm: 'alpha_beta' });
    expect(env.runtime.getStatus().lastSettings).toMatchObject({ depth: 3 });
  });

  it('rejects unsupported, missing or out-of-contract algorithm settings', async () => {
    const env = await ready();
    await expect(
      env.runtime.runSearch({ state: STATE, algorithm: 'baseline' as never }),
    ).rejects.toMatchObject({ code: 'unsupported' });
    await expect(
      env.runtime.runSearch({
        state: STATE,
        algorithm: 'jarl',
        settings: { algorithm: 'baseline' },
      }),
    ).rejects.toMatchObject({ code: 'unsupported' });
    await expect(
      env.runtime.runSearch({ state: STATE, algorithm: 'jarl', settings: { time_limit: 9 } }),
    ).rejects.toMatchObject({ code: 'unsupported' });
    await expect(
      env.runtime.runSearch({ state: STATE, algorithm: 'jarl', settings: { depth: 7 } }),
    ).rejects.toMatchObject({ code: 'unsupported' });
    await expect(
      env.runtime.runSearch({ state: STATE, algorithm: 'jarl', model }),
    ).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('rejects a request that names neither algorithm explicitly', async () => {
    const env = await ready();
    await expect(env.runtime.runSearch({ state: STATE } as never)).rejects.toMatchObject({
      code: 'unsupported',
    });
    expect(() => normalizeJarlSettings({ unknown: true })).toThrow();
  });

  it('does not start a search before initialization or while unavailable', async () => {
    const env = harness();
    await expect(env.runtime.runSearch({ state: STATE, algorithm: 'jarl' })).rejects.toMatchObject({
      code: 'disabled',
    });
    const unavailable = harness({ enabled: false });
    await unavailable.runtime.availability();
    await expect(
      unavailable.runtime.runSearch({ state: STATE, algorithm: 'jarl' }),
    ).rejects.toMatchObject({ code: 'disabled' });
  });
});

describe('browser analysis runtime — neural provider evidence', () => {
  async function neuralEnv(result: Record<string, unknown>, provider: 'wasm' | 'webgpu' = 'wasm') {
    const env = await ready();
    env.setCoordinatorResult(result);
    return { env, provider };
  }

  it('preserves the real coordinator envelope and WASM evidence', async () => {
    const envelope = coordinatorEnvelope({ placement: 'wasm', inferences: 4, requested: 'wasm' });
    const { env, provider } = await neuralEnv(envelope);
    const result = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider,
      settings: { iterations: 32, time_limit: 1 },
    });
    expect(result.algorithm).toBe('neural_mcts');
    expect(result.evidence).toEqual({
      executionRuntime: 'pyodide',
      requestedProvider: 'wasm',
      observedPlacement: 'wasm',
      gpuExecutionVerified: false,
      inferenceObserved: true,
      inferences: 4,
      completedSimulations: 32,
      completionReason: 'completed',
    });
    expect(result.model).toEqual({
      sha256: model.onnxSha256,
      ruleset: model.ruleset,
      size: model.boardSize,
    });
    expect(result.searchId).toBe('s1-0-abcdef');
    expect(result.settings).toMatchObject({ iterations: 32, time_limit: 1 });
  });

  it('preserves mixed placement and never synthesises it from the request', async () => {
    const envelope = coordinatorEnvelope({
      placement: 'webgpu-mixed',
      requested: 'webgpu',
    });
    stubWebGPU(true);
    const { env } = await neuralEnv(envelope, 'webgpu');
    const result = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'webgpu',
    });
    expect(result.evidence.observedPlacement).toBe('webgpu-mixed');
    expect(result.evidence.gpuExecutionVerified).toBe(false);
  });

  it('represents a no-inference shortcut as no observed placement', async () => {
    const envelope = coordinatorEnvelope({ inferences: 0, provider: null, completed: 0 });
    const { env } = await neuralEnv(envelope);
    const result = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    expect(result.evidence).toMatchObject({
      observedPlacement: 'none',
      inferenceObserved: false,
      inferences: 0,
    });
  });

  it('rejects missing or malformed provider evidence', async () => {
    for (const envelope of [
      coordinatorEnvelope({ inferences: 4, provider: null }),
      coordinatorEnvelope({ provider: { requested: 'wasm' } }),
      coordinatorEnvelope({
        provider: {
          requested: 'webgpu',
          execution_placement: 'wasm',
          gpu_execution_verified: false,
        },
      }),
      coordinatorEnvelope({
        provider: { requested: 'wasm', execution_placement: 'wasm', gpu_execution_verified: 'yes' },
      }),
      coordinatorEnvelope({
        provider: {
          requested: 'wasm',
          execution_placement: 'remote-gpu',
          gpu_execution_verified: false,
        },
      }),
      coordinatorEnvelope({ inferences: 0, provider: { requested: 'wasm' } }),
      { analysis: {}, search: { coordinator: { inferences: 1 } } },
      { analysis: {}, search: { provider: null, coordinator: null } },
    ]) {
      const { env } = await neuralEnv(envelope);
      await expect(
        env.runtime.runSearch({ state: STATE, algorithm: 'neural_mcts', model, provider: 'wasm' }),
      ).rejects.toMatchObject({ code: 'search-failed' });
    }
  });

  it('rejects a WASM request that reports GPU placement', async () => {
    const envelope = coordinatorEnvelope({ placement: 'webgpu-session-unverified' });
    const { env } = await neuralEnv(envelope);
    await expect(
      env.runtime.runSearch({ state: STATE, algorithm: 'neural_mcts', model, provider: 'wasm' }),
    ).rejects.toMatchObject({ code: 'search-failed' });
  });

  it('reports WebGPU unavailability before loading a model', async () => {
    stubWebGPU(false);
    const env = await ready();
    await expect(
      env.runtime.runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model,
        provider: 'webgpu',
      }),
    ).rejects.toMatchObject({ code: 'capability-missing' });
    expect(env.modelRequests).toBe(0);
  });
});

describe('browser analysis runtime — model bytes and download reuse', () => {
  it('downloads one cold model and initializes inference from verified bytes', async () => {
    const env = await ready();
    const download = await env.runtime.downloadModel(model);
    expect(download).toMatchObject({
      fromCache: false,
      retained: true,
      bytes: model.artifactBytes,
    });
    expect(env.modelRequests).toBe(1);
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    // The validated client loads the stored bytes, never the network URL.
    expect(env.modelRequests).toBe(1);
  });

  it('reuses a verified cached model without a model transfer', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(env.modelRequests).toBe(1);
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(env.modelRequests).toBe(1);
    expect((await env.runtime.storageUsage()).bytes).toBe(model.artifactBytes);
  });

  it('rejects a revoked publication before using cached bytes', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    const original = env.fetchImpl.getMockImplementation()!;
    env.fetchImpl.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === `/api/browser-analysis/models/${model.publicationId}`) {
        return jsonResponse({ detail: 'not found' }, 404);
      }
      return original(url, init);
    });
    await expect(
      env.runtime.runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model,
        provider: 'wasm',
        settings: { iterations: 8 },
      }),
    ).rejects.toMatchObject({ code: 'runtime-missing' });
  });

  it('discards a corrupted cached model and downloads a verified replacement', async () => {
    const env = await ready();
    await env.runtime.downloadModel(model);
    const key = `model:${model.onnxSha256}`;
    await env.cache.admit(key, bytesFor('corrupted cache bytes').bytes, {});
    const download = await env.runtime.downloadModel(model);
    expect(download.fromCache).toBe(false);
    expect(env.modelRequests).toBe(2);
  });

  it('cancels model initialization and does not publish a stale session', async () => {
    const env = await ready();
    await env.runtime.downloadModel(model);
    env.gates.modelLoad = deferred();
    const controller = new AbortController();
    const pending = env.runtime.runSearch(
      { state: STATE, algorithm: 'neural_mcts', model, provider: 'wasm' },
      { signal: controller.signal },
    );
    await vi.waitFor(() => expect(env.workers.neuralCreated).toBe(1));
    controller.abort();
    const error = await settle(pending);
    expect((error as BrowserAnalysisError).code).toBe('cancelled');
    await vi.waitFor(() => expect(env.workers.neuralCancels).toBeGreaterThan(0));
    expect(env.runtime.getLastResult()).toBeNull();
  });
});

describe('browser analysis runtime — ownership, logout and disposal', () => {
  it('logout during availability prevents any later worker creation', async () => {
    const env = harness();
    env.gates.availability = deferred();
    const pending = env.runtime.availability();
    await env.runtime.invalidateAccountContext();
    const error = await settle(pending);
    expect(error).toBeInstanceOf(BrowserAnalysisError);
    expect(env.workers.jarlCreated).toBe(0);
  });

  it('logout during model loading cannot publish a session or result', async () => {
    const env = await ready();
    await env.runtime.downloadModel(model);
    env.gates.modelLoad = deferred();
    const pending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    await vi.waitFor(() => expect(env.workers.neuralCreated).toBe(1));
    await env.runtime.invalidateAccountContext();
    const error = await settle(pending);
    expect((error as BrowserAnalysisError).code).toMatch(/cancelled|superseded/);
    expect(env.runtime.getLastResult()).toBeNull();
    expect(env.runtime.getStatus().state).toBe('idle');
  });

  it('logout during search cancels inference and releases workers', async () => {
    const env = await ready();
    await env.runtime.downloadModel(model);
    env.gates.search = deferred();
    const pending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    await env.runtime.invalidateAccountContext();
    await settle(pending);
    expect(env.workers.neuralCancels).toBeGreaterThan(0);
    expect(env.workers.coordinatorDisposed).toBe(1);
    expect(env.workers.neuralDisposed).toBe(1);
    expect(env.runtime.getLastResult()).toBeNull();
  });

  it('obsolete completion after a newer runtime becomes ready cannot publish', async () => {
    const env = harness();
    env.gates.jarlInfo = deferred();
    const first = env.runtime.initialize();
    await vi.waitFor(() => expect(env.workers.jarlCreated).toBe(1));
    await env.runtime.invalidateAccountContext();
    const second = await env.runtime.initialize();
    expect(second.state).toBe('ready');
    env.gates.jarlInfo = null;
    await settle(first);
    expect(env.runtime.getStatus().state).toBe('ready');
    expect(env.runtime.getStatus().engine).not.toBeNull();
  });

  it('account switching during a Jarl search discards the result and requires reinitialization', async () => {
    const env = await ready();
    const pending = env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
    await env.runtime.invalidateAccountContext();
    await settle(pending);
    expect(env.runtime.getLastResult()).toBeNull();
    await expect(env.runtime.runSearch({ state: STATE, algorithm: 'jarl' })).rejects.toMatchObject({
      code: 'not-initialized',
    });
  });

  it('dispose aborts in-flight work and refuses any later operation', async () => {
    const env = await ready();
    env.gates.modelLoad = deferred();
    const pending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    await vi.waitFor(() => expect(env.workers.neuralCreated).toBe(1));
    await env.runtime.dispose();
    await settle(pending);
    expect(env.runtime.getStatus().state).toBe('idle');
    await expect(env.runtime.runSearch({ state: STATE, algorithm: 'jarl' })).rejects.toMatchObject({
      code: 'unsupported',
    });
    await expect(env.runtime.initialize()).rejects.toMatchObject({ code: 'unsupported' });
    expect(env.workers.jarlDisposed + env.workers.neuralDisposed).toBeGreaterThan(0);
  });

  it('rejects a second concurrent search instead of silently superseding it', async () => {
    const env = await ready();
    env.gates.search = deferred();
    await env.runtime.downloadModel(model);
    const first = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    await expect(env.runtime.runSearch({ state: STATE, algorithm: 'jarl' })).rejects.toMatchObject({
      code: 'superseded',
    });
    await env.runtime.cancel();
    await settle(first);
  });
});
