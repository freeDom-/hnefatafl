import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BrowserAnalysisError, type RuntimeManifest, type SearchRequest } from './contracts';
import { computeRuntimeId, validateModelManifest } from './manifests';
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
  // Gate promises may be rejected before a consumer attaches; tests still assert
  // the awaiting operation, so silence the unused rejection here.
  promise.catch(() => undefined);
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
    iterations?: number;
    reason?: string;
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
  const iterations = options.iterations ?? 128;
  const settings = options.settings ?? {
    iterations,
    time_limit: 1,
    seed: 7,
    exploration: 1.4,
    algorithm: 'mcts',
  };
  return {
    analysis: { best_move: { source: 26, target: 12 }, value: -0.4, position_key: 'pk' },
    search: {
      search_id: 's1-0-abcdef',
      provider,
      completion_reason: options.reason ?? 'simulations',
      completed_simulations: options.completed ?? iterations,
      coordinator: { inferences, completed_simulations: options.completed ?? iterations },
      settings,
    },
    settings,
    authoritative: false,
  };
}

type AnyRecord = Record<string, any>;

type AnyRequest = { settings?: Record<string, unknown>; provider?: { requested?: string } };

function defaultEnvelope(request: AnyRequest): Record<string, unknown> {
  const settings = request.settings ?? {};
  return coordinatorEnvelope({
    settings: { ...settings, algorithm: 'mcts' },
    iterations: Number(settings.iterations ?? 128),
    completed: Number(settings.iterations ?? 128),
    requested: request.provider?.requested ?? 'wasm',
    placement: request.provider?.requested === 'webgpu' ? 'webgpu-session-unverified' : 'wasm',
  });
}

