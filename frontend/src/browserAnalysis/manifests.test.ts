import { describe, expect, it } from 'vitest';

import { BrowserAnalysisError } from './contracts';
import {
  assertModelRuntimeCompatible,
  computeRuntimeId,
  verifyRuntimeIdentity,
  modelArtifactUrl,
  runtimeAssetUrl,
  validateAvailability,
  validateModelCatalog,
  validateModelManifest,
  validateRuntimeManifest,
} from './manifests';
import {
  availabilityWire,
  modelWire,
  nodeDigest,
  runtimeManifest,
  runtimeWire,
} from './testSupport';

function expectCode(fn: () => unknown, code: string) {
  try {
    fn();
  } catch (error) {
    expect(error).toBeInstanceOf(BrowserAnalysisError);
    expect((error as BrowserAnalysisError).code).toBe(code);
    return;
  }
  throw new Error(`expected ${code}`);
}

describe('runtime manifest validation', () => {
  it('accepts the complete pinned manifest', () => {
    const manifest = validateRuntimeManifest(runtimeWire());
    expect(manifest.runtimeId).toBe(runtimeManifest().runtimeId);
    expect(manifest.basePath).toBe(`/browser-analysis/runtime/${manifest.runtimeId}`);
    expect(manifest.files).toHaveLength(23);
    expect(manifest.totalBytes).toBeGreaterThan(0);
  });

  it('rejects unsupported schemas, versions and protocols', () => {
    const wire = runtimeWire();
    expectCode(() => validateRuntimeManifest({ ...wire, schema: 2 }), 'runtime-incompatible');
    const encoder = structuredClone(wire) as any;
    encoder.pins.encoder_version = 'two-plane-v0';
    expectCode(() => validateRuntimeManifest(encoder), 'runtime-incompatible');
    const ort = structuredClone(wire) as any;
    ort.pins.onnxruntime_web = '1.0.0';
    expectCode(() => validateRuntimeManifest(ort), 'runtime-incompatible');
    const protocol = structuredClone(wire) as any;
    protocol.pins.search_protocol = 2;
    expectCode(() => validateRuntimeManifest(protocol), 'runtime-incompatible');
  });

  it('rejects missing, duplicate, unsafe or oversized inventory entries', () => {
    const missing = runtimeWire();
    (missing.files as any[]).pop();
    expectCode(() => validateRuntimeManifest(missing), 'runtime-integrity');

    const duplicate = structuredClone(runtimeWire()) as any;
    duplicate.files.push({ ...duplicate.files[0] });
    expectCode(() => validateRuntimeManifest(duplicate), 'runtime-integrity');

    for (const path of [
      '../runtime/pyodide.js',
      '/etc/passwd',
      'runtime\\pyodide.js',
      'runtime/%2e%2e',
    ]) {
      const wire = structuredClone(runtimeWire()) as any;
      wire.files[0].path = path;
      expectCode(() => validateRuntimeManifest(wire), 'runtime-integrity');
    }

    const digest = structuredClone(runtimeWire()) as any;
    digest.files[0].sha256 = 'x'.repeat(64);
    expectCode(() => validateRuntimeManifest(digest), 'runtime-integrity');

    const oversized = structuredClone(runtimeWire()) as any;
    oversized.files[0].bytes = 1_000_000_000;
    expectCode(() => validateRuntimeManifest(oversized), 'runtime-integrity');
  });

  it('recomputes the server runtime identity from canonical immutable content', async () => {
    const manifest = validateRuntimeManifest(runtimeWire());
    expect(await computeRuntimeId(manifest, nodeDigest)).toBe(manifest.runtimeId);
    await expect(verifyRuntimeIdentity(manifest, nodeDigest)).resolves.toBeUndefined();
    const tampered = {
      ...manifest,
      files: manifest.files.map((entry, index) =>
        index === 0 ? { ...entry, sha256: 'f'.repeat(64) } : entry,
      ),
    };
    expect(await computeRuntimeId(tampered, nodeDigest)).not.toBe(manifest.runtimeId);
    await expect(verifyRuntimeIdentity(tampered, nodeDigest)).rejects.toMatchObject({
      code: 'runtime-integrity',
    });
  });

  it('rejects a runtime without required assets', () => {
    const wire = structuredClone(runtimeWire()) as any;
    wire.files = wire.files.filter((entry: any) => entry.path !== 'search-worker.js');
    expectCode(() => validateRuntimeManifest(wire), 'runtime-integrity');
  });

  it('only builds URLs for manifest members', () => {
    const manifest = validateRuntimeManifest(runtimeWire());
    expect(runtimeAssetUrl(manifest, 'runtime/pyodide.js')).toBe(
      `/browser-analysis/runtime/${manifest.runtimeId}/runtime/pyodide.js`,
    );
    expectCode(() => runtimeAssetUrl(manifest, 'other.js'), 'runtime-integrity');
    expectCode(() => runtimeAssetUrl(manifest, '../bridge.py'), 'runtime-integrity');
  });
});

