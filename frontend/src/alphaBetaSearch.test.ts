import { describe, expect, it } from 'vitest';
import {
  ALPHA_BETA_PRESETS,
  DEFAULT_ALPHA_BETA_SEARCH,
  TABLE_CAPACITY_MAX,
  effectiveSearch,
  isAlphaBetaSearch,
  presetSearch,
  searchFromConfig,
  searchPreset,
  searchSummary,
} from './alphaBetaSearch';
import { parameterSummary } from './RecordsParameters';

describe('alpha-beta search helpers', () => {
  it('accepts only complete, bounded search configurations', () => {
    expect(isAlphaBetaSearch(DEFAULT_ALPHA_BETA_SEARCH)).toBe(true);
    expect(TABLE_CAPACITY_MAX).toBe(500_000);
    expect(isAlphaBetaSearch({ ...DEFAULT_ALPHA_BETA_SEARCH, table_capacity: 0 })).toBe(false);
    expect(
      isAlphaBetaSearch({ ...DEFAULT_ALPHA_BETA_SEARCH, table_capacity: TABLE_CAPACITY_MAX }),
    ).toBe(true);
    expect(
      isAlphaBetaSearch({ ...DEFAULT_ALPHA_BETA_SEARCH, table_capacity: TABLE_CAPACITY_MAX + 1 }),
    ).toBe(false);
    expect(isAlphaBetaSearch({ ...DEFAULT_ALPHA_BETA_SEARCH, legacy: 1 })).toBe(false);
    expect(isAlphaBetaSearch({ legacy: true })).toBe(false);
    expect(isAlphaBetaSearch(null)).toBe(false);
    expect(isAlphaBetaSearch([])).toBe(false);
  });

  it('reads a nested search object from a loose config', () => {
    expect(searchFromConfig({ algorithm: 'alpha_beta' })).toBeUndefined();
    expect(searchFromConfig({ search: { legacy: true } })).toBeUndefined();
    expect(searchFromConfig({ search: ALPHA_BETA_PRESETS.symmetry })).toEqual(
      ALPHA_BETA_PRESETS.symmetry,
    );
  });

  it('identifies and applies presets without mutating the input', () => {
    for (const name of ['legacy', 'optimized', 'symmetry'] as const) {
      expect(searchPreset(ALPHA_BETA_PRESETS[name])).toBe(name);
    }
    expect(searchPreset({ ...DEFAULT_ALPHA_BETA_SEARCH, table_capacity: 7 })).toBe('custom');
    const current = { ...DEFAULT_ALPHA_BETA_SEARCH, table_capacity: 7 };
    const next = presetSearch('legacy', current);
    expect(next).toEqual(ALPHA_BETA_PRESETS.legacy);
    expect(current.table_capacity).toBe(7);
    expect(presetSearch('custom', current)).toEqual(current);
  });

  it('summarises the active optimizations', () => {
    expect(searchSummary(ALPHA_BETA_PRESETS.legacy)).toBe('Legacy search');
    expect(searchSummary(ALPHA_BETA_PRESETS.optimized)).toContain('Transposition table');
    expect(searchSummary(ALPHA_BETA_PRESETS.symmetry)).toContain('Symmetry pruning');
  });

  it('collapses legacy over stale optimization switches like the server', () => {
    const stale = {
      ...DEFAULT_ALPHA_BETA_SEARCH,
      legacy: true,
      symmetry_transposition: true,
      symmetry_pruning: true,
      table_capacity: 7,
    };
    expect(effectiveSearch(stale)).toEqual(ALPHA_BETA_PRESETS.legacy);
    expect(searchPreset(stale)).toBe('legacy');
    // Reading a stored config normalises it so the form never displays an
    // ignored optimization as active.
    expect(searchFromConfig({ search: stale })).toEqual(ALPHA_BETA_PRESETS.legacy);
    expect(effectiveSearch(ALPHA_BETA_PRESETS.optimized)).toEqual(ALPHA_BETA_PRESETS.optimized);
  });

  it('returns the canonical legacy shape when the legacy preset is applied', () => {
    const stale = { ...DEFAULT_ALPHA_BETA_SEARCH, symmetry_pruning: true };
    expect(presetSearch('legacy', stale)).toEqual(ALPHA_BETA_PRESETS.legacy);
  });
});

describe('parameter summary', () => {
  it('describes nested alpha-beta search without an object placeholder', () => {
    const summary = parameterSummary({
      algorithm: 'alpha_beta',
      depth: 3,
      time_limit: 1,
      search: ALPHA_BETA_PRESETS.symmetry,
    });
    expect(summary).toContain('Alpha-beta');
    expect(summary).toContain('Symmetry pruning');
    expect(summary).not.toContain('[object Object]');
    expect(summary).toContain('depth 3');
  });
});
