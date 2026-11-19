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
 *   - "cpu-fallback"             the session reported only WASM/CPU providers
 *   - "webgpu-mixed"             the session reported WebGPU plus another provider
 *   - "placement-unknown"        the session reported only unrecognised providers
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
    if (!evidence || !evidence.length) return "webgpu-session-unverified";
    const gpu = evidence.filter((name) => /webgpu/i.test(name));
    const cpu = evidence.filter((name) => /wasm|cpu/i.test(name));
    if (gpu.length === evidence.length) return "webgpu-session-verified";
    if (cpu.length === evidence.length) return "cpu-fallback";
    // A mixed list containing WebGPU must not be reported as CPU-only
    // execution, and an unknown mix must not be reported as verified GPU.
    if (gpu.length > 0) return "webgpu-mixed";
    return "placement-unknown";
  }

  function gpuVerified(placement) {
    return placement === "webgpu-session-verified";
  }

  const api = { providerEvidence, placementFor, gpuVerified };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.__neuralProvider = api;
})(typeof self !== "undefined" ? self : typeof globalThis !== "undefined" ? globalThis : null);
