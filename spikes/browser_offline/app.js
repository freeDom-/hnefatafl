import { LocalAnalysisClient } from "./analysis-client.js";
const client = new LocalAnalysisClient();
let current = null;
let positionEpoch = 0;
let analysisToken = 0;
let pulses = 0;
const status = document.querySelector("#status");
const analysisOutput = document.querySelector("#analysis");
const metrics = { startup_ms: null, ai_ms: [], worker: true };
const request = (payload, options) => client.request(payload, options);
const controls = {
  ai: document.querySelector("#ai"),
  analyze: document.querySelector("#analyze"),
  move: document.querySelector("#move"),
};
function setStatus(text) {
  status.textContent = text;
}
function setAnalysis(text) {
  analysisOutput.textContent = text;
}
// Invalidates any in-flight analysis render without touching the result cache.
function invalidateAnalysis() {
  analysisToken++;
  setAnalysis("");
}
function render(value) {
  current = value;
  positionEpoch++;
  const n = Math.sqrt(value.state.board.length);
  document.querySelector("#board").textContent = Array.from(
    { length: n },
    (_, r) => value.state.board.slice(r * n, (r + 1) * n).join(" "),
  ).join("\n");
  document.querySelector("#moves").replaceChildren(
    ...value.legal_moves.map((move) => {
      const option = document.createElement("option");
      option.value = JSON.stringify(move);
      option.textContent = `${move.source} → ${move.target}`;
      return option;
    }),
  );
  setStatus(
    value.state.winner
      ? `${value.state.winner}: ${value.state.reason}`
      : `${value.state.turn} to move`,
  );
  document.querySelector("#metrics").textContent = JSON.stringify(
    metrics,
    null,
    2,
  );
  invalidateAnalysis();
  for (const control of Object.values(controls)) control.disabled = false;
  return value;
}
function handleError(error) {
  if (error?.name === "AbortError") setStatus("Local work cancelled");
  else setStatus(`Spike error: ${error?.message ?? String(error)}`);
}
// Foreground gameplay preempts any background analysis and starts a new position epoch.
function beginForeground(label) {
  invalidateAnalysis();
  setStatus(client.info ? label : "Loading local runtime…");
}
async function foreground(label, action) {
  beginForeground(label);
  return render(await action());
}
const api = {
  info: () => request({ action: "info" }),
  probe: () => request({ action: "probe" }),
  newGame: (ruleset) =>
    foreground("Starting new game…", () =>
      request({ action: "new", ruleset }, { preempt: true }),
    ),
  restore: (state) =>
    foreground("Restoring position…", () =>
      request({ action: "restore", state }, { preempt: true }),
    ),
  analyze: async (config) => {
    if (!current) throw new Error("No position loaded");
    const epoch = positionEpoch;
    const token = ++analysisToken;
    setAnalysis("Analyzing…");
    setStatus("Analyzing locally…");
    try {
      const value = await client.analyze(current.state, config, {
        preempt: true,
      });
      // A superseded position or analysis must never publish its result.
      if (token !== analysisToken || epoch !== positionEpoch) return undefined;
      setAnalysis(JSON.stringify(value, null, 2));
      setStatus("Analysis complete");
      return value;
    } catch (error) {
      if (token !== analysisToken || epoch !== positionEpoch) return undefined;
      throw error;
    }
  },
  analyzeState: (state, config) => client.analyze(state, config),
  cancel: () => client.cancel(),
  memory: () => client.info,
  cacheSize: () => client.cache.size,
  raw: request,
  move: (move) =>
    foreground("Applying move…", () =>
      request(
        { action: "move", state: current.state, move },
        { preempt: true },
      ),
    ),
  ai: () =>
    foreground("Choosing local AI move…", async () => {
      const value = await request(
        { action: "ai", state: current.state },
        { preempt: true },
      );
      metrics.ai_ms.push(value.ai_ms);
      return value;
    }),
  fixtures: async () => {
    beginForeground("Comparing CPython fixtures…");
    const vectors = await (await fetch("./fixtures.json")).json();
    const result = await request(
      { action: "fixtures", vectors },
      { preempt: true },
    );
    setStatus(`${result.passed} CPython fixtures matched`);
    return result;
  },
  metrics,
};
window.spike = api;
for (const [id, action] of Object.entries({
  new: () => api.newGame(document.querySelector("#ruleset").value),
  ai: api.ai,
  analyze: () =>
    api.analyze({
      algorithm: document.querySelector("#algorithm").value,
      depth: 2,
      time_limit: 0.2,
    }),
  cancel: api.cancel,
  fixtures: api.fixtures,
  move: () => api.move(JSON.parse(document.querySelector("#moves").value)),
}))
  document.querySelector(`#${id}`).onclick = () => {
    Promise.resolve().then(action).catch(handleError);
  };
document.querySelector("#pulse").onclick = () => {
  document.querySelector("#pulses").textContent = String(++pulses);
};
const started = performance.now();
api.ready = (async () => {
  const value = await api.newGame("brandubh");
  metrics.startup_ms = performance.now() - started;
  render(value);
  const registration = await navigator.serviceWorker.register("./sw.js");
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller)
    await new Promise((resolve) =>
      navigator.serviceWorker.addEventListener("controllerchange", resolve, {
        once: true,
      }),
    );
  return registration.scope;
})();
api.ready.catch(handleError);
