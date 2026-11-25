/**
 * Fail-closed validation of runtime and published-model manifests.
 *
 * The server is trusted to serve the application, but every manifest is still
 * validated for schema, identity shape, sizes, digests, path safety and runtime
 * compatibility. Unknown fields never silently change behavior, and no asset URL
 * is constructed from an unvalidated string.
 */
import {
  BrowserAnalysisError,
  ENCODER_VERSION,
  LIMITS,
  MODEL_MANIFEST_SCHEMA,
  ONNX_RUNTIME_WEB_VERSION,
  REQUIRED_RUNTIME_FILES,
  RUNTIME_MANIFEST_SCHEMA,
  SEARCH_PROTOCOL,
  type Availability,
  type ModelManifest,
  type RuntimeFile,
  type RuntimeManifest,
} from './contracts';

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
  if (pins.encoder_version !== ENCODER_VERSION) {
    fail('runtime-incompatible', 'Runtime encoder version is not supported');
  }
  if (pins.onnxruntime_web !== ONNX_RUNTIME_WEB_VERSION) {
    fail('runtime-incompatible', 'Runtime ORT Web version is not the pinned version');
  }
  if (pins.search_protocol !== SEARCH_PROTOCOL || pins.bridge_protocol !== SEARCH_PROTOCOL) {
    fail('runtime-incompatible', 'Runtime worker protocol is not supported');
  }
  if (typeof pins.application_version !== 'string' || pins.application_version.length === 0) {
    fail('runtime-incompatible', 'Runtime application version is missing');
  }
  const files = raw.files;
  if (!Array.isArray(files) || files.length === 0 || files.length > 4096) {
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
    const bytes = positiveInt(entry.bytes, LIMITS.maxRuntimeBytes);
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
  for (const required of REQUIRED_RUNTIME_FILES) {
    if (!seen.has(required)) fail('runtime-integrity', `Runtime asset is missing: ${required}`);
  }
  return {
    schema: RUNTIME_MANIFEST_SCHEMA,
    runtimeId: String(raw.runtime_id),
    pins: {
      pyodide: String(pins.pyodide),
      onnxruntimeWeb: String(pins.onnxruntime_web),
      encoderVersion: String(pins.encoder_version),
      driverVersion: Number(pins.driver_version),
      searchProtocol: Number(pins.search_protocol),
      bridgeProtocol: Number(pins.bridge_protocol),
      applicationVersion: pins.application_version,
    },
    files: validated,
    totalBytes: total,
    basePath: `/browser-analysis/runtime/${String(raw.runtime_id)}`,
  };
}

export function validateAvailability(raw: unknown): Availability {
  if (!isRecord(raw)) fail('runtime-incompatible', 'Availability response is invalid');
  if (raw.schema !== 1) fail('runtime-incompatible', 'Unsupported availability schema');
  if (typeof raw.enabled !== 'boolean' || typeof raw.runtime_available !== 'boolean') {
    fail('runtime-incompatible', 'Availability response is incomplete');
  }
  const runtime = raw.runtime_available ? validateRuntimeManifest(raw.runtime) : null;
  const count = positiveInt(raw.published_models, 10_000);
  return {
    schema: 1,
    enabled: raw.enabled,
    runtimeAvailable: raw.runtime_available,
    runtime,
    reason: typeof raw.reason === 'string' ? raw.reason : null,
    publishedModels: count ?? 0,
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
