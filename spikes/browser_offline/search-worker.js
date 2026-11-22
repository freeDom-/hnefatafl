/* Experimental cooperative neural MCTS search worker (Pyodide).
 *
 * Responsibilities:
 *  - load the pinned Pyodide runtime plus digest-verified staged Python sources;
 *  - keep the real `hnefatafl` engine, shared encoder and `mcts_context` in this
 *    worker, so no rules engine is reimplemented in JavaScript;
 *  - execute one bounded slice of `search_bridge.dispatch` per message and return
 *    immediately, so the worker never blocks its event loop waiting for inference;
 *  - own and release cooperative search contexts (the bridge closes the Python
 *    generator on completion, cancellation and worker disposal).
 *
 * Inference itself lives in the separate neural worker (ORT Web). This worker
 * never loads PyTorch, ONNX Python or NumPy.
 */
let runtime = null;
let info = null;
let directPort = null;
let directState = null;

function dispatch(payload) {
  runtime.globals.set("search_request", JSON.stringify(payload));
  const envelope = JSON.parse(runtime.runPython("dispatch(search_request)"));
  if (envelope?.ok !== true)
    throw Object.assign(new Error(envelope?.message ?? "Search dispatch failed"), {
      code: envelope?.code ?? "SEARCH_FAILED",
    });
  return envelope.event;
}

function directErrorMessage(error) {
  return { message: String(error?.message ?? error), code: error?.code ?? null };
}

function postDirect(port, value) {
  port.postMessage(value);
}

function rejectDirectPending(error) {
  const pending = directState?.pending;
  if (!pending) return;
  directState.pending = null;
  pending.reject(error);
}

async function directInfer(request, model, searchId) {
  if (!directPort) throw new Error("Direct inference port is not attached");
  if (!directState || directState.searchId !== searchId)
    throw new Error("Direct inference lost its search context");
  const requestId = `${searchId}:${request.request_index}`;
  const response = await new Promise((resolve, reject) => {
    const pending = { resolve, reject, requestId };
    directState.pending = pending;
    directPort.postMessage({
      kind: "infer",
      id: requestId,
      model,
      request: {
        action: "inferCompact",
        planes_base64: request.planes,
        size: request.size,
        legal: request.legal,
        ruleset: request.ruleset,
        request_id: requestId,
        position_digest: request.position_digest,
        model,
      },
    });
  });
  directState.pending = null;
  if (response.request_id !== requestId)
    throw new Error("Direct inference response belongs to another request");
  if (response.position_digest !== request.position_digest)
    throw new Error("Direct inference response belongs to another position");
  if (
    !Array.isArray(response.legal) ||
    response.legal.length !== request.legal.length ||
    response.legal.some((value, index) => value !== request.legal[index])
  )
    throw new Error("Direct inference legal actions do not match the request");
  if (
    response.model_sha256 !== model.sha256 ||
    response.model_ruleset !== model.ruleset
  )
    throw new Error("Direct inference response came from another model");
  return response;
}

async function runDirectSearch(payload) {
  const searchId = payload.search_id;
  let event;
  try {
    event = dispatch({
      action: "start",
      search_id: searchId,
      state: payload.state,
      settings: payload.settings,
      model: payload.model,
      provider: payload.provider ?? null,
      trace: false,
    });
    while (event.kind === "evaluate") {
      if (!directState || directState.searchId !== searchId || directState.cancelled)
        throw Object.assign(new Error("Direct search cancelled"), { code: "CANCELLED" });
      postMessage({ kind: "direct_inference", search_id: searchId, phase: "start" });
      const response = await directInfer(event.request, payload.model, searchId);
      postMessage({ kind: "direct_inference", search_id: searchId, phase: "end" });
      if (!directState || directState.cancelled)
        throw Object.assign(new Error("Direct search cancelled"), { code: "CANCELLED" });
      if (event.progress) postMessage({ kind: "direct_progress", search_id: searchId, progress: event.progress });
      event = dispatch({
        action: "resume",
        search_id: searchId,
        request_index: event.request.request_index,
        legal: event.request.legal,
        priors: response.legal_probabilities,
        value: response.value,
      });
    }
    if (event.kind === "cancelled") {
      postMessage({ kind: "direct_cancelled", search_id: searchId, event });
    } else {
      postMessage({ kind: "direct_complete", search_id: searchId, event });
    }
  } catch (error) {
    postMessage({
      kind: directState?.cancelled ? "direct_cancelled" : "direct_error",
      search_id: searchId,
      error: directErrorMessage(error),
      event: error?.code === "CANCELLED" ? { kind: "cancelled", reason: "cancelled" } : null,
    });
  } finally {
    if (directState?.searchId === searchId) directState = null;
  }
}

