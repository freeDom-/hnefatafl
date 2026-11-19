/* Provider-reporting helpers shared by the neural worker and its unit tests.
 *
 * Classic script (no ES module) so a worker can `importScripts` it; the same
 * functions are exported through `module.exports` for Node unit tests.
 *
 * The pinned ORT Web version does not expose a guaranteed per-node execution
 * provider placement API. We therefore distinguish:
 *   - "wasm"                     the WASM/CPU provider was requested
 *   - "webgpu-session-verified"  a session reported only WebGPU providers
 *   - "webgpu-session-unverified" a WebGPU session was created but placement is
 *                                not observable (still NOT a CPU-fallback claim)
 *   - "cpu-fallback"             the session reported a WASM/CPU provider
 *   - "webgpu-mixed"             the session reported a mix of providers
 * A WASM fallback is never reported as GPU acceleration.
 */
(function attach(root) {
  function providerEvidence(created) {
    const candidates = [created?.executionProviders, created?.handler?.executionProviders];
    for (const candidate of candidates) {
      if (Array.isArray(candidate) && candidate.length) return candidate.map(String);
    }
    return null;
  }

  function placementFor(provider, evidence) {
    if (provider === "wasm") return "wasm";
    if (evidence && evidence.length) {
      if (evidence.every((name) => /webgpu/i.test(name))) return "webgpu-session-verified";
      if (evidence.some((name) => /wasm|cpu/i.test(name))) return "cpu-fallback";
      return "webgpu-mixed";
    }
    return "webgpu-session-unverified";
  }

  function gpuVerified(placement) {
    return placement === "webgpu-session-verified";
  }

  const api = { providerEvidence, placementFor, gpuVerified };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.__neuralProvider = api;
})(typeof self !== "undefined" ? self : typeof globalThis !== "undefined" ? globalThis : null);
