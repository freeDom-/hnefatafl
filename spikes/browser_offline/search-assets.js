/* Staged search-runtime manifest validation shared by the worker and tests.
 *
 * Every mandatory executable Python source must have a valid expected SHA-256.
 * A missing, malformed or duplicate inventory entry is a loading failure, never a
 * silently unverified import.
 */
(function attach(root) {
  const MODULE_NAME = /^[a-z_]+[.]py$/;
  const HEX64 = /^[0-9a-f]{64}$/;
  const REQUIRED_ROOT = "search_bridge.py";

  function fail(message) {
    const error = new Error(message);
    error.code = "SEARCH_ASSETS_INVALID";
    return error;
  }

  function validateDigest(expected, path) {
    if (typeof expected !== "string" || !HEX64.test(expected))
      throw fail(`A staged SHA-256 digest is required for ${path}`);
    return expected;
  }

  function validateSearchManifest(manifest) {
    if (!manifest || typeof manifest !== "object")
      throw fail("search-assets.json must be an object");
    if (manifest.schema !== 1)
      throw fail(`Unsupported search asset manifest schema: ${manifest.schema}`);
    if (typeof manifest.pyodide !== "string" || !manifest.pyodide)
      throw fail("manifest must record the pinned Pyodide version");
    const modules = manifest.python_modules;
    if (!Array.isArray(modules) || modules.length === 0)
      throw fail("manifest.python_modules must be a non-empty list");
    if (modules.some((name) => typeof name !== "string" || !MODULE_NAME.test(name)))
      throw fail("manifest.python_modules contains an invalid module name");
    if (new Set(modules).size !== modules.length)
      throw fail("manifest.python_modules contains duplicate module names");
    const roots = manifest.root_modules;
    if (!Array.isArray(roots) || roots.length === 0)
      throw fail("manifest.root_modules must be a non-empty list");
    if (roots.some((name) => typeof name !== "string" || !MODULE_NAME.test(name)))
      throw fail("manifest.root_modules contains an invalid module name");
    if (new Set(roots).size !== roots.length)
      throw fail("manifest.root_modules contains duplicate module names");
    if (!roots.includes(REQUIRED_ROOT))
      throw fail(`manifest.root_modules must include ${REQUIRED_ROOT}`);
    const inventory = manifest.inventory;
    if (!Array.isArray(inventory) || inventory.length === 0)
      throw fail("manifest.inventory must be a non-empty list");
    const digests = new Map();
    for (const entry of inventory) {
      if (!entry || typeof entry !== "object")
        throw fail("manifest.inventory contains a non-object entry");
      if (typeof entry.path !== "string" || !entry.path)
        throw fail("manifest.inventory entry has no path");
      if (digests.has(entry.path))
        throw fail(`manifest.inventory contains a duplicate path: ${entry.path}`);
      const digest = validateDigest(entry.sha256, entry.path);
      if (typeof entry.bytes !== "number" || !Number.isInteger(entry.bytes) || entry.bytes < 0)
        throw fail(`manifest.inventory entry has invalid bytes: ${entry.path}`);
      digests.set(entry.path, digest);
    }
    const requiredPaths = [
      ...modules.map((name) => `python/hnefatafl/${name}`),
      ...roots,
    ];
    for (const path of requiredPaths) {
      if (!digests.has(path))
        throw fail(`manifest.inventory has no digest for required source ${path}`);
    }
    const runtime = manifest.runtime_inventory;
    if (runtime !== undefined) {
      if (!Array.isArray(runtime) || runtime.length === 0)
        throw fail("manifest.runtime_inventory must be a non-empty list");
      const seen = new Set();
      for (const entry of runtime) {
        if (!entry || typeof entry.path !== "string" || !entry.path)
          throw fail("manifest.runtime_inventory entry has no path");
        if (seen.has(entry.path))
          throw fail(`manifest.runtime_inventory contains a duplicate path: ${entry.path}`);
        seen.add(entry.path);
        validateDigest(entry.sha256, entry.path);
      }
    }
    return { digests, modules: [...modules], roots: [...roots] };
  }

  function assertFetchedDigest(expected, actual, path) {
    validateDigest(expected, path);
    if (typeof actual !== "string" || !HEX64.test(actual))
      throw fail(`Fetched source produced no valid digest for ${path}`);
    if (actual !== expected) throw fail(`Staged source digest mismatch for ${path}`);
    return actual;
  }

  function requiredDigest(validation, path) {
    const digest = validation.digests.get(path);
    if (!digest) throw fail(`No staged digest for required source ${path}`);
    return validateDigest(digest, path);
  }

  const api = {
    validateSearchManifest,
    requiredDigest,
    validateDigest,
    assertFetchedDigest,
    HEX64,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.__searchAssets = api;
})(typeof self !== "undefined" ? self : typeof globalThis !== "undefined" ? globalThis : null);
