import { expect, it } from 'vitest';
import corpus from '../../tests/fixtures/profile_text.json';
import { profileText } from './profileText';

it.each(corpus.valid)(
  'preserves NFC multilingual text and emoji: $input',
  ({ input, normalized }) => {
    expect(profileText(input, 80)).toBe(normalized);
    expect(profileText(input, 100)).toBe(normalized);
  },
);
it.each(corpus.rejected_code_points)('rejects formatting/control U+%i in either field', (point) => {
  const value = 'Name' + String.fromCodePoint(point) + 'City';
  for (const limit of [80, 100])
    expect(() => profileText(value, limit)).toThrow('control characters');
});
it('retains code-point length limits after NFC normalization and trimming', () => {
  expect(profileText('  ' + 'e\u0301'.repeat(80) + '  ', 80)).toBe('é'.repeat(80));
  expect(() => profileText('é'.repeat(81), 80)).toThrow('80 characters');
  expect(profileText('京'.repeat(100), 100)).toHaveLength(100);
  expect(() => profileText('京'.repeat(101), 100)).toThrow('100');
});

it.each([
  ['京', 80],
  ['😀', 80],
  ['e\u0301', 80],
  ['👩\u200d🚀', 80],
  ['京', 100],
  ['😀', 100],
  ['e\u0301', 100],
  ['👩\u200d🚀', 100],
] as const)('uses normalized code points for %s at the exact %i boundary', (unit, limit) => {
  const points = [...unit.normalize('NFC')].length;
  const raw = unit.repeat(Math.floor(limit / points)) + 'x'.repeat(limit % points);
  const normalized = raw.normalize('NFC');
  expect([...normalized]).toHaveLength(limit);
  expect(profileText('  ' + raw + '  ', limit)).toBe(normalized);
  expect(() => profileText(raw + 'x', limit)).toThrow('80 characters');
});
