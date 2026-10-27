import { expect, it } from 'vitest';
import { formatRemaining, remainingSeconds } from './remainingTime';

it.each([
  [0, '0s'],
  [-5, '0s'],
  [45, '45s'],
  [59, '59s'],
  [60, '1m 00s'],
  [125, '2m 05s'],
  [3599, '59m 59s'],
  [3600, '1h 00m 00s'],
  [3725, '1h 02m 05s'],
  [86399, '23h 59m 59s'],
  [86400, '1d 00h 00m 00s'],
  [93725, '1d 02h 02m 05s'],
])('formats %i seconds as %s', (seconds, expected) => {
  expect(formatRemaining(seconds)).toBe(expected);
});

it('clamps expired and missing deadlines to zero', () => {
  const now = Date.parse('2035-01-01T00:00:00Z');
  expect(remainingSeconds('2035-01-01T00:00:45Z', now)).toBe(45);
  expect(remainingSeconds('2034-12-31T23:59:00Z', now)).toBe(0);
  expect(remainingSeconds(null, now)).toBe(0);
  expect(remainingSeconds('not-a-date', now)).toBe(0);
});
