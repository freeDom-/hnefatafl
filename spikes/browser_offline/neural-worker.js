/* Experimental neural inference worker: ORT Web WASM/WebGPU, no production use.
 *
 * Responsibilities:
 *  - load a pinned ORT Web bundle for the requested provider;
 *  - verify the received model bytes against the staged SHA-256 before creating
 *    any inference session;
 *  - own and release inference sessions and tensors explicitly (release is
 *    awaited, tensors are disposed in finally blocks, failed sessions are
 *    released);
 *  - validate every returned tensor before publishing a result: batch count,
 *    shapes, dtypes, finite logits, finite in-range values, legal indices;
 *  - run single-position and genuine distinct-position batched inference;
 *  - report the provider that was requested, accepted and observed, and never
 *    present a WASM/CPU fallback as GPU acceleration.
 *
 * Provider limitation: the pinned ORT Web version exposes no stable per-node
 * execution-placement API. A session created for WebGPU is reported as
 * "webgpu-session-unverified": the session accepted the GPU provider, but
 * per-operator GPU execution is NOT claimed.
 */
const MAX_BATCH = 32;
const INPUT_PLANES = 6;
const MAX_MODEL_BYTES = 64 * 1024 * 1024;
const SIZE_RULESETS = { brandubh: 7, tablut: 9, copenhagen: 11 };

importScripts("./neural-tensors.js");
importScripts("./neural-identity.js");

let ortLib = null;
let ortBundle = null;
let session = null;
// Test hooks are supplied by a separate worker entry point. This normal worker
// never imports the test adapter, so the capability is absent rather than gated
// by a JavaScript/client flag.
let model = null;

function named(name, message) {
  return Object.assign(new Error(message), { code: name });
}

function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

function percentile(values, fraction) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  if (ordered.length === 1) return ordered[0];
  const position = fraction * (ordered.length - 1);
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, ordered.length - 1);
  return ordered[lower] * (1 - (position - lower)) + ordered[upper] * (position - lower);
}

function memorySnapshot() {
  const heap = self.performance?.memory;
  return {
    // JavaScript heap hints. These are NOT WASM capacity or process RSS.
    js_heap_size_limit_bytes: heap?.jsHeapSizeLimit ?? null,
    js_heap_used_bytes: heap?.usedJSHeapSize ?? null,
    // The pinned ORT Web build does not expose its WASM heap size.
    wasm_heap_capacity_bytes: null,
    wasm_heap_capacity_note: "not exposed by the pinned ORT Web build",
  };
}

async function ensureOrt(provider) {
  const wanted = provider === "webgpu" ? "webgpu" : "wasm";
  if (ortLib) {
    if (ortBundle !== wanted)
      throw named(
        "PROVIDER_SWITCH_REQUIRES_RECREATE",
        "This worker loaded a different ORT bundle; recreate the worker to switch providers",
      );
    return ortLib;
  }
  importScripts("./neural-provider.js");
  importScripts(`./ort/${provider === "webgpu" ? "ort.webgpu.min.js" : "ort.min.js"}`);
  ortLib = self.ort;
  ortBundle = wanted;
  if (!ortLib) throw new Error("ORT Web did not expose a global");
  ortLib.env.wasm.wasmPaths = new URL("./ort/", self.location.href).href;
  ortLib.env.wasm.numThreads = 1; // no SharedArrayBuffer / COOP+COEP required
  ortLib.env.wasm.simd = true;
  ortLib.env.logLevel = "error";
  return ortLib;
}

