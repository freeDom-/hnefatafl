import { LocalAnalysisClient } from "./analysis-client.js";
const client = new LocalAnalysisClient();
let current;
let pulses = 0;
const status = document.querySelector("#status");
const metrics = { startup_ms: null, ai_ms: [], worker: true };
const request = (payload, options) => client.request(payload, options);
function render(value) {
  current = value;
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
  status.textContent = value.state.winner
    ? `${value.state.winner}: ${value.state.reason}`
    : `${value.state.turn} to move`;
  document.querySelector("#metrics").textContent = JSON.stringify(
    metrics,
    null,
    2,
  );
  return value;
}
async function run(action) {
  try {
    return await action();
  } catch (error) {
    status.textContent = `Spike error: ${error.message}`;
    throw error;
  }
}
const api = {
  info: () => request({ action: "info" }),
  probe: () => request({ action: "probe" }),
  newGame: (ruleset) =>
    run(async () => {
      if (client.busy) client.cancel("Game changed");
      return render(await request({ action: "new", ruleset }));
    }),
  restore: (state) =>
    run(async () => {
      if (client.busy) client.cancel("Replay cursor changed");
      return render(await request({ action: "restore", state }));
    }),
  analyze: (config) => client.analyze(current.state, config),
  analyzeState: (state, config) => client.analyze(state, config),
  cancel: () => client.cancel(),
  memory: () => client.info,
  cacheSize: () => client.cache.size,
  raw: request,
  move: (move) =>
    run(async () =>
      render(await request({ action: "move", state: current.state, move })),
    ),
  ai: () =>
    run(async () => {
      const value = await request({ action: "ai", state: current.state });
      metrics.ai_ms.push(value.ai_ms);
      return render(value);
    }),
  fixtures: () =>
    run(async () => {
      const vectors = await (await fetch("./fixtures.json")).json();
      const result = await request({ action: "fixtures", vectors });
      status.textContent = `${result.passed} CPython fixtures matched`;
      return result;
    }),
  metrics,
};
window.spike = api;
for (const [id, action] of Object.entries({
  new: () => api.newGame(document.querySelector("#ruleset").value),
  ai: api.ai,
  analyze: async () => {
    const value = await api.analyze({
      algorithm: document.querySelector("#algorithm").value,
      depth: 2,
      time_limit: 0.2,
    });
    document.querySelector("#analysis").textContent = JSON.stringify(
      value,
      null,
      2,
    );
    return value;
  },
  cancel: api.cancel,
  fixtures: api.fixtures,
  move: () => api.move(JSON.parse(document.querySelector("#moves").value)),
}))
  document.querySelector(`#${id}`).onclick = () => {
    Promise.resolve()
      .then(action)
      .catch((error) => {
        status.textContent = `Spike error: ${error.message}`;
      });
  };
document.querySelector("#pulse").onclick = () => {
  document.querySelector("#pulses").textContent = String(++pulses);
};
const started = performance.now();
api.ready = api.newGame("brandubh").then(async (value) => {
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
});
api.ready.catch(() => {});
