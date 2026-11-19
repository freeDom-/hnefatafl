/* Classic worker: the complete Python runtime and all search work stay here. */
let runtime;
const ready = (async () => {
  importScripts("./runtime/pyodide.js");
  runtime = await loadPyodide({ indexURL: "./runtime/" });
  if (runtime.version !== "0.27.7") throw new Error("Expected Pyodide 0.27.7");
  runtime.FS.mkdirTree("/home/pyodide/hnefatafl");
  const manifest = await (await fetch("./assets.json")).json();
  for (const name of manifest.python_modules) {
    if (!/^[a-z_]+\.py$/.test(name))
      throw new Error("Invalid staged module name");
    const response = await fetch(`./python/hnefatafl/${name}`);
    if (!response.ok)
      throw new Error(`Cannot load ${name}: ${response.status}`);
    runtime.FS.writeFile(
      `/home/pyodide/hnefatafl/${name}`,
      await response.text(),
    );
  }
  // Root-level modules importable by bridge.py (for example the shared
  // benchmark definitions) are staged beside it and validated by name.
  for (const name of manifest.root_modules || []) {
    if (name === "bridge.py") continue;
    if (!/^[a-z_]+[.]py$/.test(name))
      throw new Error("Invalid staged root module name");
    const module = await fetch(`./${name}`);
    if (!module.ok)
      throw new Error(`Cannot load ${name}: ${module.status}`);
    runtime.FS.writeFile(`/home/pyodide/${name}`, await module.text());
  }
  const response = await fetch("./bridge.py");
  if (!response.ok) throw new Error(`Cannot load bridge: ${response.status}`);
  runtime.runPython(await response.text());
})();
// Attach a rejection handler immediately; each request still receives the failure.
ready.then(
  () =>
    postMessage({
      id: 0,
      value: {
        heap_capacity_bytes:
          runtime._module?.HEAPU8?.buffer?.byteLength ??
          runtime._module?.HEAP8?.buffer?.byteLength ??
          null,
      },
    }),
  (error) => postMessage({ id: 0, error: String(error) }),
);
onmessage = async ({ data: { id, request } }) => {
  try {
    await ready;
    if (typeof request !== "string")
      throw new Error("Expected a serialized request payload");
    runtime.globals.set("spike_request", request);
    const value = JSON.parse(runtime.runPython("dispatch(spike_request)"));
    value.heap_capacity_bytes =
      runtime._module?.HEAPU8?.buffer?.byteLength ??
      runtime._module?.HEAP8?.buffer?.byteLength ??
      null;
    postMessage({ id, value });
  } catch (error) {
    postMessage({ id, error: String(error) });
  }
};