describe('availability and model manifests', () => {
  it('accepts disabled and enabled availability projections', () => {
    const disabled = validateAvailability(
      availabilityWire(null, { enabled: false, reason: 'disabled' }),
    );
    expect(disabled.enabled).toBe(false);
    expect(disabled.runtime).toBeNull();
    const enabled = validateAvailability(availabilityWire());
    expect(enabled.runtime?.runtimeId).toBe(runtimeManifest().runtimeId);
  });

  it('rejects an enabled response claiming a runtime without a manifest', () => {
    expectCode(
      () =>
        validateAvailability({
          schema: 1,
          enabled: true,
          runtime_available: true,
          runtime: null,
          reason: null,
          published_models: 0,
        }),
      'runtime-incompatible',
    );
  });

  it('accepts a published model manifest and catalog', () => {
    const model = validateModelManifest(modelWire());
    expect(model.publicationId).toBe('0123456789abcdef01234567');
    expect(model.boardSize).toBe(7);
    expect(validateModelCatalog({ schema: 1, models: [modelWire()] })).toHaveLength(1);
    expect(modelArtifactUrl(model)).toBe(
      '/api/browser-analysis/models/0123456789abcdef01234567/artifact',
    );
  });

  it('rejects incompatible or unpublished model manifests', () => {
    const cases: Array<[Record<string, unknown>, string]> = [
      [{ ...modelWire(), status: 'revoked' }, 'model-integrity'],
      [{ ...modelWire(), board_size: 9 }, 'model-integrity'],
      [{ ...modelWire(), encoder_version: 'two-plane-v0' }, 'model-integrity'],
      [{ ...modelWire(), artifact_bytes: 512 * 1024 * 1024 }, 'model-integrity'],
      [{ ...modelWire(), publication_id: '../models' }, 'model-integrity'],
      [{ ...modelWire(), ruleset: 'chess' }, 'model-integrity'],
      [
        {
          ...modelWire(),
          runtime: { ...(modelWire().runtime as object), ort_version: '9.9.9' },
        },
        'model-integrity',
      ],
      [
        {
          ...modelWire(),
          runtime: { ...(modelWire().runtime as object), encoder_version: 'two-plane-v0' },
        },
        'model-integrity',
      ],
    ];
    for (const [wire, code] of cases) {
      expectCode(() => validateModelManifest(wire), code);
    }
  });

  it('refuses a model that is incompatible with the verified runtime', () => {
    const runtime = validateRuntimeManifest(runtimeWire());
    const compatible = validateModelManifest(modelWire());
    expect(() => assertModelRuntimeCompatible(compatible, runtime)).not.toThrow();
    // The manifest validator already rejects a bad encoder; a runtime-level guard
    // still protects Phase 5 from bypassing manifest validation.
    expect(() =>
      assertModelRuntimeCompatible(
        { ...compatible, runtime: { ...compatible.runtime, encoderVersion: 'other' } },
        runtime,
      ),
    ).toThrow();
    expect(() =>
      assertModelRuntimeCompatible(
        { ...compatible, runtime: { ...compatible.runtime, ortVersion: '9.9.9' } },
        runtime,
      ),
    ).toThrow();
  });

  it('never trusts a manifest-supplied base path', () => {
    const wire = runtimeWire();
    (wire as any).base_path = 'https://evil.example/';
    const manifest = validateRuntimeManifest(wire);
    expect(manifest.basePath).toBe(`/browser-analysis/runtime/${manifest.runtimeId}`);
  });
});
