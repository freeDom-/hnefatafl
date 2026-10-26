/**
 * Structured alpha-beta search settings shared by the tournament setup form,
 * config import/export, records and persisted game preferences.
 *
 * The server validates and normalises the same shape. These helpers never
 * mutate their input and never invent fields the server would reject.
 */
export interface AlphaBetaSearchSettings {
  legacy: boolean;
  transposition: boolean;
  move_ordering: boolean;
  symmetry_transposition: boolean;
  symmetry_pruning: boolean;
  table_capacity: number;
}

export const TABLE_CAPACITY_MIN = 1;
export const TABLE_CAPACITY_MAX = 5_000_000;

export const DEFAULT_ALPHA_BETA_SEARCH: AlphaBetaSearchSettings = {
  legacy: false,
  transposition: true,
  move_ordering: true,
  symmetry_transposition: false,
  symmetry_pruning: false,
  table_capacity: 100_000,
};

export type AlphaBetaPresetName = 'legacy' | 'optimized' | 'symmetry' | 'custom';

export const ALPHA_BETA_PRESETS: Record<
  Exclude<AlphaBetaPresetName, 'custom'>,
  AlphaBetaSearchSettings
> = {
  legacy: { ...DEFAULT_ALPHA_BETA_SEARCH, legacy: true },
  optimized: { ...DEFAULT_ALPHA_BETA_SEARCH },
  symmetry: {
    ...DEFAULT_ALPHA_BETA_SEARCH,
    symmetry_transposition: true,
    symmetry_pruning: true,
  },
};

const SEARCH_KEYS: (keyof AlphaBetaSearchSettings)[] = [
  'legacy',
  'transposition',
  'move_ordering',
  'symmetry_transposition',
  'symmetry_pruning',
  'table_capacity',
];

/** Narrow an unknown value to a complete, well-typed search configuration. */
export function isAlphaBetaSearch(value: unknown): value is AlphaBetaSearchSettings {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.legacy === 'boolean' &&
    typeof record.transposition === 'boolean' &&
    typeof record.move_ordering === 'boolean' &&
    typeof record.symmetry_transposition === 'boolean' &&
    typeof record.symmetry_pruning === 'boolean' &&
    typeof record.table_capacity === 'number' &&
    Number.isInteger(record.table_capacity) &&
    record.table_capacity >= TABLE_CAPACITY_MIN &&
    record.table_capacity <= TABLE_CAPACITY_MAX
  );
}

/** Read the nested search object from a loose AI config, if present and valid. */
export function searchFromConfig(config: Record<string, unknown> | undefined) {
  const value = config?.search;
  return isAlphaBetaSearch(value) ? value : undefined;
}

function sameSearch(left: AlphaBetaSearchSettings, right: AlphaBetaSearchSettings) {
  return SEARCH_KEYS.every((key) => left[key] === right[key]);
}

/** Identify which preset a configuration matches, if any. */
export function searchPreset(search: AlphaBetaSearchSettings): AlphaBetaPresetName {
  for (const name of ['legacy', 'optimized', 'symmetry'] as const) {
    if (sameSearch(search, ALPHA_BETA_PRESETS[name])) return name;
  }
  return 'custom';
}

/** Apply a named preset while keeping every field the server understands. */
export function presetSearch(name: AlphaBetaPresetName, current: AlphaBetaSearchSettings) {
  return name === 'custom' ? { ...current } : { ...ALPHA_BETA_PRESETS[name] };
}

/** A short, translatable-free description of the active optimizations. */
export function searchSummary(search: AlphaBetaSearchSettings): string {
  if (search.legacy) return 'Legacy search';
  const parts: string[] = [];
  if (search.symmetry_pruning) parts.push('Symmetry pruning');
  else if (search.symmetry_transposition) parts.push('Canonical transpositions');
  if (search.transposition) parts.push('Transposition table');
  if (search.move_ordering) parts.push('Improved ordering');
  return parts.join(' + ') || 'Plain alpha-beta';
}
