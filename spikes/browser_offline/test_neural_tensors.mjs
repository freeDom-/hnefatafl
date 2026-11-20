// Deterministic tensor-cleaning tests for the neural worker helpers.
//
// The real worker cannot be imported into Node (it uses importScripts/ORT), so
// the output validation/extraction/disposal helpers live in neural-tensors.js
// and are exercised here with instrumented tensor objects.
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const tensors = createRequire(import.meta.url)("./neural-tensors.js");

function fakeTensor({ dims, data, type = "float32", disposeBehaviour = null }) {
  const tensor = {
    type,
    dims: [...dims],
    data: Float32Array.from(data),
    disposed: 0,
    dispose() {
      tensor.disposed += 1;
      if (disposeBehaviour === "throw") throw new Error("dispose failed");
    },
  };
  return tensor;
}

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`ok   ${name}`);
  } catch (error) {
    results.push({ name, ok: false, error: String(error?.message ?? error) });
    console.log(`FAIL ${name}: ${error?.message ?? error}`);
  }
}

const cases = [{ legal: [0, 1], request_id: "p0", position_digest: "a".repeat(64) }];

await check("valid outputs are extracted and each tensor disposed once", () => {
  const policy = fakeTensor({ dims: [1, 16], data: Array.from({ length: 16 }, (_, i) => i / 100) });
  const value = fakeTensor({ dims: [1], data: [0.2] });
  const output = { policy_logits: policy, defender_value: value };
  const extracted = tensors.extractResults(output, 1, 2, cases);
  assert.equal(extracted.results.length, 1);
  assert.equal(extracted.policy_shape[0], 1);
  const errors = tensors.disposeTensors(output);
  assert.deepEqual(errors, []);
  assert.equal(policy.disposed, 1);
  assert.equal(value.disposed, 1);
});

await check("invalid dimensions throw but every output tensor is still disposed", () => {
  const policy = fakeTensor({ dims: [1, 4], data: Array.from({ length: 16 }, (_, i) => i / 100) });
  const value = fakeTensor({ dims: [1], data: [0.2] });
  const output = { policy_logits: policy, defender_value: value };
  assert.throws(() => tensors.extractResults(output, 1, 2, cases), /policy dims/);
  const errors = tensors.disposeTensors(output);
  assert.deepEqual(errors, []);
  assert.equal(policy.disposed, 1);
  assert.equal(value.disposed, 1);
});

await check("non-finite logits throw and all tensors are disposed", () => {
  const policy = fakeTensor({ dims: [1, 16], data: [Number.NaN, ...Array.from({ length: 15 }, (_, i) => i / 100)] });
  const value = fakeTensor({ dims: [1], data: [0.2] });
  const output = { policy_logits: policy, defender_value: value };
  assert.throws(() => tensors.extractResults(output, 1, 2, cases), /non-finite/);
  tensors.disposeTensors(output);
  assert.equal(policy.disposed, 1);
  assert.equal(value.disposed, 1);
});

await check("legal-policy normalisation failure still disposes tensors", () => {
  const policy = fakeTensor({
    dims: [1, 16],
    data: [Number.POSITIVE_INFINITY, ...Array.from({ length: 15 }, (_, i) => i / 100)],
  });
  const value = fakeTensor({ dims: [1], data: [0.2] });
  const output = { policy_logits: policy, defender_value: value };
  assert.throws(() => tensors.extractResults(output, 1, 2, cases));
  tensors.disposeTensors(output);
  assert.equal(policy.disposed, 1);
  assert.equal(value.disposed, 1);
});

await check("a dispose failure is reported and the other tensor is still released", () => {
  const policy = fakeTensor({ dims: [1, 16], data: Array.from({ length: 16 }, (_, i) => i / 100), disposeBehaviour: "throw" });
  const value = fakeTensor({ dims: [1], data: [0.2] });
  const errors = tensors.disposeTensors({ policy_logits: policy, defender_value: value });
  assert.equal(errors.length, 1);
  assert.match(errors[0], /dispose failed/);
  assert.equal(policy.disposed, 1);
  assert.equal(value.disposed, 1);
});

await check("missing output tensors are rejected", () => {
  assert.throws(
    () => tensors.extractResults({ policy_logits: null, defender_value: null }, 1, 2, cases),
    /missing a policy or value tensor/,
  );
});

await check("out-of-range defender value is rejected", () => {
  const policy = fakeTensor({ dims: [1, 16], data: Array.from({ length: 16 }, (_, i) => i / 100) });
  const value = fakeTensor({ dims: [1], data: [1.5] });
  assert.throws(
    () => tensors.extractResults({ policy_logits: policy, defender_value: value }, 1, 2, cases),
    /outside \[-1, 1\]/,
  );
});

const failures = results.filter((row) => !row.ok);
console.log(`\n${results.length - failures.length}/${results.length} tensor-cleanup checks passed`);
if (failures.length) {
  for (const row of failures) console.error(`  - ${row.name}: ${row.error}`);
  process.exit(1);
}
