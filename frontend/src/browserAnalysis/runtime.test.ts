import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BrowserAnalysisError } from './contracts';
import { runtimeAssetUrl, validateModelManifest } from './manifests';
import { BrowserAnalysisRuntime } from './runtime';
import { ArtifactCache, MemoryArtifactStore } from './storage';
import {
  availabilityWire,
  bytesFor,
  digest,
  MODEL_BYTES,
  modelWire,
  RUNTIME_FILES,
  runtimeManifest,
  streamResponse,
} from './testSupport';

beforeEach(() => {
  // jsdom has no Worker constructor. The runtime only detects the capability; the
  // fake runtime modules below never spawn a real worker.
  vi.stubGlobal('Worker', class {});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const manifest = runtimeManifest();
const model = validateModelManifest(modelWire());

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    body: null,
  } as unknown as Response;
}

function harness(
  options: {
    manifest?: typeof manifest | null;
    tamperAsset?: string;
    fetchStatus?: number;
  } = {},
) {
  const current = options.manifest === undefined ? manifest : options.manifest;
  const assetUrls = new Map<string, Response>();
  if (current) {
    for (const [path, contents] of RUNTIME_FILES) {
      const { size } = bytesFor(contents);
      const body = options.tamperAsset === path ? 'tampered contents' : contents;
      assetUrls.set(
        runtimeAssetUrl(current, path),
        options.tamperAsset === path
          ? streamResponse([body], { contentLength: size })
          : streamResponse([body]),
      );
    }
  }
  const calls: string[] = [];
  const fetchImpl = vi.fn(async (url: string) => {
    calls.push(url);
    if (options.fetchStatus && options.fetchStatus >= 400) {
      return jsonResponse({ detail: 'denied' }, options.fetchStatus);
    }
    if (url === '/api/browser-analysis/availability') {
      return jsonResponse(availabilityWire(current));
    }
    if (url === '/api/browser-analysis/models') {
      return jsonResponse({ schema: 1, models: [modelWire()] });
    }
    if (url === '/api/browser-analysis/models/0123456789abcdef01234567/artifact') {
      const { size } = bytesFor(MODEL_BYTES);
      return streamResponse([MODEL_BYTES], { contentLength: size });
    }
    const asset = assetUrls.get(url);
    if (asset) return asset;
    return jsonResponse({ detail: 'not found' }, 404);
  });
  const loadedModules: string[] = [];
  const jarlCalls: unknown[] = [];
  const neuralSearchCalls: unknown[] = [];
  const loadModule = async (url: string) => {
    const name = url.split('/').at(-1);
    loadedModules.push(String(name));
    if (name === 'analysis-client.js') {
      return {
        LocalAnalysisClient: class {
          async request(payload: { action: string }) {
            jarlCalls.push(payload);
            if (payload.action === 'info') {
              return { ok: true, info: { python: '3.12', engine_identity: { sha256: 'engine' } } };
            }
            return {
              ok: true,
              event: { schema: 1, analysis: { best_move: 'a1-a2' }, position_key: 'key' },
            };
          }
          cancel() {}
          dispose() {}
        },
      };
    }
    if (name === 'neural-client.js') {
      return {
        NeuralClient: class {
          identity: Record<string, unknown> | null = null;
          async load(_url: string, options: Record<string, unknown>) {
            this.identity = {
              sha256: options.expectedSha256,
              ruleset: options.ruleset,
              size: options.size,
              provider: options.provider,
            };
            return this.identity;
          }
          modelIdentity() {
            if (!this.identity) throw new Error('not loaded');
            return this.identity;
          }
          dispose() {}
        },
      };
    }
    if (name === 'search-client.js') {
      return {
        NeuralSearchCoordinator: class {
          constructor(private readonly options: { neural: unknown }) {}
          async search(request: unknown) {
            void this.options.neural;
            neuralSearchCalls.push(request);
            return { analysis: { best_move: 'c3-c4' }, provider: { placement: 'wasm' } };
          }
          abort() {}
          dispose() {}
        },
      };
    }
    throw new Error(`unexpected module ${url}`);
  };
  const states: string[] = [];
  const cache = new ArtifactCache(new MemoryArtifactStore());
  const runtime = new BrowserAnalysisRuntime({
    fetchImpl: fetchImpl as unknown as typeof fetch,
    digestFn: digest,
    loadModule,
    cache,
    onStateChange: (status) => states.push(status.state),
  });
  return { runtime, fetchImpl, calls, loadedModules, jarlCalls, neuralSearchCalls, states, cache };
}

async function ready(options: Parameters<typeof harness>[0] = {}) {
  const env = harness(options);
  const status = await env.runtime.initialize();
  expect(status.state).toBe('ready');
  return env;
}

const request = { state: { ruleset: 'brandubh', size: 7, to_move: 'defenders' } };

