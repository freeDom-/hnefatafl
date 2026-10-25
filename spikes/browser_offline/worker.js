/* Classic worker: the complete Python runtime and all search work stay here. */
let runtime;
const ready = (async () => {
  importScripts("./runtime/pyodide.js");
  runtime = await loadPyodide({ indexURL: "./runtime/" });
  if (runtime.version !== "0.27.7") throw new Error("Expected Pyodide 0.27.7");
  runtime.FS.mkdirTree("/home/pyodide/hnefatafl");
  for (const name of ["__init__.py", "engine.py", "ai.py"]) {
    const response = await fetch(`./python/hnefatafl/${name}`);
    if (!response.ok)
      throw new Error(`Cannot load ${name}: ${response.status}`);
    runtime.FS.writeFile(
      `/home/pyodide/hnefatafl/${name}`,
      await response.text(),
    );
  }
  const response = await fetch("./bridge.py");
  if (!response.ok) throw new Error(`Cannot load bridge: ${response.status}`);
  runtime.runPython(await response.text());
})();
// Attach a rejection handler immediately; each request still receives the failure.
ready.catch(() => {});
onmessage = async ({ data: { id, request } }) => {
  try {
    await ready;
    runtime.globals.set("spike_request", JSON.stringify(request));
    const value = JSON.parse(runtime.runPython("dispatch(spike_request)"));
    postMessage({ id, value });
  } catch (error) {
    postMessage({ id, error: String(error) });
  }
};
