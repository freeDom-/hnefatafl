// Negative tests for the mandatory staged-source integrity contract.
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const assets = createRequire(import.meta.url)("./search-assets.js");
const SHA = "a".repeat(64);

function manifest(overrides = {}) {
  const base = {
    schema: 1,
    pyodide: "0.27.7",
    python_modules: ["ai.py", "engine.py"],
    root_modules: ["search_bridge.py", "mcts_vectors.py"],
    inventory: [
      { path: "python/hnefatafl/ai.py", bytes: 10, sha256: SHA },
      { path: "python/hnefatafl/engine.py", bytes: 10, sha256: SHA },
      { path: "search_bridge.py", bytes: 10, sha256: SHA },
      { path: "mcts_vectors.py", bytes: 10, sha256: SHA },
    ],
    runtime_inventory: [{ path: "runtime/pyodide.js", bytes: 10, sha256: SHA }],
  };
  return { ...base, ...overrides };
}

const results = [];
function check(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
    console.log(`ok   ${name}`);
  } catch (error) {
    results.push({ name, ok: false, error: String(error?.message ?? error) });
    console.log(`FAIL ${name}: ${error?.message ?? error}`);
  }
}
function rejects(name, fn, fragment) {
  check(name, () => {
    assert.throws(fn, (error) => {
      assert.match(String(error.message), fragment);
      return true;
    });
  });
}

check("a complete manifest validates and exposes every required digest", () => {
  const validation = assets.validateSearchManifest(manifest());
  assert.equal(validation.modules.length, 2);
  assert.equal(
    assets.requiredDigest(validation, "python/hnefatafl/ai.py"),
    SHA,
  );
  assert.equal(assets.requiredDigest(validation, "search_bridge.py"), SHA);
});

rejects("an unsupported schema is rejected", () => assets.validateSearchManifest(manifest({ schema: 2 })), /schema/i);
rejects("a missing Pyodide version is rejected", () => assets.validateSearchManifest(manifest({ pyodide: "" })), /Pyodide/);
rejects("empty python modules are rejected", () => assets.validateSearchManifest(manifest({ python_modules: [] })), /non-empty/);
rejects("invalid module names are rejected", () => assets.validateSearchManifest(manifest({ python_modules: ["../evil.py"] })), /invalid module name/);
rejects("duplicate module names are rejected", () => assets.validateSearchManifest(manifest({ python_modules: ["ai.py", "ai.py"] })), /duplicate/);
rejects("missing root modules are rejected", () => assets.validateSearchManifest(manifest({ root_modules: [] })), /non-empty/);
rejects(
  "a missing search_bridge is rejected",
  () => assets.validateSearchManifest(manifest({ root_modules: ["mcts_vectors.py"] })),
  /search_bridge/,
);
rejects("an empty inventory is rejected", () => assets.validateSearchManifest(manifest({ inventory: [] })), /non-empty/);
rejects(
  "a source without an inventory digest is rejected",
  () =>
    assets.validateSearchManifest(
      manifest({
        inventory: manifest().inventory.filter(
          (entry) => entry.path !== "python/hnefatafl/ai.py",
        ),
      }),
    ),
  /no digest for required source python\/hnefatafl\/ai.py/,
);
rejects(
  "a duplicate inventory path is rejected",
  () =>
    assets.validateSearchManifest({
      ...manifest(),
      inventory: [...manifest().inventory, manifest().inventory[0]],
    }),
  /duplicate path/,
);
rejects(
  "a malformed inventory digest is rejected",
  () => {
    const entries = manifest().inventory.map((entry) => ({ ...entry }));
    entries[0].sha256 = "not-a-digest";
    return assets.validateSearchManifest({ ...manifest(), inventory: entries });
  },
  /SHA-256 digest is required/,
);
rejects(
  "a missing inventory byte count is rejected",
  () => {
    const entries = manifest().inventory.map((entry) => ({ ...entry }));
    delete entries[0].bytes;
    return assets.validateSearchManifest({ ...manifest(), inventory: entries });
  },
  /invalid bytes/,
);
rejects(
  "an unverified source cannot request a digest",
  () => {
    const validation = assets.validateSearchManifest(manifest());
    return assets.requiredDigest(validation, "python/hnefatafl/unknown.py");
  },
  /No staged digest/,
);
rejects("an undefined expected digest is rejected", () => assets.validateDigest(undefined, "x.py"), /SHA-256 digest is required/);
rejects("a malformed expected digest is rejected", () => assets.validateDigest("xyz", "x.py"), /SHA-256 digest is required/);
rejects(
  "a duplicate runtime path is rejected",
  () =>
    assets.validateSearchManifest({
      ...manifest(),
      runtime_inventory: [
        { path: "runtime/pyodide.js", bytes: 1, sha256: SHA },
        { path: "runtime/pyodide.js", bytes: 1, sha256: SHA },
      ],
    }),
  /duplicate path/,
);
rejects(
  "a malformed runtime digest is rejected",
  () =>
    assets.validateSearchManifest({
      ...manifest(),
      runtime_inventory: [{ path: "runtime/pyodide.js", bytes: 1, sha256: "bad" }],
    }),
  /SHA-256 digest is required/,
);

check("an altered fetched digest is rejected", () => {
  assert.throws(
    () => assets.assertFetchedDigest(SHA, "b".repeat(64), "search_bridge.py"),
    /digest mismatch/,
  );
});
check("a matching fetched digest is accepted", () => {
  assert.equal(assets.assertFetchedDigest(SHA, SHA, "search_bridge.py"), SHA);
});
check("a fetched source without a valid digest is rejected", () => {
  assert.throws(
    () => assets.assertFetchedDigest(SHA, "not-a-digest", "search_bridge.py"),
    /no valid digest/,
  );
});

const failures = results.filter((row) => !row.ok);
console.log(`\n${results.length - failures.length}/${results.length} asset checks passed`);
if (failures.length) process.exit(1);
