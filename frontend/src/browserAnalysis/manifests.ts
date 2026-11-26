/**
 * Fail-closed validation of runtime and published-model manifests.
 *
 * The server is trusted to serve the application, but every manifest is still
 * validated for schema, identity shape, sizes, digests, path safety and runtime
 * compatibility. Unknown fields never silently change behavior, and no asset URL
 * is constructed from an unvalidated string.
 */
import {
  ARCHITECTURE,
  BRIDGE_PROTOCOL,
  BrowserAnalysisError,
  DRIVER_VERSION,
  ENCODER_VERSION,
  LIMITS,
  MAX_RUNTIME_FILES,
  MODEL_MANIFEST_SCHEMA,
  ONNX_RUNTIME_WEB_VERSION,
  PYODIDE_VERSION,
  REQUIRED_RUNTIME_FILES,
  RUNTIME_MANIFEST_SCHEMA,
  SEARCH_ALGORITHM,
  SEARCH_PROTOCOL,
  type Availability,
  type ModelManifest,
  type RuntimeFile,
  type RuntimeManifest,
  type RuntimePins,
} from './contracts';
import { sha256Hex, type DigestFn } from './storage';
import { normalizeExecutionPolicy } from '../analysisPolicy';

const HEX16 = /^[0-9a-f]{16}$/;
const HEX24 = /^[0-9a-f]{24}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const RULESETS = new Set(['brandubh', 'tablut', 'copenhagen']);
const BOARD_SIZES: Record<string, number> = { brandubh: 7, tablut: 9, copenhagen: 11 };
const SAFE_SEGMENT = /^[A-Za-z0-9._-]+$/;

function fail(
  code: 'runtime-incompatible' | 'runtime-integrity' | 'model-integrity',
  message: string,
): never {
  throw new BrowserAnalysisError(code, message, false);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function positiveInt(value: unknown, maximum: number): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= maximum
    ? value
    : null;
}

/** Reject absolute paths, traversal, backslashes and encoded separators. */
export function safeRelativePath(path: unknown): string {
  if (typeof path !== 'string' || path.length === 0 || path.length > 512) {
    fail('runtime-integrity', 'Runtime asset path is invalid');
  }
  if (path.startsWith('/') || path.includes('\\') || path.includes('\0') || path.includes('%')) {
    fail('runtime-integrity', 'Runtime asset path must be a plain relative path');
  }
  const segments = path.split('/');
  if (
    segments.some((segment) => !SAFE_SEGMENT.test(segment) || segment === '.' || segment === '..')
  ) {
    fail('runtime-integrity', 'Runtime asset path contains an unsafe segment');
  }
  return path;
}

