/* Output-tensor validation, extraction and guaranteed disposal.
 *
 * Classic script so the worker can importScripts it; also exported for Node
 * unit tests with instrumented tensor objects. The worker must dispose every
 * output tensor even when validation or postprocessing throws, so disposal is a
 * separate operation the caller performs in a finally block.
 */
(function attach(root) {
  function invalid(message) {
    return Object.assign(new Error(message), { code: "OUTPUT_INVALID" });
  }

  function validateOutputs(output, batch, size) {
    if (!output || typeof output !== "object") throw invalid("Model produced no output object");
    const policy = output["policy_logits"];
    const value = output["defender_value"];
    if (!policy || !value) throw invalid("Model output is missing a policy or value tensor");
    if (policy.type !== "float32" || value.type !== "float32")
      throw invalid("Model outputs must be float32");
    const row = size ** 4;
    if (!Array.isArray(policy.dims) || policy.dims.length !== 2)
      throw invalid(`Unexpected policy rank ${JSON.stringify(policy.dims)}`);
    if (policy.dims[0] !== batch || policy.dims[1] !== row)
      throw invalid(`Unexpected policy dims ${JSON.stringify(policy.dims)} for batch ${batch}`);
    if (!Array.isArray(value.dims) || value.dims.length !== 1 || value.dims[0] !== batch)
      throw invalid(`Unexpected value dims ${JSON.stringify(value.dims)} for batch ${batch}`);
    if (policy.data.length !== batch * row || value.data.length !== batch)
      throw invalid("Model output element count is inconsistent with its dims");
    for (let index = 0; index < policy.data.length; index += 1)
      if (!Number.isFinite(policy.data[index]))
        throw invalid("Model produced a non-finite policy logit");
    for (let index = 0; index < value.data.length; index += 1) {
      if (!Number.isFinite(value.data[index]))
        throw invalid("Model produced a non-finite defender value");
      if (value.data[index] < -1 - 1e-6 || value.data[index] > 1 + 1e-6)
        throw invalid("Model produced a defender value outside [-1, 1]");
    }
    return { policy, value };
  }

  function legalSoftmax(logits, legal) {
    const values = legal.map((index) => logits[index]);
    if (legal.some((index, position) => !Number.isFinite(values[position])))
      throw invalid("Policy logits contain a non-finite value");
    const maximum = Math.max(...values);
    const exponentials = values.map((value) => Math.exp(value - maximum));
    const total = exponentials.reduce((sum, value) => sum + value, 0);
    if (!Number.isFinite(total) || total <= 0)
      throw invalid("Legal softmax normaliser is not finite and positive");
    return exponentials.map((value) => value / total);
  }

  // Compact legal-only extraction. MCTS consumes normalized legal probabilities
  // and a value, never the full policy row. Reading directly from the output
  // tensor avoids allocating and transferring policy_logits (65,536 values for
  // Brandubh) on every simulation.
  function legalSoftmaxAt(data, offset, legal) {
    const values = legal.map((index) => data[offset + index]);
    if (values.some((value) => !Number.isFinite(value)))
      throw invalid("Policy logits contain a non-finite value");
    const maximum = Math.max(...values);
    const exponentials = values.map((value) => Math.exp(value - maximum));
    const total = exponentials.reduce((sum, value) => sum + value, 0);
    if (!Number.isFinite(total) || total <= 0)
      throw invalid("Legal softmax normaliser is not finite and positive");
    return exponentials.map((value) => value / total);
  }

  function illegalMass(logits, legal) {
    const maximum = Math.max(...logits);
    const exponentials = logits.map((value) => Math.exp(value - maximum));
    const total = exponentials.reduce((sum, value) => sum + value, 0);
    const legalSet = new Set(legal);
    let sum = 0;
    for (let index = 0; index < exponentials.length; index += 1)
      if (!legalSet.has(index)) sum += exponentials[index];
    return sum / total;
  }

  function extractResults(output, batch, size, cases, { includeLogits = true } = {}) {
    const { policy, value } = validateOutputs(output, batch, size);
    const row = size ** 4;
    const results = cases.map((entry, batchIndex) => {
      const legal = entry.legal;
      const base = {
        index: batchIndex,
        request_id: entry.request_id,
        position_digest: entry.position_digest,
        value: value.data[batchIndex],
        legal,
      };
      if (!includeLogits) {
        return {
          ...base,
          legal_probabilities: legalSoftmaxAt(policy.data, batchIndex * row, legal),
        };
      }
      const logits = Array.from(policy.data.subarray(batchIndex * row, (batchIndex + 1) * row));
      return {
        ...base,
        policy_logits: logits,
        legal_probabilities: legalSoftmax(logits, legal),
        illegal_mass_raw: illegalMass(logits, legal),
      };
    });
    return {
      results,
      policy_shape: [...policy.dims],
      value_shape: [...value.dims],
      policy_dtype: policy.type,
      value_dtype: value.type,
    };
  }

  function disposeTensors(output) {
    const errors = [];
    if (!output || typeof output !== "object") return errors;
    for (const tensor of Object.values(output)) {
      if (!tensor || typeof tensor.dispose !== "function") continue;
      try {
        tensor.dispose();
      } catch (error) {
        errors.push(String(error?.message ?? error));
      }
    }
    return errors;
  }

  const api = {
    validateOutputs,
    extractResults,
    disposeTensors,
    legalSoftmax,
    legalSoftmaxAt,
    illegalMass,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.__neuralTensors = api;
})(typeof self !== "undefined" ? self : typeof globalThis !== "undefined" ? globalThis : null);