async function capabilities() {
  const gpu = typeof navigator !== "undefined" ? navigator.gpu : undefined;
  let adapter = null;
  let adapterError = null;
  if (gpu) {
    try {
      adapter = await Promise.race([
        gpu.requestAdapter(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("WebGPU adapter probe timed out")), 2000),
        ),
      ]);
    } catch (error) {
      adapterError = String(error);
    }
  }
  let adapterInfo = null;
  if (adapter && typeof adapter.info === "object") {
    adapterInfo = {
      vendor: adapter.info.vendor ?? null,
      architecture: adapter.info.architecture ?? null,
      device: adapter.info.device ?? null,
      description: adapter.info.description ?? null,
    };
  }
  return {
    crossOriginIsolated: self.crossOriginIsolated === true,
    sharedArrayBuffer: typeof SharedArrayBuffer !== "undefined",
    webAssembly: typeof WebAssembly !== "undefined",
    webGPUAPI: !!gpu,
    webGPUAdapter: !!adapter,
    webGPUAdapterError: adapterError,
    adapterInfo,
    hardwareConcurrency: navigator.hardwareConcurrency ?? null,
    deviceMemoryGiBHint: navigator.deviceMemory ?? null,
    memory: memorySnapshot(),
  };
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function releaseSession({ unloadBundle = false } = {}) {
  const current = session;
  session = null;
  let released = false;
  if (current) {
    if (typeof current.release === "function") {
      // Release is asynchronous in ORT Web; awaiting it ensures the previous
      // session's resources are gone before a replacement is created.
      await current.release();
      released = true;
    } else if (typeof current.dispose === "function") {
      current.dispose();
      released = true;
    }
  }
  if (unloadBundle) {
    ortLib = null;
    ortBundle = null;
  }
  return released;
}

function assertFiniteF32(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value))
    throw named("INPUT_INVALID", `${label} contains a non-finite value`);
  if (!Number.isFinite(Math.fround(value)))
    throw named("INPUT_OVERFLOW", `${label} overflows when converted to FP32`);
}

function assertPlanes(planes, size) {
  const expected = INPUT_PLANES * size * size;
  const isArray = Array.isArray(planes);
  const isArrayBufferView = ArrayBuffer.isView(planes) && !(planes instanceof DataView);
  if ((!isArray && !isArrayBufferView) || planes.length !== expected)
    throw named("INPUT_INVALID", `Expected ${expected} plane values, received ${planes?.length ?? "none"}`);
  for (const value of planes) assertFiniteF32(value, "Input planes");
}

function decodePlanesBase64(encoded, size) {
  if (typeof encoded !== "string" || encoded.length === 0 || encoded.length > 64 * 1024)
    throw named("INPUT_INVALID", "planes_base64 must be a bounded non-empty string");
  let binary;
  try {
    binary = atob(encoded);
  } catch {
    throw named("INPUT_INVALID", "planes_base64 is not valid base64");
  }
  const expected = INPUT_PLANES * size * size;
  if (binary.length !== expected)
    throw named("INPUT_INVALID", `Expected ${expected} encoded plane bytes, received ${binary.length}`);
  const planes = new Float32Array(expected);
  for (let index = 0; index < expected; index += 1) {
    const value = binary.charCodeAt(index);
    if (value !== 0 && value !== 1)
      throw named("INPUT_INVALID", "Encoded binary planes may contain only 0 or 1");
    planes[index] = value;
  }
  return planes;
}

function assertLegal(legal, size) {
  const limit = size ** 4;
  if (!Array.isArray(legal) || legal.length === 0)
    throw named("INPUT_INVALID", "Legal action indices are required");
  const seen = new Set();
  for (const index of legal) {
    if (!Number.isInteger(index) || index < 0 || index >= limit)
      throw named("INPUT_INVALID", `Legal action index ${index} is out of range [0, ${limit})`);
    if (seen.has(index)) throw named("INPUT_INVALID", `Duplicate legal action index ${index}`);
    seen.add(index);
  }
  return legal;
}