export function validateRuntimeManifest(raw: unknown): RuntimeManifest {
  if (!isRecord(raw)) fail('runtime-incompatible', 'Runtime manifest must be an object');
  if (raw.schema !== RUNTIME_MANIFEST_SCHEMA) {
    fail('runtime-incompatible', `Unsupported runtime manifest schema: ${String(raw.schema)}`);
  }
  if (!HEX16.test(String(raw.runtime_id))) {
    fail('runtime-incompatible', 'Runtime identity is invalid');
  }
  const pins = raw.pins;
  if (!isRecord(pins)) fail('runtime-incompatible', 'Runtime pins are missing');
  const expectedPins: Array<[string, unknown, string]> = [
    ['pyodide', PYODIDE_VERSION, 'Pyodide version'],
    ['onnxruntime_web', ONNX_RUNTIME_WEB_VERSION, 'ONNX Runtime Web version'],
    ['encoder_version', ENCODER_VERSION, 'encoder version'],
    ['driver_version', DRIVER_VERSION, 'driver version'],
    ['search_protocol', SEARCH_PROTOCOL, 'search protocol'],
    ['bridge_protocol', BRIDGE_PROTOCOL, 'bridge protocol'],
    ['search_algorithm', SEARCH_ALGORITHM, 'search algorithm'],
    ['architecture', ARCHITECTURE, 'architecture'],
  ];
  for (const [key, expected, label] of expectedPins) {
    if (pins[key] !== expected) {
      fail('runtime-incompatible', `Runtime ${label} is not supported`);
    }
  }
  if (
    typeof pins.application_version !== 'string' ||
    pins.application_version.length === 0 ||
    pins.application_version.length > 100
  ) {
    fail('runtime-incompatible', 'Runtime application version is missing');
  }
  const files = raw.files;
  if (!Array.isArray(files) || files.length === 0 || files.length > MAX_RUNTIME_FILES) {
    fail('runtime-integrity', 'Runtime manifest file inventory is invalid');
  }
  const seen = new Set<string>();
  let total = 0;
  const validated: RuntimeFile[] = [];
  for (const entry of files) {
    if (!isRecord(entry)) fail('runtime-integrity', 'Runtime inventory entry is invalid');
    const path = safeRelativePath(entry.path);
    if (seen.has(path)) fail('runtime-integrity', 'Runtime inventory contains duplicate paths');
    seen.add(path);
    const bytes = positiveInt(entry.bytes, Math.min(LIMITS.maxRuntimeBytes, 64 * 1024 * 1024));
    if (bytes === null) fail('runtime-integrity', `Invalid asset size: ${path}`);
    if (typeof entry.sha256 !== 'string' || !HEX64.test(entry.sha256)) {
      fail('runtime-integrity', `Invalid asset digest: ${path}`);
    }
    total += bytes;
    validated.push({ path, bytes, sha256: entry.sha256 });
  }
  if (total > LIMITS.maxRuntimeBytes) {
    fail('runtime-integrity', 'Runtime exceeds the configured size budget');
  }
  if (raw.total_bytes !== undefined && raw.total_bytes !== total) {
    fail('runtime-integrity', 'Declared runtime total size does not match its inventory');
  }
  for (const required of REQUIRED_RUNTIME_FILES) {
    if (!seen.has(required)) fail('runtime-integrity', `Runtime asset is missing: ${required}`);
  }
  const validatedPins: RuntimePins = {
    pyodide: String(pins.pyodide),
    onnxruntimeWeb: String(pins.onnxruntime_web),
    encoderVersion: String(pins.encoder_version),
    driverVersion: Number(pins.driver_version),
    searchProtocol: Number(pins.search_protocol),
    searchAlgorithm: String(pins.search_algorithm),
    bridgeProtocol: Number(pins.bridge_protocol),
    architecture: String(pins.architecture),
    applicationVersion: pins.application_version,
  };
  return {
    schema: RUNTIME_MANIFEST_SCHEMA,
    runtimeId: String(raw.runtime_id),
    pins: validatedPins,
    files: validated,
    totalBytes: total,
    basePath: `/browser-analysis/runtime/${String(raw.runtime_id)}`,
  };
}

/** Deterministic canonical JSON matching the Python manifest identity encoder. */
export function canonicalJson(value: unknown): string {
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

/** Content-derived runtime identity from canonical immutable content. */
export async function computeRuntimeId(
  manifest: RuntimeManifest,
  digestFn: DigestFn = sha256Hex,
): Promise<string> {
  const files = [...manifest.files]
    .map((entry) => [entry.path, entry.sha256] as [string, string])
    .sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0));
  const identity = canonicalJson({
    files,
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
  });
  const bytes = new TextEncoder().encode(identity);
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  return (await digestFn(buffer as ArrayBuffer)).slice(0, 16);
}

/** Reject a manifest whose identity does not match its canonical content. */
export async function verifyRuntimeIdentity(
  manifest: RuntimeManifest,
  digestFn: DigestFn = sha256Hex,
): Promise<void> {
  const expected = await computeRuntimeId(manifest, digestFn);
  if (expected !== manifest.runtimeId) {
    fail('runtime-integrity', 'Runtime identity does not match its immutable content');
  }
}

