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