async function loadModel(request) {
  const { buffer, provider, size, ruleset, expectedSha256 } = request;
  if (!(buffer instanceof ArrayBuffer)) throw named("INPUT_INVALID", "Model buffer must be an ArrayBuffer");
  if (buffer.byteLength === 0 || buffer.byteLength > MAX_MODEL_BYTES)
    throw named("ARTIFACT_SIZE", "Model exceeds the experimental size limit");
  if (!["wasm", "webgpu"].includes(provider))
    throw named("UNSUPPORTED_PROVIDER", `Unsupported provider: ${provider}`);
  if (!Number.isInteger(size) || size < 5 || size > 15) throw named("INPUT_INVALID", "Invalid board size");
  if (typeof ruleset !== "string" || !(ruleset in SIZE_RULESETS))
    throw named("ARTIFACT_RULESET_MISMATCH", "A supported ruleset identity is required");
  if (SIZE_RULESETS[ruleset] !== size)
    throw named("ARTIFACT_RULESET_MISMATCH", `Ruleset ${ruleset} does not match board size ${size}`);
  if (!expectedSha256 || !/^[0-9a-f]{64}$/.test(expectedSha256))
    throw named("ARTIFACT_NO_DIGEST", "A staged model SHA-256 is required");
  const received = await sha256Hex(buffer);
  if (received !== expectedSha256)
    throw named("ARTIFACT_DIGEST_MISMATCH", `Model integrity check failed: ${received} != ${expectedSha256}`);
  if (provider === "webgpu") {
    const probe = await capabilities();
    if (!probe.webGPUAdapter)
      throw named(
        "WEBGPU_NO_ADAPTER",
        `WebGPU requested but no adapter is available: ${probe.webGPUAdapterError ?? "null adapter"}`,
      );
  }
  await ensureOrt(provider);
  try {
    await releaseSession();
  } catch (error) {
    throw named("SESSION_RELEASE_FAILED", `Previous session release failed: ${error?.message ?? error}`);
  }
  let created;
  try {
    created = await ortLib.InferenceSession.create(buffer, {
      executionProviders: [provider],
      graphOptimizationLevel: "all",
    });
  } catch (error) {
    throw named("SESSION_CREATE_FAILED", `Session creation failed: ${error?.message ?? error}`);
  }
  const inputNames = [...created.inputNames];
  const outputNames = [...created.outputNames];
  if (inputNames.length !== 1 || inputNames[0] !== "planes") {
    await releaseQuietly(created);
    throw named("ARTIFACT_BAD_INPUT", `Unexpected input names: ${inputNames}`);
  }
  if (
    outputNames.length !== 2 ||
    !["policy_logits", "defender_value"].every((name) => outputNames.includes(name))
  ) {
    await releaseQuietly(created);
    throw named("ARTIFACT_BAD_OUTPUT", `Unexpected output names: ${outputNames}`);
  }
  session = created;
  const evidence = self.__neuralProvider.providerEvidence(created);
  const placement = self.__neuralProvider.placementFor(provider, evidence);
  model = {
    size,
    ruleset: ruleset ?? null,
    providerRequested: provider,
    providerEvidence: evidence,
    executionPlacement: placement,
    modelSha256: expectedSha256,
    modelBytes: buffer.byteLength,
    inputName: "planes",
  };
  return {
    provider_requested: provider,
    provider_evidence: evidence,
    execution_placement: placement,
    gpu_execution_verified: self.__neuralProvider.gpuVerified(placement),
    model_sha256: expectedSha256,
    model_ruleset: ruleset ?? null,
    model_bytes: buffer.byteLength,
    input_names: inputNames,
    output_names: outputNames,
    memory: memorySnapshot(),
  };
}

async function releaseQuietly(target) {
  try {
    if (typeof target?.release === "function") await target.release();
    else target?.dispose?.();
  } catch {
    // A failed release of a rejected session is not actionable; the worker can
    // still be terminated as the final recovery mechanism.
  }
}

function rejectTestHook(request) {
  if (request && Object.prototype.hasOwnProperty.call(request, "test_delay_ms"))
    throw named("TEST_HOOKS_DISABLED", "test_delay_ms is not accepted by an inference request");
}

async function handleTestAction(request) {
  // This branch exists only for the dedicated test worker, which installs an
  // adapter before/after importing this core. A normal worker has no adapter and
  // therefore no activatable delay capability.
  const adapter = self.__neuralTestAdapter;
  if (!adapter || typeof adapter.handle !== "function")
    throw named("TEST_HOOKS_DISABLED", "Test inference hooks are unavailable");
  return adapter.handle(request);
}

function assertRequestId(value, label = "request_id") {
  if (typeof value !== "string" || !value || value.length > 128)
    throw named("INPUT_INVALID", `${label} must be a non-empty string of at most 128 characters`);
}

