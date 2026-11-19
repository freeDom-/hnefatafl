/* Experimental neural inference worker: ORT Web WASM/WebGPU, no production use.
 *
 * Responsibilities:
 *  - load a pinned ORT Web bundle for the requested provider;
 *  - verify the received model bytes against the staged SHA-256 before creating
 *    any inference session;
 *  - own and release inference sessions and tensors explicitly;
 *  - run single-position and genuine distinct-position batched inference;
 *  - report the provider that was requested, accepted and observed, and never
 *    present a WASM/CPU fallback as GPU acceleration.
 *
 * Provider limitation: ORT Web does not expose a stable per-node execution
 * provider placement API in the pinned version. A session created with the
 * WebGPU provider list is reported as "webgpu-session-unverified": the session
 * accepted the GPU provider, but per-node GPU execution is NOT claimed.
 */
const MAX_BATCH = 32;
const INPUT_PLANES = 6;

let ortLib = null;
let ortBundle = null;
let session = null;
let model = null;

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
      throw Object.assign(
        new Error("This worker loaded a different ORT bundle; recreate the worker to switch providers"),
        { code: "PROVIDER_SWITCH_REQUIRES_RECREATE" },
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

function releaseSession() {
  const released = !!session;
  try {
    session?.release?.();
  } catch {
    // Releasing an already-released session is not an error we need to surface.
  }
  session = null;
  return released;
}

async function loadModel(request) {
  const { buffer, provider, size, ruleset, expectedSha256 } = request;
  if (!(buffer instanceof ArrayBuffer)) throw new Error("Model buffer must be an ArrayBuffer");
  if (buffer.byteLength === 0 || buffer.byteLength > 64 * 1024 * 1024)
    throw Object.assign(new Error("Model exceeds the experimental size limit"), {
      code: "ARTIFACT_SIZE",
    });
  if (!["wasm", "webgpu"].includes(provider))
    throw Object.assign(new Error(`Unsupported provider: ${provider}`), { code: "UNSUPPORTED_PROVIDER" });
  if (!Number.isInteger(size) || size < 5 || size > 15) throw new Error("Invalid board size");
  if (!expectedSha256 || !/^[0-9a-f]{64}$/.test(expectedSha256))
    throw Object.assign(new Error("A staged model SHA-256 is required"), { code: "ARTIFACT_NO_DIGEST" });
  const received = await sha256Hex(buffer);
  if (received !== expectedSha256) {
    throw Object.assign(
      new Error(`Model integrity check failed: ${received} != ${expectedSha256}`),
      { code: "ARTIFACT_DIGEST_MISMATCH" },
    );
  }
  if (provider === "webgpu") {
    const probe = await capabilities();
    if (!probe.webGPUAdapter) {
      throw Object.assign(
        new Error(`WebGPU requested but no adapter is available: ${probe.webGPUAdapterError ?? "null adapter"}`),
        { code: "WEBGPU_NO_ADAPTER" },
      );
    }
  }
  await ensureOrt(provider);
  releaseSession();
  let created;
  try {
    created = await ortLib.InferenceSession.create(buffer, {
      executionProviders: [provider],
      graphOptimizationLevel: "all",
    });
  } catch (error) {
    throw Object.assign(new Error(`Session creation failed: ${error?.message ?? error}`), {
      code: "SESSION_CREATE_FAILED",
    });
  }
  const inputNames = [...created.inputNames];
  const outputNames = [...created.outputNames];
  if (inputNames.length !== 1 || inputNames[0] !== "planes")
    throw Object.assign(new Error(`Unexpected input names: ${inputNames}`), {
      code: "ARTIFACT_BAD_INPUT",
    });
  if (outputNames.length !== 2 || !["policy_logits", "defender_value"].every((n) => outputNames.includes(n)))
    throw Object.assign(new Error(`Unexpected output names: ${outputNames}`), {
      code: "ARTIFACT_BAD_OUTPUT",
    });
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
    policyName: "policy_logits",
    valueName: "defender_value",
  };
  return {
    provider_requested: provider,
    provider_evidence: evidence,
    execution_placement: placement,
    gpu_execution_verified: self.__neuralProvider.gpuVerified(placement),
    model_sha256: expectedSha256,
    model_bytes: buffer.byteLength,
    input_names: inputNames,
    output_names: outputNames,
    memory: memorySnapshot(),
  };
}

