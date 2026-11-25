/** Shared deterministic fixtures for browser-analysis unit tests. */
import { createHash } from 'node:crypto';

import type { Availability, ModelManifest, RuntimeFile, RuntimeManifest } from './contracts';

/** Fast deterministic 64-hex digest for tests (production uses SHA-256). */
export function testDigest(bytes: ArrayBuffer): string {
  const view = new Uint8Array(bytes);
  let hash = 0x811c9dc5;
  for (const byte of view) {
    hash ^= byte;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  const base = hash.toString(16).padStart(8, '0');
  return base.repeat(8);
}

/** Real SHA-256 for artifact fixtures and identity checks (test-only Node crypto). */
export const nodeDigest = async (bytes: ArrayBuffer): Promise<string> =>
  createHash('sha256').update(new Uint8Array(bytes)).digest('hex');

export const digest: (bytes: ArrayBuffer) => Promise<string> = nodeDigest;

export function bytesFor(contents: string): { bytes: ArrayBuffer; sha256: string; size: number } {
  const encoded = new TextEncoder().encode(contents);
  const buffer = encoded.buffer.slice(
    encoded.byteOffset,
    encoded.byteOffset + encoded.byteLength,
  ) as ArrayBuffer;
  return {
    bytes: buffer,
    sha256: createHash('sha256').update(encoded).digest('hex'),
    size: buffer.byteLength,
  };
}

export const RUNTIME_FILES: Array<[string, string]> = [
  ['runtime/pyodide.js', 'pyodide-js'],
  ['runtime/pyodide.asm.js', 'pyodide-asm-js'],
  ['runtime/pyodide.asm.wasm', 'pyodide-wasm'],
  ['runtime/python_stdlib.zip', 'stdlib'],
  ['runtime/pyodide-lock.json', '{}'],
  ['ort/ort.min.js', 'ort-min'],
  ['ort/ort-wasm-simd-threaded.mjs', 'ort-mjs'],
  ['ort/ort-wasm-simd-threaded.wasm', 'ort-wasm'],
  ['ort/ort-wasm-simd-threaded.jsep.mjs', 'jsep-mjs'],
  ['ort/ort-wasm-simd-threaded.jsep.wasm', 'jsep-wasm'],
  ['python/hnefatafl/engine.py', '# engine'],
  ['python/hnefatafl/encoding.py', '# encoding'],
  ['python/hnefatafl/mcts_driver.py', '# driver'],
  ['bridge.py', '# bridge'],
  ['search_bridge.py', '# search bridge'],
  ['assets.json', '{}'],
  ['search-assets.json', '{}'],
  ['worker.js', '// worker'],
  ['analysis-client.js', 'export class LocalAnalysisClient {}'],
  ['search-worker.js', '// search worker'],
  ['search-client.js', 'export class NeuralSearchCoordinator {}'],
  ['neural-client.js', 'export class NeuralClient {}'],
  ['neural-worker.js', '// neural worker'],
];

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

export function runtimeManifest(overrides: Partial<RuntimeManifest> = {}): RuntimeManifest {
  const files: RuntimeFile[] = RUNTIME_FILES.map(([path, contents]) => {
    const { sha256, size } = bytesFor(contents);
    return { path, bytes: size, sha256 };
  });
  const base = {
    schema: 1,
    runtimeId: '',
    pins: {
      pyodide: '0.27.7',
      onnxruntimeWeb: '1.22.0',
      encoderVersion: 'six-plane-v1',
      driverVersion: 1,
      searchProtocol: 1,
      searchAlgorithm: 'neural-mcts-puct-v1',
      bridgeProtocol: 1,
      architecture: 'tafl-policy-value-v1',
      applicationVersion: '0.41.1',
    },
    files,
    totalBytes: files.reduce((sum, entry) => sum + entry.bytes, 0),
    basePath: '',
  } as RuntimeManifest;
  const identity = canonicalJson({
    files: [...files]
      .map((entry) => [entry.path, entry.sha256])
      .sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0)),
    pins: {
      pyodide: base.pins.pyodide,
      onnxruntime_web: base.pins.onnxruntimeWeb,
      encoder_version: base.pins.encoderVersion,
      driver_version: base.pins.driverVersion,
      search_protocol: base.pins.searchProtocol,
      search_algorithm: base.pins.searchAlgorithm,
      bridge_protocol: base.pins.bridgeProtocol,
      architecture: base.pins.architecture,
      application_version: base.pins.applicationVersion,
    },
  });
  const runtimeId = createHash('sha256').update(identity).digest('hex').slice(0, 16);
  return {
    ...base,
    runtimeId,
    basePath: `/browser-analysis/runtime/${runtimeId}`,
    ...overrides,
  };
}