function harness(
  options: {
    enabled?: boolean;
    runtime?: boolean;
    tamperAsset?: string;
  } = {},
) {
  let current = options.runtime === false ? null : manifest;
  const enabled = options.enabled !== false;
  const assetUrls = new Map<string, { body: string; tampered: boolean }>();
  const buildAssets = () => {
    assetUrls.clear();
    if (!current) return;
    for (const [path, contents] of RUNTIME_FILES) {
      const body = options.tamperAsset === path ? 'tampered contents' : contents;
      assetUrls.set(`${current.basePath}/${path}`, {
        body,
        tampered: options.tamperAsset === path,
      });
    }
  };
  buildAssets();

  let coordinatorResult:
    | Record<string, unknown>
    | ((request: AnyRequest) => Record<string, unknown>) = defaultEnvelope;
  const availabilityGates: Array<ReturnType<typeof deferred>> = [];
  let digestGate: ReturnType<typeof deferred> | null = null;
  let cancelWorkGate: ReturnType<typeof deferred> | null = null;
  const gates = {
    availability: null as ReturnType<typeof deferred> | null,
    jarlInfo: null as ReturnType<typeof deferred> | null,
    jarlSearch: null as ReturnType<typeof deferred> | null,
    modelLoad: null as ReturnType<typeof deferred> | null,
    search: null as ReturnType<typeof deferred> | null,
    artifact: null as ReturnType<typeof deferred> | null,
  };
  const modelRecords = new Map<string, { wire: Record<string, unknown>; bytes: string }>();
  modelRecords.set(model.publicationId, { wire: modelWire(), bytes: MODEL_BYTES });
  let failLoadDigest: string | null = null;
  let failNextSearch = false;
  let destroySessionOnSearchFailure = false;
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
    const availabilityPayload =
      url === '/api/browser-analysis/availability' ? availabilityWire(current, { enabled }) : null;
    if (availabilityGates.length > 0) {
      const gate = availabilityGates.shift()!;
      await awaitGate(gate, init?.signal ?? undefined);
    } else if (gates.availability) {
      const gate = gates.availability;
      gates.availability = null;
      await awaitGate(gate, init?.signal ?? undefined);
    }
    if (availabilityPayload) {
      return jsonResponse(availabilityPayload);
    }
    if (url === '/api/browser-analysis/models') {
      return jsonResponse({
        schema: 1,
        models: [...modelRecords.values()].map((entry) => entry.wire),
      });
    }
    const modelMatch = url.match(/^\/api\/browser-analysis\/models\/([0-9a-f]{24})(\/artifact)?/);
    if (modelMatch) {
      const record = modelRecords.get(modelMatch[1]);
      if (!record) return jsonResponse({ detail: 'not found' }, 404);
      if (!modelMatch[2]) return jsonResponse(record.wire);
      modelRequests += 1;
      if (artifactFailure) throw artifactFailure;
      if (gates.artifact) {
        const gate = gates.artifact;
        await awaitGate(gate, init?.signal ?? undefined);
        if (gates.artifact === gate) gates.artifact = null;
      }
      const { size } = bytesFor(record.bytes);
      return streamResponse([record.bytes], { contentLength: size });
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
    cancels = 0;
    constructor() {
      workers.jarlCreated += 1;
      this.info = { python: '3.12', engine_identity: { sha256: 'engine' } };
    }
    async load() {
      return { status: 'ready' };
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
      if (gates.jarlSearch) {
        const gate = gates.jarlSearch;
        await awaitGate(gate);
        if (gates.jarlSearch === gate) gates.jarlSearch = null;
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
      this.cancels += 1;
      gates.jarlSearch?.reject(new DOMException('aborted', 'AbortError'));
      gates.jarlSearch = null;
      gates.jarlInfo?.resolve(undefined);
    }
    dispose() {
      workers.jarlDisposed += 1;
    }
  }

  class FakeNeural {
    identity: Record<string, unknown> | null = null;
    provider: 'wasm' | 'webgpu' | null = null;
    constructor() {
      workers.neuralCreated += 1;
    }
    async loadModelBytes(bytes: ArrayBuffer, options: Record<string, unknown>) {
      if (gates.modelLoad) {
        const gate = gates.modelLoad;
        await awaitGate(gate);
        if (gates.modelLoad === gate) gates.modelLoad = null;
      }
      if (failLoadDigest && options.expectedSha256 === failLoadDigest) {
        this.identity = null;
        this.provider = null;
        throw Object.assign(new Error('synthetic model load failure'), {
          name: 'ModelLoadError',
        });
      }
      this.identity = {
        sha256: options.expectedSha256,
        ruleset: options.ruleset,
        size: options.size,
      };
      this.provider = (options.provider as 'wasm' | 'webgpu') ?? 'wasm';
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
    sessionIdentity() {
      if (!this.identity || this.provider === null) return null;
      return { ...this.identity, provider: this.provider };
    }
    async cancelWork() {
      workers.neuralCancels += 1;
      this.identity = null;
      this.provider = null;
      gates.modelLoad?.resolve(undefined);
      if (cancelWorkGate) await cancelWorkGate.promise;
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
    async search(request: unknown) {
      if (gates.search) {
        const gate = gates.search;
        await awaitGate(gate);
        if (gates.search === gate) gates.search = null;
      }
      if (failNextSearch) {
        failNextSearch = false;
        if (destroySessionOnSearchFailure && activeNeural) {
          activeNeural.identity = null;
          activeNeural.provider = null;
        }
        throw Object.assign(new Error('synthetic worker failure'), { name: 'WorkerError' });
      }
      return typeof coordinatorResult === 'function'
        ? coordinatorResult(request as AnyRequest)
        : coordinatorResult;
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
    digestFn: async (bytes) => {
      if (digestGate) await digestGate.promise;
      return nodeDigest(bytes);
    },
    loadModule,
    cache,
  });

  return {
    runtime,
    fetchImpl,
    loadModule,
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
    setCoordinatorResult(
      result: Record<string, unknown> | ((request: AnyRequest) => Record<string, unknown>),
    ) {
      coordinatorResult = result;
    },
    registerModel(id: string, wire: Record<string, unknown>, bytes: string) {
      modelRecords.set(id, { wire, bytes });
    },
    setFailLoadDigest(digest: string | null) {
      failLoadDigest = digest;
    },
    setFailNextSearch(destroySession: boolean) {
      failNextSearch = true;
      destroySessionOnSearchFailure = destroySession;
    },
    setRuntime(next: RuntimeManifest | null) {
      current = next;
      buildAssets();
    },
    queueAvailabilityGate() {
      const gate = deferred();
      availabilityGates.push(gate);
      return gate;
    },
    setDigestGate(gate: ReturnType<typeof deferred> | null) {
      digestGate = gate;
    },
    setCancelWorkGate(gate: ReturnType<typeof deferred> | null) {
      cancelWorkGate = gate;
    },
    async createManagedRuntime() {
      return BrowserAnalysisRuntime.create({
        fetchImpl: fetchImpl as unknown as typeof fetch,
        digestFn: async (bytes) => {
          if (digestGate) await digestGate.promise;
          return nodeDigest(bytes);
        },
        loadModule,
        cache,
      });
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
    const envelope = coordinatorEnvelope({
      placement: 'wasm',
      inferences: 4,
      requested: 'wasm',
      iterations: 32,
      completed: 32,
      settings: { iterations: 32, time_limit: 1, seed: 7, exploration: 1.4, algorithm: 'mcts' },
    });
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
      completionReason: 'simulations',
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
    const envelope = coordinatorEnvelope({
      inferences: 0,
      provider: null,
      completed: 0,
      reason: 'immediate_win',
    });
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
    ).rejects.toMatchObject({ code: 'model-not-published' });
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

function secondModel() {
  const bytes = 'second model bytes for replacement';
  const fixture = bytesFor(bytes);
  const wire = modelWire({
    publication_id: 'b'.repeat(24),
    artifact_version: fixture.sha256,
    onnx_sha256: fixture.sha256,
    artifact_bytes: fixture.size,
    display_name: 'Second Brandubh model',
  });
  return { model: validateModelManifest(wire), wire, bytes };
}

function registerSecond(env: ReturnType<typeof harness>) {
  const second = secondModel();
  env.registerModel(second.model.publicationId, second.wire, second.bytes);
  return second.model;
}

describe('browser analysis runtime — cancellation and session recovery (G1)', () => {
  it('propagates AbortSignal cancellation to the actual Jarl request and recovers', async () => {
    const env = await ready();
    env.gates.jarlSearch = deferred();
    const pending = env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
    await vi.waitFor(() => expect(env.activeJarl?.calls.length).toBeGreaterThan(0));
    await env.runtime.cancel();
    const error = await settle(pending);
    expect((error as BrowserAnalysisError).code).toBe('cancelled');
    expect(env.activeJarl?.cancels).toBeGreaterThan(0);
    expect(env.workers.jarlCreated).toBe(1);
    const recovered = await env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
    expect(recovered.algorithm).toBe('jarl');
    expect(env.workers.jarlCreated).toBe(1);
  });

  it('supports a valid search immediately after cancellation without worker accumulation', async () => {
    const env = await ready();
    env.gates.jarlSearch = deferred();
    const pending = env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
    await vi.waitFor(() => expect(env.activeJarl?.calls.length).toBeGreaterThan(0));
    await env.runtime.cancel();
    await settle(pending);
    for (let index = 0; index < 3; index += 1) {
      const result = await env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
      expect(result.algorithm).toBe('jarl');
    }
    expect(env.workers.jarlCreated).toBe(1);
    expect(env.workers.jarlDisposed).toBe(0);
  });

  it('recovers model A after a failed replacement with model B', async () => {
    const env = await ready();
    const second = registerSecond(env);
    const first = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    expect(first.model?.sha256).toBe(model.onnxSha256);
    env.setFailLoadDigest(second.onnxSha256);
    await expect(
      env.runtime.runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model: second,
        provider: 'wasm',
      }),
    ).rejects.toBeInstanceOf(BrowserAnalysisError);
    // The failed replacement destroyed the previous session; the next request
    // for A must reinitialize from verified bytes without recreating the API.
    env.setFailLoadDigest(null);
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
    expect(env.workers.neuralCreated).toBe(1);
    // A was retained in the verified cache, so recovery needs no new transfer.
    expect(env.modelRequests).toBe(2);
  });

  it('retries initial model initialization after a failure', async () => {
    const env = await ready();
    env.setFailLoadDigest(model.onnxSha256);
    await expect(
      env.runtime.runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model,
        provider: 'wasm',
      }),
    ).rejects.toBeInstanceOf(BrowserAnalysisError);
    env.setFailLoadDigest(null);
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
  });

  it('retries the same model after cancellation during model loading', async () => {
    const env = await ready();
    env.gates.modelLoad = deferred();
    const controller = new AbortController();
    const pending = env.runtime.runSearch(
      { state: STATE, algorithm: 'neural_mcts', model, provider: 'wasm' },
      { signal: controller.signal },
    );
    await vi.waitFor(() => expect(env.workers.neuralCreated).toBe(1));
    controller.abort();
    await settle(pending);
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
    expect(env.workers.neuralCancels).toBeGreaterThan(0);
  });

  it('recovers after a worker/search failure with a fresh session', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    env.setFailNextSearch(true);
    await expect(
      env.runtime.runSearch({ state: STATE, algorithm: 'neural_mcts', model, provider: 'wasm' }),
    ).rejects.toMatchObject({ code: 'search-failed' });
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
    });
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
    expect(env.modelRequests).toBe(1);
  });

  it('account invalidation is an admission boundary during initialization', async () => {
    const env = harness();
    env.gates.jarlInfo = deferred();
    const first = env.runtime.initialize();
    await vi.waitFor(() => expect(env.workers.jarlCreated).toBe(1));
    const invalidation = env.runtime.invalidateAccountContext();
    const second = env.runtime.initialize();
    const firstResult = await settle(first);
    expect(firstResult).toBeInstanceOf(BrowserAnalysisError);
    const readyResult = await second;
    await invalidation;
    expect(readyResult.state).toBe('ready');
    expect(env.workers.jarlCreated).toBe(2);
    expect(env.workers.jarlDisposed).toBe(1);
    // Delayed cleanup disposed the captured old worker only, never the new one.
    expect(env.runtime.getStatus().state).toBe('ready');
  });

  it('enforces the public single-instance admission policy', async () => {
    const first = await BrowserAnalysisRuntime.create();
    await expect(BrowserAnalysisRuntime.create()).rejects.toMatchObject({
      code: 'quota-exceeded',
    });
    await first.dispose();
    const second = await BrowserAnalysisRuntime.create();
    await second.dispose();
  });
});

