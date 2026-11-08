import { expect, it } from 'vitest';
import { validatePassword, validatePasswordLength } from './passwordPolicy';

it.each([
  'abcd'.repeat(3),
  '😀😁😂😃'.repeat(3),
  'abcd'.repeat(64),
  '😀😁😂😃'.repeat(64),
  'e\u0301😀🔑'.repeat(64),
])('accepts the unchanged policy in code points (case %#)', (password) => {
  expect(() => validatePassword(password)).not.toThrow();
  expect(() => validatePasswordLength(password)).not.toThrow();
});
it.each([
  'abcdabcdabc',
  '😀😁😂😃😁😂',
  'abcd'.repeat(64) + 'x',
  '😀😁😂😃'.repeat(64) + 'x',
  'a'.repeat(12),
])('rejects too-short, oversized or insufficiently varied new passwords (case %#)', (password) => {
  expect(() => validatePassword(password)).toThrow('12–256 characters');
});
it('bounds existing credentials without imposing a new minimum or normalizing text', () => {
  expect(() => validatePasswordLength('a')).not.toThrow();
  expect(() => validatePasswordLength('😀'.repeat(256))).not.toThrow();
  expect(() => validatePasswordLength('😀'.repeat(257))).toThrow('at most 256');
  const decomposed = 'e\u0301😀🔑'.repeat(64);
  expect([...decomposed]).toHaveLength(256);
  expect([...decomposed.normalize('NFC')]).toHaveLength(192);
  expect(() => validatePassword(decomposed + 'x')).toThrow('12–256');
});
