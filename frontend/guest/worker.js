/* The guest runtime has no API, account, online-ID or arbitrary-board protocol. */
let python;
const ready = (async () => {
  importScripts('./runtime/pyodide.js');
  python = await loadPyodide({ indexURL: new URL('./runtime/', location.href).href });
  if (python.version !== '0.27.7') throw new Error('Unsupported Python runtime.');
  python.FS.mkdirTree('/home/pyodide/hnefatafl');
  for (const name of ['__init__.py', 'engine.py', 'ai.py', 'guest.py', 'symmetry.py']) {
    const response = await fetch(`./python/${name}`);
    if (!response.ok) throw new Error('Guest engine unavailable.');
    python.FS.writeFile(`/home/pyodide/hnefatafl/${name}`, await response.text());
  }
  const bridge = await fetch('./bridge.py');
  if (!bridge.ok) throw new Error('Guest engine unavailable.');
  python.runPython(await bridge.text());
})();
ready.catch(() => {});
let running = false;
onmessage = async ({ data }) => {
  if (running) {
    postMessage({ id: data.id, error: 'Guest engine is busy.' });
    return;
  }
  running = true;
  try {
    await ready;
    if (!data || !Number.isSafeInteger(data.id) || JSON.stringify(data.request).length > 262144)
      throw new Error('Invalid guest request.');
    python.globals.set('guest_request', JSON.stringify(data.request));
    const value = JSON.parse(python.runPython('dispatch(guest_request)'));
    postMessage({ id: data.id, value });
  } catch (error) {
    // No user payload, stack, account or interpreter internals are reflected.
    const message = String(error);
    const known = [
      'Unsupported guest',
      'Invalid guest',
      'guest save',
      'guest move history',
      'different engine version',
      'other player',
      'guest position',
      'guest game is finished',
    ];
    postMessage({
      id: data.id,
      error: known.some((s) => message.includes(s))
        ? 'Guest data or operation is invalid or unsupported.'
        : 'Guest engine could not complete this operation.',
    });
  } finally {
    running = false;
  }
};