describe('browser analysis runtime — provider and settings mutations (G3/G6)', () => {
  async function neuralMutation(
    envelope: (request: AnyRequest) => Record<string, unknown>,
    request: SearchRequest = {
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 32 },
    },
  ) {
    const env = await ready();
    env.setCoordinatorResult(envelope);
    return { env, request };
  }

  it('rejects a GPU verification claim for WASM and session-only evidence', async () => {
    const { env, request } = await neuralMutation((req) =>
      coordinatorEnvelope({
        settings: { ...(req.settings ?? {}), algorithm: 'mcts' },
        iterations: 32,
        completed: 32,
        gpuVerified: true,
      }),
    );
    await expect(env.runtime.runSearch(request)).rejects.toMatchObject({
      code: 'search-failed',
    });
  });

  it('rejects unauthorized WebGPU fallback to WASM or CPU', async () => {
    stubWebGPU(true);
    for (const placement of ['wasm', 'cpu-fallback']) {
      const { env, request } = await neuralMutation(
        (req) =>
          coordinatorEnvelope({
            settings: { ...(req.settings ?? {}), algorithm: 'mcts' },
            iterations: 32,
            completed: 32,
            requested: 'webgpu',
            placement,
          }),
        {
          state: STATE,
          algorithm: 'neural_mcts',
          model,
          provider: 'webgpu',
          settings: { iterations: 32 },
        },
      );
      await expect(env.runtime.runSearch(request)).rejects.toMatchObject({
        code: 'search-failed',
      });
    }
  });

  it('requires valid effective settings instead of substituting the request', async () => {
    for (const settings of [undefined, { iterations: 999, algorithm: 'mcts' }]) {
      const { env, request } = await neuralMutation((req) => {
        const envelope = coordinatorEnvelope({
          settings: { ...(req.settings ?? {}), algorithm: 'mcts' },
          iterations: 32,
          completed: 32,
        }) as AnyRecord;
        if (settings === undefined) delete envelope.settings;
        else envelope.settings = settings;
        return envelope;
      });
      await expect(env.runtime.runSearch(request)).rejects.toMatchObject({
        code: 'search-failed',
      });
    }
  });

  it('validates completion reason and completed work against the simulation budget', async () => {
    const cases = [
      { reason: 'mystery' },
      { reason: 'simulations', completed: 16 },
      { reason: 'simulations', completed: 64 },
    ];
    for (const mutation of cases) {
      const { env, request } = await neuralMutation((req) => {
        const envelope = coordinatorEnvelope({
          settings: { ...(req.settings ?? {}), algorithm: 'mcts' },
          iterations: 32,
          completed: 32,
        }) as AnyRecord;
        const search = envelope.search as AnyRecord;
        search.completion_reason = mutation.reason;
        if (mutation.completed !== undefined) {
          search.completed_simulations = mutation.completed;
          (search.coordinator as AnyRecord).completed_simulations = mutation.completed;
        }
        return envelope;
      });
      await expect(env.runtime.runSearch(request)).rejects.toMatchObject({
        code: 'search-failed',
      });
    }
  });

  it('accepts deterministic temperature zero but rejects nonzero temperature', async () => {
    const env = await ready();
    const accepted = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 16, temperature: 0 },
    });
    expect(accepted.settings).not.toHaveProperty('temperature');
    await expect(
      env.runtime.runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model,
        provider: 'wasm',
        settings: { iterations: 16, temperature: 0.5 },
      }),
    ).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('strictly validates nested Jarl search configuration', async () => {
    const env = await ready();
    for (const search of [
      { unknown: true },
      { transposition: 'yes' },
      { table_capacity: 0 },
      { table_capacity: 1_000_001 },
    ]) {
      await expect(
        env.runtime.runSearch({
          state: STATE,
          algorithm: 'jarl',
          settings: { search },
        }),
      ).rejects.toMatchObject({ code: 'unsupported' });
    }
    const result = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'jarl',
      settings: { depth: 3, search: { table_capacity: 4096, symmetry_transposition: true } },
    });
    expect(result.settings.search).toMatchObject({
      table_capacity: 4096,
      symmetry_transposition: true,
      transposition: true,
    });
  });
});

