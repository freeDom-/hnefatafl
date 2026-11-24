/* Dedicated test-only neural inference worker.
 *
 * This entry point is never staged by prepare_neural.py. The normal
 * neural-worker.js has no test adapter and rejects all test actions, so a normal
 * client cannot activate an inference delay even if it calls request() directly.
 *
 * The test harness serves this file explicitly for browser lifecycle tests.
 * Delay handling lives here, not in the production-facing worker.
 */
importScripts("./neural-worker.js");

let configured = false;

async function delay(request) {
  if (!configured)
    throw Object.assign(new Error("Test hooks are not configured"), { code: "TEST_HOOKS_DISABLED" });
  const milliseconds = request.test_delay_ms;
  if (milliseconds === undefined || milliseconds === null) return;
  if (!Number.isInteger(milliseconds) || milliseconds < 0 || milliseconds > 5000)
    throw Object.assign(new Error("test_delay_ms must be an integer in 0..5000"), {
      code: "INPUT_INVALID",
    });
  if (milliseconds > 0) await new Promise((done) => setTimeout(done, milliseconds));
}

self.__neuralTestAdapter = {
  async handle(request) {
    const action = request.test_action;
    if (action === "configure") {
      if (configured)
        throw Object.assign(new Error("Test hooks may be configured only once per worker"), {
          code: "INPUT_INVALID",
        });
      if (request.enabled !== true)
        throw Object.assign(new Error("Test hooks must be enabled explicitly"), {
          code: "INPUT_INVALID",
        });
      configured = true;
      return { test_hooks: true };
    }
    if (action === "infer" || action === "infer_compact") {
      await delay(request);
      const { test_action: _testAction, test_delay_ms: _delay, ...inference } = request;
      const internals = self.__neuralWorkerInternals;
      if (!internals)
        throw Object.assign(new Error("Worker internals are unavailable"), {
          code: "TEST_HOOKS_DISABLED",
        });
      return action === "infer"
        ? internals.inferSingle(inference)
        : internals.inferCompact(inference);
    }
    throw Object.assign(new Error("Unknown test action"), { code: "INPUT_INVALID" });
  },
};