function assertPositionDigest(value, label = "position_digest") {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value))
    throw named("INPUT_INVALID", `${label} must be a 64-character lowercase hex digest`);
}

function buildInputTensor(cases, size) {
  const planeCount = INPUT_PLANES * size * size;
  const data = new Float32Array(planeCount * cases.length);
  cases.forEach((entry, batchIndex) => {
    assertPlanes(entry.planes, size);
    assertLegal(entry.legal, size);
    data.set(entry.planes, batchIndex * planeCount);
  });
  return new ortLib.Tensor("float32", data, [cases.length, 6, size, size]);
}

function activeModelIdentity() {
  if (!model) return null;
  return {
    modelSha256: model.modelSha256,
    ruleset: model.ruleset,
    size: model.size,
  };
}

async function runBatch(
  cases,
  size,
  { requireIdentity = true, expectedModel = null, includeLogits = true } = {},
) {
  if (!session || !model) throw named("MODEL_NOT_LOADED", "No model session is loaded");
  // Every workload — inference, batching, benchmarking and sustained runs —
  // must name the exact model it was prepared for. Validate before touching
  // inputs so a delayed request can never execute against another session.
  if (expectedModel === undefined || expectedModel === null)
    throw named("MODEL_IDENTITY_REQUIRED", "An expected model identity is required");
  self.__neuralIdentity.validateModelIdentity(expectedModel, activeModelIdentity());
  if (model.size !== size)
    throw named("ARTIFACT_RULESET_MISMATCH", "Requested board size differs from the model");
  if (!Array.isArray(cases) || cases.length === 0)
    throw named("INPUT_INVALID", "cases must be a non-empty array");
  if (cases.length > MAX_BATCH)
    throw named("INPUT_INVALID", `batch ${cases.length} exceeds the ${MAX_BATCH} limit`);
  if (requireIdentity) {
    const seen = new Set();
    cases.forEach((entry, index) => {
      assertRequestId(entry.request_id);
      assertPositionDigest(entry.position_digest);
      if (seen.has(entry.request_id))
        throw named("INPUT_INVALID", `Duplicate request_id ${entry.request_id}`);
      seen.add(entry.request_id);
      if (entry.index !== undefined && entry.index !== index)
        throw named("INPUT_INVALID", "Request index does not match its array position");
    });
  } else {
    cases = cases.map((entry, index) => ({
      ...entry,
      request_id: entry.request_id ?? `bench-${index}`,
      position_digest: entry.position_digest ?? null,
    }));
  }

  const prepared = performance.now();
  const tensor = buildInputTensor(cases, size);
  const prepareMs = performance.now() - prepared;
  let output = null;
  let failure = null;
  const inputCleanup = [];
  const runStarted = performance.now();
  try {
    output = await session.run({ [model.inputName]: tensor });
  } catch (error) {
    failure = error;
  } finally {
    // Always dispose the input tensor, including on inference failure.
    try {
      tensor.dispose?.();
    } catch (error) {
      inputCleanup.push(String(error?.message ?? error));
    }
  }
  const runMs = performance.now() - runStarted;

  let extracted = null;
  const extractStarted = performance.now();
  if (!failure && output) {
    try {
      extracted = self.__neuralTensors.extractResults(output, cases.length, size, cases, {
        includeLogits,
      });
    } catch (error) {
      failure = error;
    }
  }
  const extractMs = performance.now() - extractStarted;
  // Every output tensor must be released even when validation or extraction
  // throws. Cleanup failures mark the worker unhealthy so the client can reset it.
  const cleanup = output ? self.__neuralTensors.disposeTensors(output) : [];
  const cleanupErrors = [...inputCleanup, ...cleanup];
  if (cleanupErrors.length) {
    const suffix = failure ? ` (after: ${failure?.message ?? failure})` : "";
    throw named(
      "RESOURCE_CLEANUP_FAILED",
      `Tensor cleanup failed: ${cleanupErrors.join("; ")}${suffix}`,
    );
  }
  if (failure) throw failure;
  return {
    ...extracted,
    provider_requested: model.providerRequested,
    execution_placement: model.executionPlacement,
    model_sha256: model.modelSha256,
    model_ruleset: model.ruleset,
    model_bytes: model.modelBytes,
    tensor_prepare_ms: prepareMs,
    run_ms: runMs,
    extract_ms: extractMs,
    batch: cases.length,
    requested_count: cases.length,
    batch_limit: MAX_BATCH,
  };
}