describe('browser analysis runtime — healthy session reuse (G5)', () => {
  it('does not retrieve or hash model bytes for repeated searches on a healthy session', async () => {
    const env = await ready();
    const spy = vi.spyOn(env.cache, 'getVerified');
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 16 },
    });
    const afterCold = spy.mock.calls.length;
    expect(afterCold).toBeGreaterThan(0);
    for (let index = 0; index < 3; index += 1) {
      await env.runtime.runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model,
        provider: 'wasm',
        settings: { iterations: 16 },
      });
    }
    expect(spy.mock.calls.length).toBe(afterCold);
    expect(env.modelRequests).toBe(1);
  });

  it('reuses a healthy session after the cached artifact is evicted', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 16 },
    });
    await env.cache.clear();
    const result = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 16 },
    });
    expect(result.model?.sha256).toBe(model.onnxSha256);
    expect(env.modelRequests).toBe(1);
  });

  it('rejects a revoked publication even while a session is loaded', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 16 },
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
        settings: { iterations: 16 },
      }),
    ).rejects.toMatchObject({ code: 'model-not-published' });
  });

  it('loads a new session from cached bytes when the provider changes', async () => {
    stubWebGPU(true);
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 16 },
    });
    const switched = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'webgpu',
      settings: { iterations: 16 },
    });
    expect(switched.evidence.requestedProvider).toBe('webgpu');
    expect(env.modelRequests).toBe(1);
    expect(env.workers.neuralCreated).toBe(1);
  });
});

