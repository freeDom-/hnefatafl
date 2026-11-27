import { afterEach, describe, expect, it } from 'vitest';
import {
  MAX_RETIREMENTS,
  __resetRetirements,
  addRetirement,
  retirementCount,
  retirementFor,
  retirementKey,
  settleRetirement,
} from './analysisRetirements';

function record(receipt: string) {
  return {
    receipt,
    epoch: 'epoch',
    account: 'account-1',
    gameId: 'g1',
    basePath: '/games',
    version: 0,
    cursor: 0,
    revision: 1,
  };
}

afterEach(() => __resetRetirements());

describe('analysis retirement records', () => {
  it('are bounded without evicting older potentially active work', () => {
    for (let index = 0; index < MAX_RETIREMENTS; index += 1) {
      expect(
        addRetirement(retirementKey('a', '/games', `g${index}`, 0, 0, 1), record(`r${index}`)),
      ).toBe(true);
    }
    expect(
      addRetirement(retirementKey('a', '/games', 'overflow', 0, 0, 1), record('overflow')),
    ).toBe(false);
    expect(retirementCount()).toBe(MAX_RETIREMENTS);
    expect(retirementFor(retirementKey('a', '/games', 'g0', 0, 0, 1))?.receipt).toBe('r0');
  });

  it('are removed only by the exact receipt with explicit evidence', () => {
    const key = retirementKey('a', '/games', 'g1', 0, 0, 1);
    addRetirement(key, record('receipt'));
    expect(settleRetirement(key, 'other')).toBe(false);
    expect(retirementCount()).toBe(1);
    expect(settleRetirement(key, 'receipt')).toBe(true);
    expect(retirementCount()).toBe(0);
  });

  it('keeps records independent per account and position identity', () => {
    const first = retirementKey('a', '/games', 'g1', 0, 0, 1);
    const second = retirementKey('b', '/games', 'g1', 0, 0, 1);
    expect(addRetirement(first, record('first'))).toBe(true);
    expect(addRetirement(second, record('second'))).toBe(true);
    expect(retirementFor(first)?.receipt).toBe('first');
    expect(retirementFor(second)?.receipt).toBe('second');
  });
});
