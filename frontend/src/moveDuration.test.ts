import { expect, it } from 'vitest';
import { durationParts, moveSeconds } from './moveDuration';
it.each([
  ['0', '0', '1', 60],
  ['0', '1', '0', 3600],
  ['2', '3', '4', 183840],
  ['14', '0', '0', 1209600],
])('converts %s days %s hours %s minutes exactly', (d, h, m, expected) =>
  expect(moveSeconds(d as string, h as string, m as string)).toBe(expected),
);
it.each([
  ['0', '0', '0'],
  ['14', '0', '1'],
  ['15', '0', '0'],
  ['0', '24', '0'],
  ['0', '0', '60'],
  ['-1', '0', '1'],
  ['0', '1.5', '0'],
  ['0', '1e1', '0'],
  ['', '0', '1'],
  ['0', '0', 'NaN'],
])('rejects invalid or out-of-policy duration %j', (d, h, m) =>
  expect(() => moveSeconds(d, h, m)).toThrow(),
);
it('separates display components without discarding a retained sub-minute remainder', () =>
  expect(durationParts(61)).toEqual({ days: '0', hours: '0', minutes: '1', remainder: 1 }));