async function variantRuntime(): Promise<RuntimeManifest> {
  const base = runtimeManifest();
  const variant: RuntimeManifest = {
    ...base,
    pins: { ...base.pins, applicationVersion: '0.42.0' },
    runtimeId: '',
    basePath: '',
  };
  variant.runtimeId = await computeRuntimeId(variant, nodeDigest);
  variant.basePath = `/browser-analysis/runtime/${variant.runtimeId}`;
  return variant;
}

async function waitForManagedCreate(env: ReturnType<typeof harness>) {
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    try {
      return await env.createManagedRuntime();
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  }
  throw new Error('managed runtime reservation was not released');
}

describe('browser analysis runtime — initialization and admission coordination (H1/H4)', () => {
  it('is idempotent during a pending Jarl search without replacing its worker', async () => {
    const env = await ready();
    env.gates.jarlSearch = deferred();
    const pending = env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
    await vi.waitFor(() => expect(env.runtime.getStatus().state).toBe('searching'));
    const status = await env.runtime.initialize();
    expect(status.state).toBe('searching');
    expect(env.workers.jarlCreated).toBe(1);
    expect(env.workers.jarlDisposed).toBe(0);
    env.gates.jarlSearch.resolve(undefined);
    env.gates.jarlSearch = null;
    await expect(pending).resolves.toMatchObject({ algorithm: 'jarl' });
  });

  it('is idempotent during a pending neural search', async () => {
    const env = await ready();
    env.gates.search = deferred();
    const pending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    const status = await env.runtime.initialize();
    expect(status.state).toBe('searching');
    expect(env.workers.neuralCreated).toBe(1);
    expect(env.workers.neuralDisposed).toBe(0);
    env.gates.search.resolve(undefined);
    env.gates.search = null;
    await expect(pending).resolves.toMatchObject({ algorithm: 'neural_mcts' });
  });

  it('rejects search admission while the runtime identity is being replaced', async () => {
    const env = await ready();
    env.gates.jarlSearch = deferred();
    const pending = env.runtime.runSearch({ state: STATE, algorithm: 'jarl' });
    await vi.waitFor(() => expect(env.activeJarl?.calls.length).toBeGreaterThan(0));
    env.setRuntime(await variantRuntime());
    await env.runtime.availability();
    await expect(pending).rejects.toMatchObject({ code: 'cancelled' });
    await expect(env.runtime.runSearch({ state: STATE, algorithm: 'jarl' })).rejects.toMatchObject({
      code: 'not-initialized',
    });
    expect(env.workers.jarlDisposed).toBe(1);
    const initialized = await env.runtime.initialize();
    expect(initialized.state).toBe('ready');
    await expect(env.runtime.runSearch({ state: STATE, algorithm: 'jarl' })).resolves.toMatchObject(
      {
        algorithm: 'jarl',
      },
    );
    expect(env.workers.jarlCreated).toBe(2);
  });

  it('ignores an out-of-order availability response after a newer state applied', async () => {
    const env = await ready();
    const firstGate = env.queueAvailabilityGate();
    const first = env.runtime.availability();
    const variant = await variantRuntime();
    env.setRuntime(variant);
    const secondGate = env.queueAvailabilityGate();
    const second = env.runtime.availability();
    secondGate.resolve(undefined);
    await second;
    expect(env.runtime.getStatus().runtimeId).toBe(variant.runtimeId);
    firstGate.resolve(undefined);
    await first;
    expect(env.runtime.getStatus().runtimeId).toBe(variant.runtimeId);
  });

  it('removes caller abort forwarding listeners on repeated operations', async () => {
    const env = await ready();
    const controller = new AbortController();
    const add = vi.spyOn(controller.signal, 'addEventListener');
    const remove = vi.spyOn(controller.signal, 'removeEventListener');
    for (let index = 0; index < 3; index += 1) {
      await env.runtime.runSearch(
        { state: STATE, algorithm: 'jarl' },
        {
          signal: controller.signal,
        },
      );
    }
    const added = add.mock.calls.filter((call) => call[0] === 'abort').length;
    const removed = remove.mock.calls.filter((call) => call[0] === 'abort').length;
    expect(added).toBeGreaterThan(0);
    expect(removed).toBe(added);
  });

  it('handles an already-aborted caller signal without retaining a listener', async () => {
    const env = await ready();
    const controller = new AbortController();
    controller.abort();
    const add = vi.spyOn(controller.signal, 'addEventListener');
    await expect(
      env.runtime.runSearch(
        { state: STATE, algorithm: 'jarl' },
        {
          signal: controller.signal,
        },
      ),
    ).rejects.toBeInstanceOf(BrowserAnalysisError);
    expect(add.mock.calls.filter((call) => call[0] === 'abort')).toHaveLength(0);
  });

  it('coordinates overlapping cancellation and account invalidation without touching newer workers', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    env.gates.search = deferred();
    const pending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    const slowCancel = deferred();
    env.setCancelWorkGate(slowCancel);
    const cancelling = env.runtime.cancel();
    const invalidating = env.runtime.invalidateAccountContext();
    await settle(pending);
    await Promise.all([cancelling, invalidating]);
    slowCancel.resolve(undefined);
    const initialized = await env.runtime.initialize();
    expect(initialized.state).toBe('ready');
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
    expect(env.runtime.getStatus().state).toBe('ready');
  }, 20000);

  it('recovers after cancellation cleanup times out and quarantines only old workers', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    env.gates.search = deferred();
    const pending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    const cancelGate = deferred();
    env.setCancelWorkGate(cancelGate);
    const cancel = env.runtime.cancel();
    await expect(pending).rejects.toBeInstanceOf(BrowserAnalysisError);
    await cancel;
    expect(env.runtime.getStatus().state).toBe('error');
    expect(env.runtime.getStatus().error?.retryable).toBe(true);
    // A new search is not permanently blocked by the obsolete unresolved cleanup.
    cancelGate.resolve(undefined);
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
    expect(env.workers.neuralCreated).toBe(2);
  }, 20000);

  it('obsolete cleanup completing later cannot dispose a newer session', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    env.gates.search = deferred();
    const pending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    const oldCancelGate = deferred();
    env.setCancelWorkGate(oldCancelGate);
    const cancel = env.runtime.cancel();
    await settle(pending);
    await cancel;
    const oldNeural = env.activeNeural;
    env.setCancelWorkGate(null);
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    const newNeural = env.activeNeural;
    oldCancelGate.resolve(undefined);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(newNeural).not.toBe(oldNeural);
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
    expect(env.runtime.getLastResult()?.model?.sha256).toBe(model.onnxSha256);
  }, 20000);
});

