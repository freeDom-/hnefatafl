import { expect, it } from 'vitest';
import { ConfigImportError, parseImportedConfig, readImportedConfig } from './trainingConfigImport';
import { trainingConfig } from './trainingPresets';

const base = trainingConfig('tablut', 'recommended');
const file = (value: unknown, name = 'config.json') =>
  new File([JSON.stringify(value)], name, { type: 'application/json' });

it('maps a flat exporter-compatible train config onto the form', () => {
  const exported = {
    ...base,
    channels: 12,
    blocks: 3,
    iterations: 7,
    batch_size: 19,
    seed: 0,
    save_all_best: false,
    dirichlet_fraction: 0,
    model_name: 'Imported raven',
  };
  const result = parseImportedConfig(exported, base, { mode: 'create' });
  expect(result.kind).toBe('train');
  expect(result.config).toMatchObject({
    channels: 12,
    blocks: 3,
    iterations: 7,
    batch_size: 19,
    seed: 0,
    save_all_best: false,
    dirichlet_fraction: 0,
    model_name: 'Imported raven',
  });
  expect(result.warnings).toEqual([]);
  expect(result.ignored).toEqual([]);
});

it('reads and maps a nested optimization config with its study fields', () => {
  const search = { learning_rate: { type: 'float', low: 0.0001, high: 0.01, log: true } };
  const exported = {
    trials: 7,
    study_name: 'raven-search',
    seed: 5,
    search_space: search,
    training: { ...base, batch_size: 7 },
    output_dir: '/srv/jobs/abc',
    storage: 'sqlite:////srv/jobs/abc/study.sqlite3',
  };
  const result = parseImportedConfig(exported, base, { mode: 'create' });
  expect(result.kind).toBe('tune');
  expect(result.config.batch_size).toBe(7);
  expect(result.trials).toBe(7);
  expect(result.studyName).toBe('raven-search');
  expect(result.studySeed).toBe(5);
  expect(result.search).toEqual(search);
  expect(result.ignored).toEqual(expect.arrayContaining(['output_dir', 'storage']));
});

it('preserves form values omitted from a partial config', () => {
  const previous = { ...base, model_name: 'Keep me', batch_size: 99, save_all_best: true };
  const result = parseImportedConfig({ iterations: 3 }, previous, { mode: 'create' });
  expect(result.config.model_name).toBe('Keep me');
  expect(result.config.batch_size).toBe(99);
  expect(result.config.save_all_best).toBe(true);
  expect(result.config.iterations).toBe(3);
});

it('ignores identifiers, paths, status and other non-training metadata', () => {
  const result = parseImportedConfig(
    {
      ...base,
      output_dir: '/srv/jobs/abc',
      registry_dir: '/srv/models',
      resume: 'abc:last.pt',
      status: 'completed',
      model_id: 'a'.repeat(24),
      provenance: { original_job_id: 'b'.repeat(32) },
    },
    base,
    { mode: 'create' },
  );
  expect(result.config).not.toHaveProperty('output_dir');
  expect(result.config).not.toHaveProperty('resume');
  expect(result.ignored).toEqual(
    expect.arrayContaining([
      'output_dir',
      'registry_dir',
      'resume',
      'status',
      'model_id',
      'provenance',
    ]),
  );
});

it.each([
  ['not an object', 42],
  ['an array', []],
])('rejects %s as a configuration', (_label, value) => {
  expect(() => parseImportedConfig(value, base, { mode: 'create' })).toThrow(ConfigImportError);
});

it.each([
  ['unknown ruleset', { ruleset: 'atlantis' }],
  ['out-of-range channels', { channels: 9999 }],
  ['wrong type for a boolean', { mixed_precision: 'yes' }],
  ['empty model name', { model_name: '   ' }],
  ['fractional integer field', { batch_size: 2.5 }],
  ['invalid optimizer', { optimizer: 'rmsprop' }],
])('rejects a recognized parameter with %s', (_label, patch) => {
  const error = (() => {
    try {
      parseImportedConfig({ ...base, ...patch }, base, { mode: 'create' });
    } catch (caught) {
      return caught as ConfigImportError;
    }
    return null;
  })();
  expect(error).toBeInstanceOf(ConfigImportError);
  expect(error?.fields.length).toBeGreaterThan(0);
});

it('rejects malformed JSON and unreadable files without partial state', async () => {
  await expect(
    readImportedConfig(file('{not json'), base, { mode: 'create' }),
  ).rejects.toBeInstanceOf(ConfigImportError);
  await expect(
    readImportedConfig(new File(['x'.repeat(2 * 1024 * 1024)], 'huge.json'), base, {
      mode: 'create',
    }),
  ).rejects.toBeInstanceOf(ConfigImportError);
});

it('keeps checkpoint identity on resume and reports the overrides it refused', () => {
  const checkpoint = {
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    seed: 918,
    optimizer: 'adamw',
  };
  const previous = {
    ...trainingConfig('brandubh', 'smoke'),
    ...checkpoint,
    iterations: 10,
    batch_size: 8,
  };
  const imported = {
    ...previous,
    channels: 12,
    blocks: 3,
    seed: 5,
    optimizer: 'sgd',
    batch_size: 17,
    iterations: 4,
  };
  const result = parseImportedConfig(imported, previous, {
    mode: 'resume',
    immutable: checkpoint,
    minimumIterations: 6,
  });
  expect(result.config).toMatchObject({
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    seed: 918,
    optimizer: 'adamw',
    batch_size: 17,
    iterations: 10,
  });
  expect(result.warnings).toEqual(
    expect.arrayContaining(['channels', 'blocks', 'seed', 'optimizer']),
  );
  expect(result.warnings).not.toContain('iterations');
  expect(result.targetKept).toBe(true);
});

it('applies a matching immutable value and a valid target without warnings', () => {
  const checkpoint = { ruleset: 'brandubh', channels: 4, blocks: 0, seed: 17, optimizer: 'adamw' };
  const previous = {
    ...trainingConfig('brandubh', 'smoke'),
    ...checkpoint,
    iterations: 6,
    batch_size: 8,
  };
  const result = parseImportedConfig({ ...previous, iterations: 12, batch_size: 16 }, previous, {
    mode: 'resume',
    immutable: checkpoint,
    minimumIterations: 6,
  });
  expect(result.config.iterations).toBe(12);
  expect(result.config.batch_size).toBe(16);
  expect(result.warnings).toEqual([]);
  expect(result.targetKept).toBe(false);
});