export function validateAvailability(raw: unknown): Availability {
  if (!isRecord(raw)) fail('runtime-incompatible', 'Availability response is invalid');
  if (raw.schema !== 1) fail('runtime-incompatible', 'Unsupported availability schema');
  if (typeof raw.enabled !== 'boolean' || typeof raw.runtime_available !== 'boolean') {
    fail('runtime-incompatible', 'Availability response is incomplete');
  }
  const runtime = raw.runtime_available ? validateRuntimeManifest(raw.runtime) : null;
  const count = positiveInt(raw.published_models, 10_000);
  const execution = normalizeExecutionPolicy(raw.execution, {
    browserDelivery: raw.enabled === true,
  });
  const mode = typeof raw.analysis_mode === 'string' ? raw.analysis_mode : execution.mode;
  if (mode !== execution.mode) {
    fail('runtime-incompatible', 'Analysis mode contradicts the execution policy');
  }
  return {
    schema: 1,
    enabled: raw.enabled,
    runtimeAvailable: raw.runtime_available,
    runtime,
    reason: typeof raw.reason === 'string' ? raw.reason : null,
    publishedModels: count ?? 0,
    analysisMode: execution.mode,
    execution,
  };
}

export function validateModelManifest(raw: unknown): ModelManifest {
  if (!isRecord(raw)) fail('model-integrity', 'Model manifest must be an object');
  if (raw.schema !== MODEL_MANIFEST_SCHEMA) {
    fail('model-integrity', `Unsupported model manifest schema: ${String(raw.schema)}`);
  }
  const publicationId = String(raw.publication_id);
  if (!HEX24.test(publicationId)) fail('model-integrity', 'Publication identity is invalid');
  const ruleset = String(raw.ruleset);
  if (!RULESETS.has(ruleset)) fail('model-integrity', 'Model ruleset is not supported');
  if (raw.board_size !== BOARD_SIZES[ruleset]) {
    fail('model-integrity', 'Model board size does not match its ruleset');
  }
  if (raw.encoder_version !== ENCODER_VERSION) {
    fail('model-integrity', 'Model encoder version is not supported');
  }
  const sha = String(raw.onnx_sha256 ?? raw.artifact_version);
  if (!HEX64.test(sha) || raw.artifact_version !== sha) {
    fail('model-integrity', 'Model artifact digest is invalid');
  }
  const bytes = positiveInt(raw.artifact_bytes, LIMITS.maxModelBytes);
  if (bytes === null || bytes === 0) fail('model-integrity', 'Model artifact size is invalid');
  const architecture = raw.architecture;
  if (!isRecord(architecture)) fail('model-integrity', 'Model architecture is missing');
  const channels = positiveInt(architecture.channels, 128);
  const blocks = positiveInt(architecture.blocks, 8);
  if (channels === null || blocks === null || channels < 4) {
    fail('model-integrity', 'Model architecture shape is invalid');
  }
  const runtime = raw.runtime;
  if (!isRecord(runtime)) fail('model-integrity', 'Model runtime compatibility is missing');
  if (runtime.encoder_version !== ENCODER_VERSION) {
    fail('model-integrity', 'Model runtime encoder compatibility does not match');
  }
  if (runtime.ort_version !== ONNX_RUNTIME_WEB_VERSION) {
    fail('model-integrity', 'Model requires another ONNX Runtime Web version');
  }
  if (runtime.search_protocol !== SEARCH_PROTOCOL || runtime.bridge_protocol !== SEARCH_PROTOCOL) {
    fail('model-integrity', 'Model runtime protocol compatibility does not match');
  }
  if (raw.status !== 'published') fail('model-integrity', 'Model is not published');
  const displayName = typeof raw.display_name === 'string' ? raw.display_name.trim() : '';
  if (displayName.length === 0 || displayName.length > 100) {
    fail('model-integrity', 'Model display name is invalid');
  }
  return {
    schema: MODEL_MANIFEST_SCHEMA,
    publicationId,
    artifactVersion: sha,
    displayName,
    ruleset,
    boardSize: BOARD_SIZES[ruleset],
    architecture: { name: String(architecture.name), channels, blocks },
    encoderVersion: ENCODER_VERSION,
    sourceWeightsSha256: String(raw.source_weights_sha256),
    onnxSha256: sha,
    artifactBytes: bytes,
    runtime: {
      runtimeManifestSchema: Number(runtime.runtime_manifest_schema),
      ortVersion: String(runtime.ort_version),
      minOpset: Number(runtime.min_opset),
      maxOpset: Number(runtime.max_opset),
      encoderVersion: String(runtime.encoder_version),
      searchProtocol: Number(runtime.search_protocol),
      bridgeProtocol: Number(runtime.bridge_protocol),
    },
    io: isRecord(raw.io)
      ? { input: raw.io.input, outputs: raw.io.outputs }
      : { input: null, outputs: null },
    status: 'published',
    publishedAt: String(raw.published_at ?? ''),
    registeredModelIds: Array.isArray(raw.registered_model_ids)
      ? raw.registered_model_ids.filter(
          (entry): entry is string => typeof entry === 'string' && entry.length > 0,
        )
      : [],
  };
}

