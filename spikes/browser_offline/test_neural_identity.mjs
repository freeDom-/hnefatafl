// Request-side model identity contract checks (no browser/ORT required).
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const identity = createRequire(import.meta.url)("./neural-identity.js");
const A = "a".repeat(64);
const B = "b".repeat(64);
const active = { modelSha256: A, ruleset: "brandubh", size: 7 };

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

check("valid identity is accepted", () => {
  assert.deepEqual(identity.validateModelIdentity({ sha256: A, ruleset: "brandubh", size: 7 }, active), {
    sha256: A,
    ruleset: "brandubh",
    size: 7,
  });
});

check("different weights with identical architecture are rejected", () => {
  assert.throws(
    () => identity.validateModelIdentity({ sha256: B, ruleset: "brandubh", size: 7 }, active),
    (error) => error.code === "MODEL_IDENTITY_MISMATCH",
  );
});

check("missing expected identity is rejected", () => {
  assert.throws(
    () => identity.validateModelIdentity(null, active),
    (error) => error.code === "MODEL_IDENTITY_REQUIRED",
  );
  assert.throws(
    () => identity.validateModelIdentity({ ruleset: "brandubh", size: 7 }, active),
    (error) => error.code === "MODEL_IDENTITY_INVALID",
  );
});

check("incorrect ruleset is rejected", () => {
  assert.throws(
    () => identity.validateModelIdentity({ sha256: A, ruleset: "tablut", size: 9 }, active),
    (error) => error.code === "RULESET_MISMATCH",
  );
});

check("incorrect board size is rejected", () => {
  assert.throws(
    () => identity.validateModelIdentity({ sha256: A, ruleset: "brandubh", size: 9 }, active),
    (error) => error.code === "BOARD_SIZE_MISMATCH",
  );
});

check("missing active session is rejected", () => {
  assert.throws(
    () => identity.validateModelIdentity({ sha256: A, ruleset: "brandubh", size: 7 }, null),
    (error) => error.code === "MODEL_NOT_LOADED",
  );
});

check("active identity is captured explicitly", () => {
  assert.deepEqual(identity.identifyActive({ modelSha256: A, ruleset: "brandubh", size: 7 }), active);
  assert.equal(identity.identifyActive(null), null);
});

const failures = results.filter((row) => !row.ok);
console.log(`\n${results.length - failures.length}/${results.length} identity checks passed`);
if (failures.length) process.exit(1);
