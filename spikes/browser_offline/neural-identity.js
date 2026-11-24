/* Request-side model-identity validation shared by the neural worker and tests.
 *
 * Inference requests must name the exact model they were prepared for: an
 * immutable content digest, ruleset and board size. A delayed request can never
 * be evaluated against a different active session, even when both models share
 * an architecture and ruleset.
 */
(function attach(root) {
  const RULESET_SIZES = { brandubh: 7, tablut: 9, copenhagen: 11 };
  const HEX64 = /^[0-9a-f]{64}$/;

  function identityError(code, message) {
    return Object.assign(new Error(message), { code });
  }

  function normalizeExpectedModel(model) {
    if (!model || typeof model !== "object")
      throw identityError("MODEL_IDENTITY_REQUIRED", "An expected model identity is required");
    const { sha256, ruleset, size } = model;
    if (typeof sha256 !== "string" || !HEX64.test(sha256))
      throw identityError(
        "MODEL_IDENTITY_INVALID",
        "Expected model sha256 must be a 64-character lowercase hex digest",
      );
    if (typeof ruleset !== "string" || !(ruleset in RULESET_SIZES))
      throw identityError("RULESET_MISMATCH", `Unsupported expected ruleset: ${ruleset}`);
    if (size !== RULESET_SIZES[ruleset])
      throw identityError(
        "BOARD_SIZE_MISMATCH",
        `Expected size ${size} does not match ruleset ${ruleset}`,
      );
    return { sha256, ruleset, size };
  }

  function validateModelIdentity(expected, active) {
    const wanted = normalizeExpectedModel(expected);
    if (!active) throw identityError("MODEL_NOT_LOADED", "No model session is loaded");
    if (active.modelSha256 !== wanted.sha256)
      throw identityError(
        "MODEL_IDENTITY_MISMATCH",
        `Requested model ${wanted.sha256} does not match active model ${active.modelSha256}`,
      );
    if (active.ruleset !== wanted.ruleset)
      throw identityError(
        "RULESET_MISMATCH",
        `Requested ruleset ${wanted.ruleset} does not match active ${active.ruleset}`,
      );
    if (active.size !== wanted.size)
      throw identityError(
        "BOARD_SIZE_MISMATCH",
        `Requested size ${wanted.size} does not match active ${active.size}`,
      );
    return wanted;
  }

  function identifyActive(model) {
    if (!model) return null;
    return {
      modelSha256: model.modelSha256,
      ruleset: model.ruleset,
      size: model.size,
    };
  }

  const api = { RULESET_SIZES, normalizeExpectedModel, validateModelIdentity, identifyActive };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.__neuralIdentity = api;
})(typeof self !== "undefined" ? self : typeof globalThis !== "undefined" ? globalThis : null);