describe('browser analysis runtime', () => {
  it('stays disabled when the server flag is off and loads nothing', async () => {
    const { runtime, fetchImpl, loadedModules } = harness({ manifest: null });
    fetchImpl.mockImplementation(async (url: string) => {
      if (url === '/api/browser-analysis/availability') {
        return jsonResponse(availabilityWire(null, { enabled: false, reason: 'disabled' }));
      }
      return jsonResponse({}, 404);
    });
    const status = await runtime.initialize();
    expect(status.state).toBe('disabled');
    expect(loadedModules).toHaveLength(0);
    expect(runtime.getLastResult()).toBeNull();
  });

  it('reports an enabled deployment without a verified runtime as unavailable', async () => {
    const { runtime, loadedModules } = harness({ manifest: null });
    const status = await runtime.initialize();
    expect(status.state).toBe('unavailable');
    expect(status.error?.code).toBe('runtime-missing');
    expect(loadedModules).toHaveLength(0);
  });

  it('verifies every runtime asset, imports the client lazily and probes the engine', async () => {
    const env = await ready();
    expect(env.states).toEqual(['downloading', 'initializing', 'ready']);
    expect(env.loadedModules).toEqual(['analysis-client.js']);
    expect(env.calls.filter((url) => url.startsWith(manifest.basePath))).toHaveLength(
      RUNTIME_FILES.length,
    );
    expect(env.runtime.getStatus().engine).not.toBeNull();
    // Jarl initialization must not touch ONNX/neural assets.
    expect(env.loadedModules).not.toContain('neural-client.js');
    expect(env.calls).not.toContain(
      '/api/browser-analysis/models/0123456789abcdef01234567/artifact',
    );
  });

  it('runs Jarl without a model and never claims an authoritative result', async () => {
    const env = await ready();
    const result = await env.runtime.runSearch(request);
    expect(result.model).toBeNull();
    expect(result.authoritative).toBe(false);
    expect(result.analysis).toEqual({ best_move: 'a1-a2' });
    expect(env.loadedModules).not.toContain('neural-client.js');
    expect(env.runtime.getStatus().state).toBe('ready');
  });

  it('runs neural MCTS only with a published, compatible model', async () => {
    const env = await ready();
    const result = await env.runtime.runSearch({ ...request, model, provider: 'wasm' });
    expect(result.model).toEqual({ sha256: model.onnxSha256, ruleset: 'brandubh', size: 7 });
    expect(result.analysis).toEqual({ best_move: 'c3-c4' });
    expect(result.provider).toEqual({
      requested: 'wasm',
      placement: 'wasm',
      gpuExecutionVerified: false,
    });
    expect(env.neuralSearchCalls).toHaveLength(1);
    expect(env.loadedModules).toContain('neural-client.js');
    expect(env.loadedModules).toContain('search-client.js');
  });

  it('reports a missing WebGPU capability instead of silently falling back', async () => {
    const env = await ready();
    await expect(
      env.runtime.runSearch({ ...request, model, provider: 'webgpu' }),
    ).rejects.toMatchObject({ code: 'capability-missing' });
  });

  it('rejects a tampered runtime asset before initialization completes', async () => {
    const { runtime, loadedModules } = harness({ tamperAsset: 'runtime/pyodide.asm.wasm' });
    await expect(runtime.initialize()).rejects.toMatchObject({ code: 'size-mismatch' });
    expect(runtime.getStatus().state).toBe('error');
    expect(loadedModules).toHaveLength(0);
  });

  it('surfaces authorization failures as structured errors', async () => {
    const { runtime } = harness({ fetchStatus: 401 });
    await expect(runtime.initialize()).rejects.toMatchObject({ code: 'unauthorized' });
  });

  it('downloads and caches published models with bounded storage', async () => {
    const env = await ready();
    const progress: number[] = [];
    await env.runtime.downloadModel(model, {
      onProgress: (event) => progress.push(event.transferredBytes),
    });
    expect(progress.at(-1)).toBe(model.artifactBytes);
    expect(await env.runtime.storageUsage()).toMatchObject({
      persistent: false,
      bytes: model.artifactBytes,
    });
    const before = env.calls.length;
    await env.runtime.downloadModel(model);
    expect(env.calls.length).toBe(before);
  });

  it('rejects an incompatible model before loading it', async () => {
    const env = await ready();
    const incompatible = { ...model, encoderVersion: 'two-plane-v0' } as typeof model;
    await expect(env.runtime.downloadModel(incompatible)).rejects.toBeInstanceOf(
      BrowserAnalysisError,
    );
    await expect(env.runtime.runSearch({ ...request, model: incompatible })).rejects.toMatchObject({
      code: 'runtime-incompatible',
    });
  });

  it('invalidates searches, downloads and results on account changes', async () => {
    const env = await ready();
    await env.runtime.runSearch(request);
    expect(env.runtime.getLastResult()).not.toBeNull();
    await env.runtime.invalidateAccountContext();
    expect(env.runtime.getLastResult()).toBeNull();
    expect(env.runtime.getStatus().state).toBe('idle');
    await expect(env.runtime.runSearch(request)).rejects.toMatchObject({
      code: 'not-initialized',
    });
  });

  it('disposes workers and refuses further work', async () => {
    const env = await ready();
    await env.runtime.dispose();
    expect(env.runtime.getStatus().state).toBe('idle');
    await expect(env.runtime.runSearch(request)).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('never exposes a stale result after cancellation', async () => {
    const env = await ready();
    const pending = env.runtime.runSearch(request);
    env.runtime.cancel();
    await expect(pending).rejects.toMatchObject({ code: 'cancelled' });
    expect(env.runtime.getLastResult()).toBeNull();
  });
});
