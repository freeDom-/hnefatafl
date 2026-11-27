import { describe, expect, it } from 'vitest';
import vectors from './identityVectors.json';
import { canonicalDigest, searchIdentity } from './canonical';

describe('cross-language canonical identity vectors', () => {
  it.each(vectors.cases)('$label matches the Python identity', async (vector) => {
    const state = vector.state as Record<string, unknown>;
    const settings = vector.settings as Record<string, unknown>;
    const actual =
      vector.kind === 'jarl'
        ? await canonicalDigest({ state, settings, protocol: 1 })
        : await searchIdentity(
            state,
            settings,
            vector.model as { onnxSha256: string; ruleset: string; boardSize: number },
            vector.provider,
          );
    expect(actual).toBe(vector.sha256);
  });
});