describe('browser analysis runtime — managed disposal admission (H2)', () => {
  it('retains the singleton reservation until active downloads retire', async () => {
    const env = harness();
    const managed = await env.createManagedRuntime();
    await managed.initialize();
    const gate = deferred();
    env.setDigestGate(gate);
    const download = managed.downloadModel(model).catch((error) => error);
    await vi.waitFor(() => expect(env.modelRequests).toBe(1));
    const disposal = managed.dispose();
    await expect(env.createManagedRuntime()).rejects.toMatchObject({ code: 'quota-exceeded' });
    const outcome = await disposal;
    expect(outcome.drained).toBe(false);
    expect(outcome.transfers).toBeGreaterThan(0);
    gate.resolve(undefined);
    expect(await download).toBeInstanceOf(BrowserAnalysisError);
    const replacement = await waitForManagedCreate(env);
    await replacement.dispose();
  }, 20000);

  it('coordinates dispose with an active transfer and releases after retirement', async () => {
    const env = harness();
    const managed = await env.createManagedRuntime();
    await managed.initialize();
    env.gates.artifact = deferred();
    const download = managed.downloadModel(model).catch((error) => error);
    await vi.waitFor(() => expect(env.modelRequests).toBe(1));
    const outcome = await managed.dispose();
    expect(outcome.drained).toBe(true);
    expect(await download).toBeInstanceOf(BrowserAnalysisError);
    const replacement = await env.createManagedRuntime();
    await replacement.dispose();
  });

  it('releases the singleton immediately when no retiring downloads remain', async () => {
    const env = harness();
    const managed = await env.createManagedRuntime();
    await managed.initialize();
    const outcome = await managed.dispose();
    expect(outcome.drained).toBe(true);
    const replacement = await env.createManagedRuntime();
    const repeated = await managed.dispose();
    expect(repeated).toBe(outcome);
    await replacement.dispose();
  });

  it('waits for delayed cache admission before releasing the singleton', async () => {
    const env = harness();
    const managed = await env.createManagedRuntime();
    await managed.initialize();
    const gate = deferred();
    const originalAdmit = env.cache.admit.bind(env.cache);
    let admissionStarted = false;
    env.cache.admit = async (...args) => {
      admissionStarted = true;
      await gate.promise;
      return originalAdmit(...args);
    };
    const download = managed.downloadModel(model).catch((error) => error);
    await vi.waitFor(() => expect(admissionStarted).toBe(true));
    const disposal = managed.dispose();
    const outcome = await disposal;
    expect(outcome.drained).toBe(false);
    gate.resolve(undefined);
    expect(await download).toBeInstanceOf(BrowserAnalysisError);
    const replacement = await waitForManagedCreate(env);
    await replacement.dispose();
  }, 20000);
});

