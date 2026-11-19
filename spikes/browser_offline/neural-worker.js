/* Experimental neural inference worker: ORT Web WASM/WebGPU, no production use.
 *
 * The worker loads a pinned ONNX Runtime Web bundle, creates an inference-only
 * session from an exported Hnefatafl policy/value model and runs bounded
 * inference benchmarks. It reports the execution provider actually requested and
 * never silently presents a CPU (WASM) session as GPU acceleration: a WebGPU
 * request without an adapter fails explicitly.
 */
let ortLib = null;
let ortBundle = null;
let session = null;
let activeProvider = null;
let sessionBytes = 0;
let modelInfo = null;

function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2
    ? ordered[middle]
    : (ordered[middle - 1] + ordered[middle]) / 2;
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

async function ensureOrt(provider = "wasm") {
  if (ortLib) {
    if (provider === "webgpu" && ortBundle !== "webgpu")
      throw new Error(
        "This worker already loaded the WASM bundle; recreate it to use WebGPU",
      );
    return ortLib;
  }
  const bundle = provider === "webgpu" ? "ort.webgpu.min.js" : "ort.min.js";
  importScripts(`./ort/${bundle}`);
  ortLib = self.ort;
  ortBundle = provider === "webgpu" ? "webgpu" : "wasm";
  if (!ortLib) throw new Error("ORT Web did not expose a global");
  ortLib.env.wasm.wasmPaths = new URL("./ort/", self.location.href).href;
  // Single-threaded WASM fallback: no SharedArrayBuffer or cross-origin
  // isolation required. A multi-threaded build is benchmarked separately.
  ortLib.env.wasm.numThreads = 1;
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
  };
}

async function createSession(buffer, provider, size) {
  if (provider === "webgpu") {
    const probe = await capabilities();
    if (!probe.webGPUAdapter) {
      // Do not fall back to WASM while reporting GPU success.
      throw new Error(
        `WebGPU requested but no adapter is available: ${probe.webGPUAdapterError ?? "null adapter"}`,
      );
    }
  }
  await ensureOrt(provider);
  const created = await ortLib.InferenceSession.create(buffer, {
    executionProviders: [provider],
    graphOptimizationLevel: "all",
  });
  session = created;
  activeProvider = provider;
  sessionBytes = buffer.byteLength;
  modelInfo = { size, inputName: session.inputNames[0], outputNames: [...session.outputNames] };
  return {
    provider,
    model_bytes: sessionBytes,
    input_names: [...session.inputNames],
    output_names: [...session.outputNames],
  };
}

function buildTensor(planes, size, batch) {
  const planeCount = 6 * size * size;
  if (planes.length !== planeCount)
    throw new Error(`Expected ${planeCount} plane values, received ${planes.length}`);
  const data = new Float32Array(planeCount * batch);
  for (let index = 0; index < batch; index += 1) data.set(planes, index * planeCount);
  return new ortLib.Tensor("float32", data, [batch, 6, size, size]);
}

async function runOnce(planes, size, batch) {
  if (!session) throw new Error("No model session is loaded");
  const feeds = {};
  feeds[modelInfo.inputName] = buildTensor(planes, size, batch);
  return session.run(feeds);
}

async function infer({ planes, size, legal }) {
  if (!session) throw new Error("No model session is loaded");
  const started = performance.now();
  const output = await runOnce(planes, size, 1);
  const elapsed = performance.now() - started;
  const policy = output[modelInfo.outputNames[0]];
  const value = output[modelInfo.outputNames[1]];
  const logits = Array.from(policy.data);
  const legalIds = Array.isArray(legal) ? legal : [];
  let legalProbabilities = [];
  if (legalIds.length) {
    const maximum = Math.max(...legalIds.map((index) => logits[index]));
    const exponentials = legalIds.map((index) => Math.exp(logits[index] - maximum));
    const total = exponentials.reduce((sum, item) => sum + item, 0);
    legalProbabilities = exponentials.map((item) => item / total);
  }
  return {
    provider: activeProvider,
    inference_ms: elapsed,
    shape: [...policy.dims],
    policy_logits: logits,
    value: value.data[0],
    legal: legalIds,
    legal_probabilities: legalProbabilities,
  };
}

async function benchmark({ planes, size, batchSizes, warmup = 2, reps = 5 }) {
  if (!session) throw new Error("No model session is loaded");
  const rows = [];
  for (const batch of batchSizes) {
    for (let index = 0; index < warmup; index += 1) await runOnce(planes, size, batch);
    const times = [];
    for (let index = 0; index < reps; index += 1) {
      const started = performance.now();
      await runOnce(planes, size, batch);
      times.push(performance.now() - started);
    }
    rows.push({
      batch,
      warmup,
      reps,
      min_ms: Math.min(...times),
      median_ms: median(times),
      p90_ms: percentile(times, 0.9),
      max_ms: Math.max(...times),
      positions_per_second: batch / (median(times) / 1000),
    });
  }
  return { provider: activeProvider, rows };
}

onmessage = async ({ data: { id, request } }) => {
  try {
    let value;
    if (request.action === "capabilities") value = await capabilities();
    else if (request.action === "load")
      value = await createSession(request.buffer, request.provider, request.size);
    else if (request.action === "infer") value = await infer(request);
    else if (request.action === "benchmark") value = await benchmark(request);
    else throw new Error(`Unknown neural action: ${request.action}`);
    value.heap_capacity_bytes =
      self.performance?.memory?.jsHeapSizeLimit ??
      self.__ortHeapCapacity ??
      null;
    postMessage({ id, value });
  } catch (error) {
    postMessage({ id, error: String(error) });
  }
};