function assertPlanes(planes, size) {
  const expected = INPUT_PLANES * size * size;
  if (!Array.isArray(planes) || planes.length !== expected)
    throw new Error(`Expected ${expected} plane values, received ${planes?.length ?? "none"}`);
  for (const value of planes) {
    if (typeof value !== "number" || !Number.isFinite(value))
      throw new Error("Input planes contain a non-finite value");
  }
}

function assertLegal(legal, size) {
  const limit = size ** 4;
  if (!Array.isArray(legal) || legal.length === 0)
    throw new Error("Legal action indices are required");
  for (const index of legal) {
    if (!Number.isInteger(index) || index < 0 || index >= limit)
      throw new Error(`Legal action index ${index} is out of range [0, ${limit})`);
  }
  return legal;
}

function legalSoftmax(logits, legal) {
  const values = legal.map((index) => logits[index]);
  if (legal.some((index, position) => !Number.isFinite(values[position])))
    throw new Error("Policy logits contain a non-finite value");
  const maximum = Math.max(...values);
  const exponentials = values.map((value) => Math.exp(value - maximum));
  const total = exponentials.reduce((sum, value) => sum + value, 0);
  return exponentials.map((value) => value / total);
}

function illegalMass(logits, legal) {
  const maximum = Math.max(...logits);
  const exponentials = logits.map((value) => Math.exp(value - maximum));
  const total = exponentials.reduce((sum, value) => sum + value, 0);
  const legalSet = new Set(legal);
  let sum = 0;
  for (let index = 0; index < exponentials.length; index += 1)
    if (!legalSet.has(index)) sum += exponentials[index];
  return sum / total;
}

async function inferSingle(request) {
  if (!session || !model) throw new Error("No model session is loaded");
  const { planes, legal } = request;
  assertPlanes(planes, model.size);
  const legalIds = assertLegal(legal, model.size);
  const prepStarted = performance.now();
  const data = new Float32Array(planes);
  const tensor = new ortLib.Tensor("float32", data, [1, 6, model.size, model.size]);
  const prepareMs = performance.now() - prepStarted;
  const runStarted = performance.now();
  const output = await session.run({ [model.inputName]: tensor });
  const runMs = performance.now() - runStarted;
  tensor?.dispose?.();
  const policy = output[model.policyName];
  const value = output[model.valueName];
  const logits = Array.from(policy.data);
  const probabilities = legalSoftmax(logits, legalIds);
  return {
    provider_requested: model.providerRequested,
    execution_placement: model.executionPlacement,
    inference_ms: prepareMs + runMs,
    tensor_prepare_ms: prepareMs,
    run_ms: runMs,
    policy_shape: [...policy.dims],
    value_shape: [...value.dims],
    policy_logits: logits,
    value: value.data[0],
    legal: legalIds,
    legal_probabilities: probabilities,
    illegal_mass_raw: illegalMass(logits, legalIds),
    model_sha256: model.modelSha256,
  };
}