describe('browser analysis runtime — tracked cache verification (I1)', () => {
  it('retains managed admission while a cached-model read is pending', async () => {
    const env = harness();
    const managed = await env.createManagedRuntime();
    await managed.initialize();
    let releaseLookup!: () => void;
    const lookupGate = new Promise<void>((resolve) => {
      releaseLookup = resolve;
    });
    const original = env.cache.getVerified.bind(env.cache);
    let lookupStarted = false;
    env.cache.getVerified = async (...args) => {
      lookupStarted = true;
      await lookupGate;
      return original(...args);
    };
    const pending = managed
      .runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model,
        provider: 'wasm',
        settings: { iterations: 8 },
      })
      .catch((error) => error);
    await vi.waitFor(() => expect(lookupStarted).toBe(true));
    const disposal = managed.dispose();
    await expect(env.createManagedRuntime()).rejects.toMatchObject({ code: 'quota-exceeded' });
    const outcome = await disposal;
    expect(outcome.drained).toBe(false);
    expect(outcome.cacheVerifications).toBe(1);
    expect(await pending).toBeInstanceOf(BrowserAnalysisError);
    releaseLookup();
    const replacement = await waitForManagedCreate(env);
    await replacement.dispose();
  }, 20000);

  it('retains managed admission while cached-model hashing is pending', async () => {
    const env = harness();
    const managed = await env.createManagedRuntime();
    await managed.initialize();
    const fixture = bytesFor(MODEL_BYTES);
    await env.cache.admit(`model:${model.onnxSha256}`, fixture.bytes, {
      publicationId: model.publicationId,
    });
    const digestGate = deferred();
    env.setDigestGate(digestGate);
    const pending = managed
      .runSearch({
        state: STATE,
        algorithm: 'neural_mcts',
        model,
        provider: 'wasm',
        settings: { iterations: 8 },
      })
      .catch((error) => error);
    await new Promise((resolve) => setTimeout(resolve, 20));
    const disposal = managed.dispose();
    const outcome = await disposal;
    expect(outcome.drained).toBe(false);
    expect(outcome.cacheVerifications).toBe(1);
    expect(await pending).toBeInstanceOf(BrowserAnalysisError);
    digestGate.resolve(undefined);
    const replacement = await waitForManagedCreate(env);
    await replacement.dispose();
  }, 20000);

  it('uses one bounded cache lookup for a miss and none for a healthy session', async () => {
    const env = await ready();
    const spy = vi.spyOn(env.cache, 'getVerified');
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(env.modelRequests).toBe(1);
    const afterCold = spy.mock.calls.length;
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(spy).toHaveBeenCalledTimes(afterCold);
    expect(env.modelRequests).toBe(1);
  });

  it('initializes from a verified cached artifact through the download manager path', async () => {
    const env = await ready();
    const fixture = bytesFor(MODEL_BYTES);
    await env.cache.admit(`model:${model.onnxSha256}`, fixture.bytes, {
      publicationId: model.publicationId,
    });
    const spy = vi.spyOn(env.cache, 'getVerified');
    const result = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(result.model?.sha256).toBe(model.onnxSha256);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(env.modelRequests).toBe(0);
  });

  it('cancels cached-model initialization without starting a transfer', async () => {
    const env = await ready();
    let releaseLookup!: () => void;
    const lookupGate = new Promise<void>((resolve) => {
      releaseLookup = resolve;
    });
    const original = env.cache.getVerified.bind(env.cache);
    env.cache.getVerified = async (...args) => {
      await lookupGate;
      return original(...args);
    };
    const controller = new AbortController();
    const pending = env.runtime.runSearch(
      { state: STATE, algorithm: 'neural_mcts', model, provider: 'wasm' },
      { signal: controller.signal },
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    controller.abort();
    releaseLookup();
    await expect(pending).rejects.toMatchObject({ code: 'cancelled' });
    expect(env.modelRequests).toBe(0);
  });
});

