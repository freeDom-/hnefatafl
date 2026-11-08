import { expect, it } from 'vitest';
import { accountAttribution, accountName } from './accountIdentity';

it.each(['Odin', 'Þór 東京', '😀'.repeat(80)])('attributes non-unique shared name %s', (name) => {
  expect(accountName({ name, username: 'Archer' })).toBe(`${name} (@Archer)`);
  expect(accountName({ name, username: 'Keeper' })).toBe(`${name} (@Keeper)`);
});
it('avoids duplicate labels and accepts older responses and unassigned seats', () => {
  expect(accountAttribution({ name: 'Odin', username: 'Odin' })).toBe('');
  expect(accountName({ name: 'Odin', username: 'Odin' })).toBe('Odin');
  expect(accountName({ name: 'Legacy name' })).toBe('Legacy name');
  expect(accountName(null)).toBeUndefined();
});