export function validateModelCatalog(raw: unknown): ModelManifest[] {
  if (!isRecord(raw) || raw.schema !== 1 || !Array.isArray(raw.models)) {
    fail('model-integrity', 'Model catalog response is invalid');
  }
  return raw.models.map(validateModelManifest);
}

export function runtimeAssetUrl(manifest: RuntimeManifest, path: string): string {
  const safe = safeRelativePath(path);
  if (!manifest.files.some((entry) => entry.path === safe)) {
    fail('runtime-integrity', `Asset is not part of runtime ${manifest.runtimeId}`);
  }
  return `${manifest.basePath}/${safe.split('/').map(encodeURIComponent).join('/')}`;
}

export function modelArtifactUrl(manifest: ModelManifest): string {
  if (!HEX24.test(manifest.publicationId)) {
    fail('model-integrity', 'Publication identity is invalid');
  }
  return `/api/browser-analysis/models/${manifest.publicationId}/artifact`;
}

export function bytesForRuntimeAsset(manifest: RuntimeManifest, path: string): RuntimeFile {
  const entry = manifest.files.find((candidate) => candidate.path === path);
  if (!entry) fail('runtime-integrity', `Asset is not part of runtime ${manifest.runtimeId}`);
  return entry;
}

/** Compatibility of a published model with the verified runtime. */
export function assertModelRuntimeCompatible(model: ModelManifest, runtime: RuntimeManifest): void {
  if (model.runtime.runtimeManifestSchema !== runtime.schema) {
    fail('runtime-incompatible', 'Model requires another runtime manifest schema');
  }
  if (
    model.encoderVersion !== runtime.pins.encoderVersion ||
    model.runtime.encoderVersion !== runtime.pins.encoderVersion
  ) {
    fail('runtime-incompatible', 'Model encoder does not match the runtime encoder');
  }
  if (model.runtime.ortVersion !== runtime.pins.onnxruntimeWeb) {
    fail('runtime-incompatible', 'Model ORT requirement does not match the runtime');
  }
  if (
    model.runtime.searchProtocol !== runtime.pins.searchProtocol ||
    model.runtime.bridgeProtocol !== runtime.pins.bridgeProtocol
  ) {
    fail('runtime-incompatible', 'Model worker protocol does not match the runtime');
  }
  if (
    model.runtime.runtimeManifestSchema !== RUNTIME_MANIFEST_SCHEMA ||
    !Number.isInteger(model.runtime.minOpset) ||
    !Number.isInteger(model.runtime.maxOpset) ||
    model.runtime.minOpset < 13 ||
    model.runtime.maxOpset > 21 ||
    model.runtime.minOpset > model.runtime.maxOpset ||
    model.architecture.name !== ARCHITECTURE
  ) {
    fail('runtime-incompatible', 'Model runtime compatibility metadata is invalid');
  }
}

/** Structured, retryable classification for transport failures. */
export function classifyFetchFailure(status: number): BrowserAnalysisError {
  if (status === 401 || status === 403) {
    return new BrowserAnalysisError('unauthorized', 'Sign in to load browser analysis assets');
  }
  if (status === 404) {
    return new BrowserAnalysisError('runtime-missing', 'The requested asset is unavailable', true);
  }
  return new BrowserAnalysisError('download-failed', `Asset request failed (HTTP ${status})`, true);
}
