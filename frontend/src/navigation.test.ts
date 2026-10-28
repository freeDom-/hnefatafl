import { afterEach, expect, it } from 'vitest';
import { safeDestination, canonicalLocation } from './navigation';
afterEach(() => history.replaceState(null, '', '/'));
it.each([
  'https://evil.test/archive',
  '//evil.test',
  '/\\evil.test',
  '/unknown',
  '/games/online-abcd',
  '/archive\n',
])('rejects unsafe next destinations: %s', (value) => {
  expect(safeDestination(value)).toBe('/dashboard');
});
it('retains filters, selected record, game deep links and fragment through safe auth navigation', () => {
  expect(safeDestination('/statistics?mode=multiplayer&ruleset=tablut')).toBe(
    '/statistics?mode=multiplayer&ruleset=tablut',
  );
  const next = '/archive?ruleset=tablut&review=online&selected=online-123#review';
  expect(safeDestination(next)).toBe(next);
  expect(safeDestination('/games/owned-id?cursor=3')).toBe('/games/owned-id?cursor=3');
  expect(safeDestination('/online/games/online-' + 'a'.repeat(32) + '?leg=2')).toContain('?leg=2');
});
it('normalizes obsolete entries without losing query or selection', () => {
  history.replaceState(null, '', '/online?mine=true&selected=kept#lobby');
  expect(canonicalLocation()).toBe('/dashboard?mine=true&selected=kept&setup=online');
  expect(location.hash).toBe('#lobby');
});