function attachDirect(port) {
  directPort = port;
  port.onmessage = ({ data }) => {
    const pending = directState?.pending;
    // Late responses after cancellation or a replacement never resume a context.
    if (!pending || data?.id !== pending.requestId) return;
    directState.pending = null;
    if (data.kind === "infer_result") pending.resolve(data.value);
    else pending.reject(Object.assign(new Error(data.error), { code: data.code }));
  };
}

const MODULE = /^[a-z_]+[.]py$/;
const ROOT_MODULE = /^[a-z_]+[.]py$/;

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function fetchVerified(path, expected) {
  // A missing digest is a hard failure: mandatory executable sources are never
  // imported unverified.
  self.__searchAssets.validateDigest(expected, path);
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Cannot load ${path}: HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  const digest = await sha256Hex(bytes);
  self.__searchAssets.assertFetchedDigest(expected, digest, path);
  return bytes;
}

const ready = (async () => {
  importScripts("./search-assets.js");
  importScripts("./runtime/pyodide.js");
  runtime = await loadPyodide({ indexURL: "./runtime/" });
  if (runtime.version !== "0.27.7") throw new Error("Expected Pyodide 0.27.7");
  const response = await fetch("./search-assets.json");
  if (!response.ok)
    throw new Error(`Cannot load search-assets.json: HTTP ${response.status}`);
  const assets = await response.json();
  const validation = self.__searchAssets.validateSearchManifest(assets);
  runtime.FS.mkdirTree("/home/pyodide/hnefatafl");
  for (const name of validation.modules) {
    if (!MODULE.test(name)) throw new Error(`Invalid staged module name: ${name}`);
    const path = `python/hnefatafl/${name}`;
    runtime.FS.writeFile(
      `/home/pyodide/hnefatafl/${name}`,
      await fetchVerified(`./${path}`, self.__searchAssets.requiredDigest(validation, path)),
    );
  }
  for (const name of validation.roots) {
    if (!ROOT_MODULE.test(name)) throw new Error(`Invalid staged root module: ${name}`);
    runtime.FS.writeFile(
      `/home/pyodide/${name}`,
      await fetchVerified(`./${name}`, self.__searchAssets.requiredDigest(validation, name)),
    );
  }
  const bridgePath = "search_bridge.py";
  const bridge = await fetchVerified(
    `./${bridgePath}`,
    self.__searchAssets.requiredDigest(validation, bridgePath),
  );
  runtime.runPython(new TextDecoder().decode(bridge));
  const meta = JSON.parse(
    runtime.runPython(
      "import json\njson.dumps({'protocol': 1, 'python': __import__('sys').version})",
    ),
  );
  info = {
    ...meta,
    pyodide: runtime.version,
    heap_capacity_bytes:
      runtime._module?.HEAPU8?.buffer?.byteLength ??
      runtime._module?.HEAP8?.buffer?.byteLength ??
      null,
    bundle_id: assets.bundle_id ?? null,
  };
  return info;
})();

ready.then(
  (value) => postMessage({ id: 0, value }),
  (error) => postMessage({ id: 0, error: String(error?.message ?? error) }),
);
// Attach a handler immediately so a rejected boot never becomes unhandled.
ready.catch(() => {});

onmessage = async ({ data }) => {
  const { id, payload } = data;
  try {
    if (data.control === "attachDirect") {
      attachDirect(data.port);
      postMessage({ id, value: { kind: "direct_attached" } });
      return;
    }
    await ready;
    if (typeof payload !== "string") throw new Error("Expected a serialized search payload");
    const request = JSON.parse(payload);
    if (request.action === "start_direct") {
      if (directState) {
        postMessage({
          id,
          value: JSON.stringify({
            ok: false,
            code: "SEARCH_BUSY",
            message: "Direct search is already active",
          }),
        });
        return;
      }
      directState = {
        searchId: request.search_id,
        cancelled: false,
        pending: null,
        pendingId: null,
      };
      postMessage({
        id,
        value: JSON.stringify({
          ok: true,
          event: { kind: "direct_admitted", search_id: request.search_id },
        }),
      });
      void runDirectSearch(request);
      return;
    }
    if (request.action === "cancel" && directState?.searchId === request.search_id) {
      directState.cancelled = true;
      rejectDirectPending(Object.assign(new Error("Direct search cancelled"), { code: "CANCELLED" }));
      const value = dispatch(request);
      postMessage({ id, value });
      return;
    }
    runtime.globals.set("search_request", payload);
    const value = runtime.runPython("dispatch(search_request)");
    postMessage({ id, value });
  } catch (error) {
    postMessage({ id, error: String(error?.message ?? error) });
  }
};
