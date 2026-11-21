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

onmessage = async ({ data: { id, payload } }) => {
  try {
    await ready;
    if (typeof payload !== "string") throw new Error("Expected a serialized search payload");
    runtime.globals.set("search_request", payload);
    const value = runtime.runPython("dispatch(search_request)");
    postMessage({ id, value });
  } catch (error) {
    postMessage({ id, error: String(error?.message ?? error) });
  }
};