async function inferSingle(request) {
  rejectTestHook(request);
  const { planes, legal, ruleset, size, request_id, position_digest, model: expectedModel } = request;
  if (!model) throw named("MODEL_NOT_LOADED", "No model session is loaded");
  if (expectedModel === undefined || expectedModel === null)
    throw named("MODEL_IDENTITY_REQUIRED", "An expected model identity is required");
  if (size !== model.size)
    throw named("BOARD_SIZE_MISMATCH", `Requested size ${size} does not match the model`);
  if (ruleset !== model.ruleset)
    throw named("RULESET_MISMATCH", `Requested ruleset ${ruleset} does not match the model`);
  const result = await runBatch(
    [{ planes, legal, request_id, position_digest }],
    model.size,
    { expectedModel },
  );
  const single = {
    ...result,
    index: 0,
    request_id: result.results[0].request_id,
    position_digest: result.results[0].position_digest,
    policy_logits: result.results[0].policy_logits,
    value: result.results[0].value,
    legal: result.results[0].legal,
    legal_probabilities: result.results[0].legal_probabilities,
    illegal_mass_raw: result.results[0].illegal_mass_raw,
    inference_ms: result.tensor_prepare_ms + result.run_ms,
  };
  delete single.results;
  return single;
}

async function inferMany(request) {
  rejectTestHook(request);
  const { cases, model: expectedModel } = request;
  if (!model) throw named("MODEL_NOT_LOADED", "No model session is loaded");
  if (expectedModel === undefined || expectedModel === null)
    throw named("MODEL_IDENTITY_REQUIRED", "An expected model identity is required");
  const result = await runBatch(cases, model.size, { expectedModel });
  return {
    ...result,
    per_position_ms: result.run_ms / result.batch,
  };
}

async function inferCompact(request) {
  const {
    planes,
    planes_base64: planesBase64,
    legal,
    ruleset,
    size,
    request_id,
    position_digest,
    model: expectedModel,
  } = request;
  if (!model) throw named("MODEL_NOT_LOADED", "No model session is loaded");
  if (expectedModel === undefined || expectedModel === null)
    throw named("MODEL_IDENTITY_REQUIRED", "An expected model identity is required");
  if (size !== model.size)
    throw named("BOARD_SIZE_MISMATCH", `Requested size ${size} does not match the model`);
  if (ruleset !== model.ruleset)
    throw named("RULESET_MISMATCH", `Requested ruleset ${ruleset} does not match the model`);
  if ((planes === undefined) === (planesBase64 === undefined))
    throw named("INPUT_INVALID", "Provide exactly one of planes or planes_base64");
  const compactPlanes =
    planesBase64 !== undefined
      ? decodePlanesBase64(planesBase64, size)
      : planes;
  const result = await runBatch(
    [{ planes: compactPlanes, legal, request_id, position_digest }],
    model.size,
    { expectedModel, includeLogits: false },
  );
  return {
    ...result,
    index: 0,
    request_id: result.results[0].request_id,
    position_digest: result.results[0].position_digest,
    value: result.results[0].value,
    legal: result.results[0].legal,
    legal_probabilities: result.results[0].legal_probabilities,
    inference_ms: result.tensor_prepare_ms + result.run_ms + result.extract_ms,
    payload_mode: "legal-only",
  };
}

