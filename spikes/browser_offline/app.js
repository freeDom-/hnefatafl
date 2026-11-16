import { LocalAnalysisClient } from "./analysis-client.js";
const client = new LocalAnalysisClient();
let current = null;
let positionEpoch = 0;
let analysisToken = 0;
let foregroundToken = 0;
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
  const playable = !value.state.winner && value.legal_moves.length > 0;
  controls.move.disabled = !playable;
  controls.ai.disabled = !playable;
  controls.analyze.disabled = false;
  return value;
}
function handleError(error) {
  if (error?.name === "AbortError") setStatus("Local work cancelled");
  else if (error?.name === "TimeoutError")
    setStatus("Local analysis timed out");
  else setStatus(`Spike error: ${error?.message ?? String(error)}`);
}
// Foreground gameplay preempts any background analysis and starts a new position epoch.
function beginForeground(label) {
  invalidateAnalysis();
  setStatus(client.info ? label : "Loading local runtime…");
}
async function foreground(label, action) {
  const token = ++foregroundToken;
  beginForeground(label);
  try {
    const value = await action();
    // A newer foreground operation owns the board and status from here on.
    if (token !== foregroundToken) return undefined;
    return render(value);
  } catch (error) {
    if (token !== foregroundToken) return undefined;
    throw error;
  }
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
  cancel: () => {
    const active = client.busy;
    invalidateAnalysis();
    foregroundToken++;
    client.cancel("Cancelled by user");
    if (active) setStatus("Local work cancelled");
  },
  memory: () => client.info,
  cacheSize: () => client.cache.size,
  raw: request,
  handleError,
  move: (move) => {
    if (
      !move ||
      !Number.isInteger(move.source) ||
      !Number.isInteger(move.target)
    )
      return Promise.reject(new Error("Invalid move"));
    return foreground("Applying move…", () =>
      request(
        { action: "move", state: current.state, move },
        { preempt: true },
      ),
    );
  },
  ai: () => {
    if (!current || current.state.winner || current.legal_moves.length === 0)
      return Promise.reject(new Error("No legal AI move available"));
    return foreground("Choosing local AI move…", async () => {
      const value = await request(
        { action: "ai", state: current.state },
        { preempt: true },
      );
      metrics.ai_ms.push(value.ai_ms);
      return value;
    });
  },
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
  move: () => {
    const raw = document.querySelector("#moves").value;
    if (!raw) return Promise.reject(new Error("No legal move selected"));
    return api.move(JSON.parse(raw));
  },
}))
  document.querySelector(`#${id}`).onclick = () => {
    Promise.resolve().then(action).catch(handleError);
  };
document.querySelector("#pulse").onclick = () => {
  document.querySelector("#pulses").textContent = String(++pulses);
};
let offlineReady = null;
function activateServiceWorker(registration) {
  return (async () => {
    const worker = registration.installing || registration.waiting;
    if (worker && worker.state !== "activated") {
      await new Promise((resolve, reject) => {
        worker.addEventListener("statechange", () => {
          if (worker.state === "redundant")
            reject(new Error("Service worker installation failed"));
          else if (worker.state === "activated") resolve();
        });
      });
    }
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller)
      await new Promise((resolve) =>
        navigator.serviceWorker.addEventListener("controllerchange", resolve, {
          once: true,
        }),
      );
  })();
}
function ensureOfflineInfrastructure() {
  if (!offlineReady) {
    offlineReady = (async () => {
      const registration = await navigator.serviceWorker.register("./sw.js");
      // Installation failure or a stalled activation must not hang startup.
      await Promise.race([
        activateServiceWorker(registration),
        new Promise((_, reject) =>
          setTimeout(
            () => reject(new Error("Service worker did not activate in time")),
            15000,
          ),
        ),
      ]);
      return registration.scope;
    })();
    // A failed attempt must not be cached forever; a later call retries.
    offlineReady.catch(() => {
      offlineReady = null;
    });
  }
  return offlineReady;
}
api.offlineReady = ensureOfflineInfrastructure;
const started = performance.now();
api.ready = (async () => {
  // Offline infrastructure is independent of cancellable gameplay.
  const infrastructure = ensureOfflineInfrastructure().catch((error) => {
    setStatus(`Offline cache unavailable: ${error.message}`);
    return null;
  });
  try {
    const value = await api.newGame("brandubh");
    if (value) {
      metrics.startup_ms = performance.now() - started;
      render(value);
    }
  } catch (error) {
    // A cancelled or superseded first game must not block startup or the cache.
    if (error?.name !== "AbortError") handleError(error);
  }
  return infrastructure;
})();
api.ready.catch(handleError);
