import { expect, it } from 'vitest';
import { birthDateError, profileDate } from './profileDate';

it.each(['1900-01-01', '2000-02-29', '2026-11-08'])(
  'accepts complete ISO calendar date %s',
  (value) => {
    expect(profileDate(value, '2026-11-08')).toBe(value);
  },
);
it.each([
  '1899-12-31',
  '1900-02-29',
  '2001-02-29',
  '2026-02-30',
  '2026-13-01',
  '2026-11-09',
  '1990',
  '1990-1-01',
  '19900101',
  '1990-03-15T00:00:00Z',
  ' 1990-03-15',
  '1990-03-15\n',
  '١٩٩٠-٠٣-١٥',
])('rejects malformed, future or unsupported date %s without echoing it', (value) => {
  expect(() => profileDate(value, '2026-11-08')).toThrow(birthDateError);
});
it.each([null, ''])('allows clearing %s', (value) => {
  expect(profileDate(value)).toBeNull();
});