async function inferMany(request) {
  if (!session || !model) throw new Error("No model session is loaded");
  const cases = request.cases;
  if (!Array.isArray(cases) || cases.length === 0) throw new Error("cases must be a non-empty array");
  if (cases.length > MAX_BATCH) throw new Error(`batch ${cases.length} exceeds the ${MAX_BATCH} limit`);
  const prepStarted = performance.now();
  const planeCount = INPUT_PLANES * model.size * model.size;
  const data = new Float32Array(planeCount * cases.length);
  const legalIds = [];
  cases.forEach((entry, batchIndex) => {
    assertPlanes(entry.planes, model.size);
    legalIds.push(assertLegal(entry.legal, model.size));
    data.set(entry.planes, batchIndex * planeCount);
  });
  const tensor = new ortLib.Tensor("float32", data, [cases.length, 6, model.size, model.size]);
  const tensorPrepareMs = performance.now() - prepStarted;
  const runStarted = performance.now();
  const output = await session.run({ [model.inputName]: tensor });
  const runMs = performance.now() - runStarted;
  tensor?.dispose?.();
  const policy = output[model.policyName];
  const values = output[model.valueName].data;
  const policyRow = model.size ** 4;
  const results = cases.map((entry, batchIndex) => {
    const logits = Array.from(
      policy.data.subarray(batchIndex * policyRow, (batchIndex + 1) * policyRow),
    );
    return {
      index: batchIndex,
      policy_logits: logits,
      value: values[batchIndex],
      legal: legalIds[batchIndex],
      legal_probabilities: legalSoftmax(logits, legalIds[batchIndex]),
    };
  });
  return {
    provider_requested: model.providerRequested,
    execution_placement: model.executionPlacement,
    batch: cases.length,
    batch_limit: MAX_BATCH,
    tensor_prepare_ms: tensorPrepareMs,
    run_ms: runMs,
    per_position_ms: runMs / cases.length,
    policy_shape: [...policy.dims],
    value_shape: [...output[model.valueName].dims],
    results,
    model_sha256: model.modelSha256,
  };
}

async function benchmark(request) {
  if (!session || !model) throw new Error("No model session is loaded");
  const batchSizes = request.batchSizes;
  if (!Array.isArray(batchSizes) || !batchSizes.length) throw new Error("batchSizes is required");
  for (const batch of batchSizes) {
    if (!Number.isInteger(batch) || batch < 1 || batch > MAX_BATCH)
      throw new Error(`Invalid batch size ${batch}; allowed 1..${MAX_BATCH}`);
  }
  const warmup = Number.isInteger(request.warmup) ? request.warmup : 2;
  const reps = Number.isInteger(request.reps) ? request.reps : 5;
  if (warmup < 0 || warmup > 20) throw new Error("warmup must be 0..20");
  if (reps < 1 || reps > 50) throw new Error("reps must be 1..50");
  // Distinct positions: repeat the supplied distinct cases to fill a batch so
  // this exercises real batched inference rather than duplicating one input.
  const cases = request.cases;
  if (!Array.isArray(cases) || !cases.length) throw new Error("distinct benchmark cases are required");
  const rows = [];
  for (const batch of batchSizes) {
    const filled = Array.from({ length: batch }, (_, index) => cases[index % cases.length]);
    for (let index = 0; index < warmup; index += 1) await inferMany({ cases: filled });
    const times = [];
    for (let index = 0; index < reps; index += 1) {
      const started = performance.now();
      await inferMany({ cases: filled });
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
  return { provider_requested: model.providerRequested, execution_placement: model.executionPlacement, rows };
}

async function release(request) {
  const released = releaseSession();
  if (request?.unloadBundle) {
    ortLib = null;
    ortBundle = null;
  }
  model = null;
  return { released, memory: memorySnapshot() };
}

onmessage = async ({ data: { id, request } }) => {
  try {
    let value;
    if (request.action === "capabilities") value = await capabilities();
    else if (request.action === "load") value = await loadModel(request);
    else if (request.action === "infer") value = await inferSingle(request);
    else if (request.action === "inferMany") value = await inferMany(request);
    else if (request.action === "benchmark") value = await benchmark(request);
    else if (request.action === "release") value = await release(request);
    else throw new Error(`Unknown neural action: ${request.action}`);
    postMessage({ id, value });
  } catch (error) {
    postMessage({
      id,
      error: String(error?.message ?? error),
      code: error?.code ?? null,
    });
  }
};