async function benchmark(request) {
  if (!model) throw named("MODEL_NOT_LOADED", "No model session is loaded");
  const batchSizes = request.batchSizes;
  if (!Array.isArray(batchSizes) || !batchSizes.length) throw new Error("batchSizes is required");
  for (const batch of batchSizes) {
    if (!Number.isInteger(batch) || batch < 1 || batch > MAX_BATCH)
      throw named("INPUT_INVALID", `Invalid batch size ${batch}; allowed 1..${MAX_BATCH}`);
  }
  const warmup = Number.isInteger(request.warmup) ? request.warmup : 2;
  const reps = Number.isInteger(request.reps) ? request.reps : 5;
  if (warmup < 0 || warmup > 20) throw named("INPUT_INVALID", "warmup must be 0..20");
  if (reps < 1 || reps > 50) throw named("INPUT_INVALID", "reps must be 1..50");
  const cases = request.cases;
  if (!Array.isArray(cases) || !cases.length)
    throw named("INPUT_INVALID", "distinct benchmark cases are required");
  const expectedModel = request.model ?? null;
  if (expectedModel === null)
    throw named("MODEL_IDENTITY_REQUIRED", "benchmark requires the expected model identity");
  const rows = [];
  for (const batch of batchSizes) {
    const filled = Array.from({ length: batch }, (_, index) => cases[index % cases.length]);
    for (let index = 0; index < warmup; index += 1)
      await runBatch(filled, model.size, { requireIdentity: false, expectedModel });
    const times = [];
    for (let index = 0; index < reps; index += 1) {
      const started = performance.now();
      await runBatch(filled, model.size, { requireIdentity: false, expectedModel });
      times.push(performance.now() - started);
    }
    rows.push({
      batch,
      distinct_positions: Math.min(batch, cases.length),
      warmup,
      reps,
      min_ms: Math.min(...times),
      median_ms: median(times),
      p90_ms: percentile(times, 0.9),
      max_ms: Math.max(...times),
      positions_per_second: batch / (median(times) / 1000),
    });
  }
  return {
    provider_requested: model.providerRequested,
    execution_placement: model.executionPlacement,
    rows,
  };
}

async function sustained(request) {
  if (!model) throw named("MODEL_NOT_LOADED", "No model session is loaded");
  const cases = request.cases;
  const iterations = request.iterations;
  if (!Array.isArray(cases) || !cases.length) throw named("INPUT_INVALID", "cases are required");
  if (!Number.isInteger(iterations) || iterations < 1 || iterations > 10000)
    throw named("INPUT_INVALID", "iterations must be 1..10000");
  const expectedModel = request.model ?? null;
  if (expectedModel === null)
    throw named("MODEL_IDENTITY_REQUIRED", "sustained requires the expected model identity");
  const started = performance.now();
  for (let index = 0; index < iterations; index += 1)
    await runBatch(cases, model.size, { requireIdentity: false, expectedModel });
  const elapsed = performance.now() - started;
  return {
    iterations,
    batch: cases.length,
    elapsed_ms: elapsed,
    positions_per_second: (iterations * cases.length) / (elapsed / 1000),
    execution_placement: model.executionPlacement,
    model_sha256: model.modelSha256,
    memory: memorySnapshot(),
  };
}

async function release(request) {
  const released = await releaseSession({ unloadBundle: request?.unloadBundle === true });
  model = null;
  return { released, memory: memorySnapshot() };
}

self.__neuralWorkerInternals = {
  inferSingle,
  inferCompact,
  runBatch,
};

onmessage = async ({ data: { id, request } }) => {
  try {
    let value;
    if (request.action === "capabilities") value = await capabilities();
    else if (request.action === "load") value = await loadModel(request);
    else if (request.action === "infer") value = await inferSingle(request);
    else if (request.action === "inferCompact") value = await inferCompact(request);
    else if (request.action === "test") value = await handleTestAction(request);
    else if (
      request.action === "configureTestHooks" ||
      request.action === "inferTestonly" ||
      request.action === "inferCompactTestonly"
    )
      throw named("TEST_HOOKS_DISABLED", "Test inference hooks are unavailable");
    else if (request.action === "inferMany") value = await inferMany(request);
    else if (request.action === "benchmark") value = await benchmark(request);
    else if (request.action === "sustained") value = await sustained(request);
    else if (request.action === "release") value = await release(request);
    else throw new Error(`Unknown neural action: ${request.action}`);
    postMessage({ id, value });
  } catch (error) {
    postMessage({ id, error: String(error?.message ?? error), code: error?.code ?? null });
  }
};
