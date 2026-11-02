import { afterEach, expect, it } from 'vitest';
import contract from './trainingContract.json';
import { setLanguage, t } from './i18n';

/**
 * The dashboard renders one label per training phase, taken from the phase name
 * the backend publishes. `trainingContract.json` is the shared vocabulary: the
 * backend test asserts the phases the training code actually publishes, and this
 * test asserts every declared phase is readable in both languages.
 */

/** The display key the dashboard derives from one backend phase name. */
const label = (phase: string) => phase.replaceAll('_', ' ');

afterEach(() => setLanguage('en', false));

it('declares one unique label key per training phase', () => {
  const phases = contract.training_phases as string[];
  expect(phases.length).toBeGreaterThan(0);
  expect(new Set(phases).size).toBe(phases.length);
  for (const phase of phases) expect(phase).toMatch(/^[a-z][a-z_]*$/);
});

it('keeps English as the source and translates every phase in German', () => {
  for (const phase of contract.training_phases) {
    // English shows the phase name itself, exactly as the backend publishes it.
    expect(t(label(phase))).toBe(label(phase));
  }
  setLanguage('de', false);
  const missing: string[] = [];
  for (const phase of contract.training_phases) {
    const german = t(label(phase));
    if (german === label(phase) || !german.trim()) missing.push(label(phase));
  }
  // A single assertion keeps the failure message short and complete.
  expect(missing).toEqual([]);
});

it('shows an unknown legacy phase instead of an empty label', () => {
  setLanguage('de', false);
  // A phase recorded by a newer or older version stays readable.
  expect(t('future phase')).toBe('future phase');
});