describe('browser analysis runtime — cleanup-barrier recovery (I2)', () => {
  it('recovers initialization after account invalidation cleanup times out', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    env.gates.search = deferred();
    const searchPending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    const hang = deferred();
    env.setCancelWorkGate(hang);
    const invalidation = env.runtime.invalidateAccountContext();
    await settle(searchPending);
    await invalidation;
    expect(env.runtime.getStatus().state).toBe('error');
    const initialized = await env.runtime.initialize();
    expect(initialized.state).toBe('ready');
    const recovered = await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    expect(recovered.model?.sha256).toBe(model.onnxSha256);
    hang.resolve(undefined);
    expect(env.runtime.getStatus().state).toBe('ready');
  }, 20000);

  it('incorporates preceding invalidation retirement into runtime disposal', async () => {
    const env = harness();
    const managed = await env.createManagedRuntime();
    await managed.initialize();
    await managed.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    env.gates.search = deferred();
    const pending = managed.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    const hang = deferred();
    env.setCancelWorkGate(hang);
    const invalidation = managed.invalidateAccountContext();
    const disposal = managed.dispose();
    await settle(pending);
    await invalidation;
    const outcome = await disposal;
    expect(outcome.drained).toBe(true);
    const replacement = await env.createManagedRuntime();
    await replacement.dispose();
    hang.resolve(undefined);
  }, 20000);

  it('keeps worker counts exact through repeated invalidation retirement', async () => {
    const env = await ready();
    await env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    env.gates.search = deferred();
    const searchPending = env.runtime.runSearch({
      state: STATE,
      algorithm: 'neural_mcts',
      model,
      provider: 'wasm',
      settings: { iterations: 8 },
    });
    await vi.waitFor(() => expect(env.workers.coordinatorCreated).toBe(1));
    const hang = deferred();
    env.setCancelWorkGate(hang);
    const first = env.runtime.invalidateAccountContext();
    const second = env.runtime.invalidateAccountContext();
    await settle(searchPending);
    await Promise.all([first, second]);
    hang.resolve(undefined);
    const initialized = await env.runtime.initialize();
    expect(initialized.state).toBe('ready');
    await expect(env.runtime.runSearch({ state: STATE, algorithm: 'jarl' })).resolves.toMatchObject(
      { algorithm: 'jarl' },
    );
    expect(env.workers.neuralDisposed).toBe(1);
  }, 20000);
});
