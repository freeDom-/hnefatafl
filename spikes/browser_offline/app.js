const worker = new Worker("./worker.js");
const pending = new Map();
let sequence = 0;
let current;
let pulses = 0;
const status = document.querySelector("#status");
const metrics = { startup_ms: null, ai_ms: [], worker: true };
function request(payload) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error("Worker timed out after 60 seconds"));
    }, 60000);
    pending.set(id, { resolve, reject, timeout });
    worker.postMessage({ id, request: payload });
  });
}
worker.onmessage = ({ data }) => {
  const call = pending.get(data.id);
  if (!call) return;
  clearTimeout(call.timeout);
  pending.delete(data.id);
  if (data.error) call.reject(new Error(data.error));
  else call.resolve(data.value);
};
worker.onerror = (event) => {
  for (const call of pending.values()) {
    clearTimeout(call.timeout);
    call.reject(new Error(event.message));
  }
  pending.clear();
};
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
    run(async () => render(await request({ action: "new", ruleset }))),
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
  fixtures: api.fixtures,
  move: () => api.move(JSON.parse(document.querySelector("#moves").value)),
}))
  document.querySelector(`#${id}`).onclick = () => {
    action().catch(() => {});
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
