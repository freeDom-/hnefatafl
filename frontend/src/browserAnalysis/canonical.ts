/**
 * Cross-language canonical identity for local search results.
 *
 * Python produces the server position key and the Pyodide bridge identity with
 * sorted JSON and no insignificant whitespace. Keep the same float spelling for
 * known floating-point settings so JavaScript and Python agree on the digest.
 */

export const CANONICAL_FLOAT_KEYS = new Set(['time_limit', 'exploration', 'temperature']);

export function canonicalJson(
  value: unknown,
  floatKeys: Set<string> = CANONICAL_FLOAT_KEYS,
  keyName?: string,
): string {
  if (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    keyName !== undefined &&
    floatKeys.has(keyName)
  ) {
    return `${value}.0`;
  }
  if (Array.isArray(value))
    return `[${value.map((item) => canonicalJson(item, floatKeys)).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${canonicalJson(
            (value as Record<string, unknown>)[key],
            floatKeys,
            key,
          )}`,
      )
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

export async function canonicalDigest(value: unknown): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error('Canonical position verification is unavailable');
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(canonicalJson(value)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** Canonical state/effective-settings/model/provider binding for a search. */
export async function searchIdentity(
  state: Record<string, unknown>,
  settings: Record<string, unknown>,
  model: { onnxSha256: string; ruleset: string; boardSize: number } | null,
  provider: string | null,
): Promise<string> {
  return canonicalDigest({
    state,
    settings,
    ...(model
      ? {
          model: {
            sha256: model.onnxSha256,
            ruleset: model.ruleset,
            size: model.boardSize,
          },
        }
      : {}),
    ...(provider ? { provider } : {}),
    protocol: 1,
  });
}
