import { describe, expect, it } from 'vitest';
import { LEGACY_EXECUTION_POLICY, normalizeExecutionPolicy } from './analysisPolicy';

function wire(overrides: Record<string, unknown> = {}) {
  return {
    mode: 'browser_preferred',
    browser_delivery: true,
    browser_execution: true,
    server_computation: true,
    fallback: true,
    webgpu: false,
    offline_authorization_seconds: 900,
    limits: { max_concurrent: 16, max_per_user: 2, requests_per_minute: 30 },
    ...overrides,
  };
}

describe('server execution policy projection', () => {
  it('normalizes every supported mode and derives flags from the mode', () => {
    expect(normalizeExecutionPolicy(wire()).mode).toBe('browser_preferred');
    expect(
      normalizeExecutionPolicy(
        wire({ mode: 'browser_only', fallback: false, server_computation: false }),
      ),
    ).toMatchObject({
      browserExecution: true,
      serverComputation: false,
      fallback: false,
    });
    expect(
      normalizeExecutionPolicy(
        wire({
          mode: 'server_only',
          browser_delivery: true,
          browser_execution: false,
          server_computation: true,
          fallback: false,
        }),
      ),
    ).toMatchObject({ browserDelivery: true, browserExecution: false, fallback: false });
    expect(
      normalizeExecutionPolicy(
        wire({
          mode: 'disabled',
          browser_delivery: false,
          browser_execution: false,
          server_computation: false,
          fallback: false,
        }),
      ).mode,
    ).toBe('disabled');
  });

  it('treats a missing projection as legacy server-only', () => {
    expect(normalizeExecutionPolicy(undefined, { browserDelivery: true })).toEqual({
      ...LEGACY_EXECUTION_POLICY,
      browserDelivery: true,
    });
  });

  it('rejects contradictions instead of guessing', () => {
    expect(() => normalizeExecutionPolicy(wire({ mode: 'auto' }))).toThrow();
    expect(() => normalizeExecutionPolicy(wire({ fallback: false }))).toThrow();
    expect(() => normalizeExecutionPolicy(wire({ server_computation: false }))).toThrow();
    expect(() => normalizeExecutionPolicy(wire({ limits: {} }))).toThrow();
    expect(() => normalizeExecutionPolicy(wire({ offline_authorization_seconds: 10 }))).toThrow();
  });
});