export function runtimeWire(
  manifest: RuntimeManifest = runtimeManifest(),
): Record<string, unknown> {
  return {
    schema: manifest.schema,
    runtime_id: manifest.runtimeId,
    pins: {
      pyodide: manifest.pins.pyodide,
      onnxruntime_web: manifest.pins.onnxruntimeWeb,
      encoder_version: manifest.pins.encoderVersion,
      driver_version: manifest.pins.driverVersion,
      search_protocol: manifest.pins.searchProtocol,
      search_algorithm: manifest.pins.searchAlgorithm,
      bridge_protocol: manifest.pins.bridgeProtocol,
      architecture: manifest.pins.architecture,
      application_version: manifest.pins.applicationVersion,
    },
    files: manifest.files.map((entry) => ({
      path: entry.path,
      bytes: entry.bytes,
      sha256: entry.sha256,
    })),
    total_bytes: manifest.totalBytes,
  };
}

export function availabilityWire(
  manifest: RuntimeManifest | null = runtimeManifest(),
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema: 1,
    enabled: true,
    runtime_available: manifest !== null,
    runtime: manifest ? runtimeWire(manifest) : null,
    reason: manifest ? null : 'runtime-missing',
    published_models: manifest ? 1 : 0,
    ...overrides,
  };
}

export const MODEL_BYTES = 'published onnx model bytes';

export function modelWire(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const { sha256, size } = bytesFor(MODEL_BYTES);
  return {
    schema: 1,
    publication_id: '0123456789abcdef01234567',
    artifact_version: sha256,
    display_name: 'Brandubh assignment model',
    ruleset: 'brandubh',
    board_size: 7,
    architecture: { name: 'tafl-policy-value-v1', channels: 32, blocks: 2 },
    encoder_version: 'six-plane-v1',
    source_weights_sha256: 'a'.repeat(64),
    onnx_sha256: sha256,
    artifact_bytes: size,
    runtime: {
      runtime_manifest_schema: 1,
      ort_version: '1.22.0',
      min_opset: 13,
      max_opset: 21,
      encoder_version: 'six-plane-v1',
      search_protocol: 1,
      bridge_protocol: 1,
    },
    io: { input: { name: 'planes' }, outputs: {} },
    status: 'published',
    published_at: '2026-11-25T00:00:00+00:00',
    ...overrides,
  };
}

export function modelManifest(overrides: Partial<ModelManifest> = {}): ModelManifest {
  const wire = modelWire();
  const sha = String(wire.onnx_sha256);
  return {
    schema: 1,
    publicationId: String(wire.publication_id),
    artifactVersion: sha,
    displayName: String(wire.display_name),
    ruleset: 'brandubh',
    boardSize: 7,
    architecture: { name: 'tafl-policy-value-v1', channels: 32, blocks: 2 },
    encoderVersion: 'six-plane-v1',
    sourceWeightsSha256: 'a'.repeat(64),
    onnxSha256: sha,
    artifactBytes: Number(wire.artifact_bytes),
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
    status: 'published',
    publishedAt: '2026-11-25T00:00:00+00:00',
    ...overrides,
  };
}

export const AVAILABILITY: Availability = {
  schema: 1,
  enabled: true,
  runtimeAvailable: true,
  runtime: runtimeManifest(),
  reason: null,
  publishedModels: 1,
};

/** Minimal Response-like object for streamed download tests. */
export function streamResponse(
  chunks: string[],
  options: { contentLength?: number | null; status?: number } = {},
): Response {
  const encoded = chunks.map((chunk) => new TextEncoder().encode(chunk));
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of encoded) controller.enqueue(chunk);
      controller.close();
    },
  });
  const headers = new Headers();
  if (options.contentLength !== null) {
    const total = encoded.reduce((sum, chunk) => sum + chunk.byteLength, 0);
    headers.set('content-length', String(options.contentLength ?? total));
  }
  return {
    ok: (options.status ?? 200) < 400,
    status: options.status ?? 200,
    headers,
    body: stream,
    arrayBuffer: async () => new ArrayBuffer(0),
  } as unknown as Response;
}
