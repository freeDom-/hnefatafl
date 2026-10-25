import { object } from './trainingDashboardData';
import { validTrainingNumbers } from './TrainingFields';
import { isTrainingParameter, validTrainingInput, type TrainingConfig } from './trainingPresets';
import { validSearchSpace } from './trainingReuse';

/** Bound the read of a local config.json before parsing it. */
export const MAX_CONFIG_BYTES = 1024 * 1024;

const TUNE_FIELDS = ['trials', 'study_name', 'seed', 'search_space'];

export class ConfigImportError extends Error {
  fields: string[];

  constructor(message: string, fields: string[] = []) {
    super(message);
    this.name = 'ConfigImportError';
    this.fields = fields;
  }
}

export interface ImportedConfig {
  config: TrainingConfig;
  kind: 'train' | 'tune';
  trials?: number;
  studySeed?: number;
  studyName?: string;
  search?: Record<string, unknown>;
  /** Recognized parameters that could not be applied because the checkpoint fixes them. */
  warnings: string[];
  /** An imported iteration target was ignored because it does not exceed the completed iteration. */
  targetKept: boolean;
  /** Fields with no training meaning: runtime paths, metadata, tune-only settings on resume. */
  ignored: string[];
}

export interface ImportOptions {
  mode: 'create' | 'resume';
  /** Checkpoint-authoritative values that imported parameters must not replace. */
  immutable?: Partial<TrainingConfig>;
  /** Target total iterations must exceed the saved completed iteration. */
  minimumIterations?: number;
}

/**
 * Map one exported config.json (or the equivalent server-side object) onto an
 * editable form. Recognized training parameters are validated against the same
 * schema as recorded-config reuse; every other field is reported, never merged.
 */
export function parseImportedConfig(
  value: unknown,
  previous: TrainingConfig,
  options: ImportOptions,
): ImportedConfig {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ConfigImportError('The configuration must be a JSON object.');
  }
  const root = value as Record<string, unknown>;
  const nested = object(root.training);
  const tuning = nested !== undefined;
  const source = tuning && nested ? nested : root;
  const config: TrainingConfig = { ...previous };
  const warnings: string[] = [];
  const ignored: string[] = [];
  const invalid: string[] = [];
  let targetKept = false;
  for (const [key, raw] of Object.entries(source)) {
    if (!isTrainingParameter(key)) {
      ignored.push(key);
      continue;
    }
    if (!validTrainingInput(key, raw)) {
      invalid.push(key);
      continue;
    }
    if (options.mode === 'resume') {
      if (
        options.immutable &&
        key in options.immutable &&
        raw !== (options.immutable as Record<string, unknown>)[key]
      ) {
        warnings.push(key);
        continue;
      }
      if (key === 'iterations') {
        const minimum = options.minimumIterations ?? 1;
        if (!Number.isInteger(raw) || Number(raw) < minimum) {
          targetKept = true;
          continue;
        }
      }
    }
    config[key] = raw as string | number | boolean;
  }
  if (invalid.length) {
    throw new ConfigImportError('Invalid configuration values.', invalid);
  }
  const result: ImportedConfig = {
    config,
    kind: tuning ? 'tune' : 'train',
    warnings,
    targetKept,
    ignored,
  };
  if (tuning) {
    const invalidTune: string[] = [];
    if (options.mode === 'resume') {
      for (const key of TUNE_FIELDS) {
        if (key in root) ignored.push(key);
      }
    } else {
      if (root.trials !== undefined) {
        const trials = root.trials;
        if (
          typeof trials === 'number' &&
          Number.isInteger(trials) &&
          trials >= 1 &&
          trials <= 10000
        )
          result.trials = trials;
        else invalidTune.push('trials');
      }
      if (root.seed !== undefined) {
        if (typeof root.seed === 'number' && validTrainingNumbers({ seed: root.seed }))
          result.studySeed = root.seed;
        else invalidTune.push('study_seed');
      }
      if (root.study_name !== undefined) {
        const name = root.study_name;
        if (typeof name === 'string' && name.trim() && name.length <= 100) result.studyName = name;
        else invalidTune.push('study_name');
      }
      if (root.search_space !== undefined) {
        const search = object(root.search_space);
        if (search && validSearchSpace(search)) result.search = search;
        else invalidTune.push('search_space');
      }
    }
    if (invalidTune.length) {
      throw new ConfigImportError('Invalid configuration values.', invalidTune);
    }
    for (const key of Object.keys(root)) {
      if (key === 'training' || TUNE_FIELDS.includes(key) || ignored.includes(key)) continue;
      ignored.push(key);
    }
  }
  return result;
}

function readFileText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('The file could not be read.'));
    reader.readAsText(file);
  });
}

/** Read a bounded local JSON file and map it onto the form. */
export async function readImportedConfig(
  file: File,
  previous: TrainingConfig,
  options: ImportOptions,
): Promise<ImportedConfig> {
  if (file.size > MAX_CONFIG_BYTES) {
    throw new ConfigImportError('The configuration file is too large.');
  }
  let text: string;
  try {
    text = await readFileText(file);
  } catch {
    throw new ConfigImportError('The configuration file could not be read.');
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new ConfigImportError('The configuration is not valid JSON.');
  }
  return parseImportedConfig(value, previous, options);
}
