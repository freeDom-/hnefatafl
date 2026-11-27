import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_RETIREMENTS,
  __resetRetirements,
  addRetirement,
  reconcileRetirements,
  retirementCount,
  retirementFor,
  retirementKey,
  settleRetirement,
} from './analysisRetirements';

function record(receipt: string) {
  return {
    receipt,
    epoch: 'epoch',
    createdAt: 1000,
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

  it('reconciles terminal, expired-floor and newer-epoch receipts without revisiting games', async () => {
    const terminalKey = retirementKey('account-1', '/games', 'g1', 0, 0, 1);
    const floorKey = retirementKey('account-1', '/games', 'g2', 0, 0, 1);
    const epochKey = retirementKey('account-1', '/games', 'g3', 0, 0, 1);
    const activeKey = retirementKey('account-1', '/games', 'g4', 0, 0, 1);
    addRetirement(terminalKey, record('terminal'));
    addRetirement(floorKey, { ...record('floor'), createdAt: 500 });
    addRetirement(epochKey, { ...record('epoch'), epoch: 'old-epoch' });
    addRetirement(activeKey, record('active'));
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const id = decodeURIComponent(url.split('/').pop() ?? '');
        const statuses: Record<string, Record<string, unknown>> = {
          terminal: { state: 'completed', terminal: true },
          floor: { state: 'unknown', terminal: false },
          epoch: { state: 'unknown', terminal: false },
          active: { state: 'active', terminal: false },
        };
        return {
          ok: true,
          json: async () => ({
            schema: 1,
            receipt: id,
            epoch: id === 'epoch' ? 'new-epoch' : 'epoch',
            reconciled_before: id === 'floor' ? 600 : 0,
            ...statuses[id],
          }),
        } as Response;
      }),
    );
    expect(await reconcileRetirements('account-1')).toBe(3);
    expect(retirementFor(terminalKey)).toBeUndefined();
    expect(retirementFor(floorKey)).toBeUndefined();
    expect(retirementFor(epochKey)).toBeUndefined();
    expect(retirementFor(activeKey)).toBeDefined();
    vi.unstubAllGlobals();
  });

  it('does not treat malformed status or another account as terminal evidence', async () => {
    const key = retirementKey('account-1', '/games', 'g1', 0, 0, 1);
    const other = retirementKey('account-2', '/games', 'g2', 0, 0, 1);
    addRetirement(key, record('receipt'));
    addRetirement(other, { ...record('foreign'), account: 'account-2' });
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          ({
            ok: true,
            json: async () => ({ active: false }),
          }) as Response,
      ),
    );
    expect(await reconcileRetirements('account-1')).toBe(0);
    expect(retirementFor(key)).toBeDefined();
    expect(retirementFor(other)).toBeDefined();
    vi.unstubAllGlobals();
  });

  it('recovers bounded capacity after repeated legitimate completed fallbacks', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const id = decodeURIComponent(url.split('/').pop() ?? '');
        return {
          ok: true,
          json: async () => ({
            schema: 1,
            receipt: id,
            epoch: 'epoch',
            reconciled_before: 0,
            state: 'completed',
            terminal: true,
          }),
        } as Response;
      }),
    );
    for (let index = 0; index < MAX_RETIREMENTS; index += 1) {
      addRetirement(
        retirementKey('account-1', '/games', `g${index}`, 0, 0, 1),
        record(`r${index}`),
      );
    }
    expect(retirementCount()).toBe(MAX_RETIREMENTS);
    await reconcileRetirements('account-1');
    expect(retirementCount()).toBe(0);
    expect(addRetirement(retirementKey('account-1', '/games', 'new', 0, 0, 1), record('new'))).toBe(
      true,
    );
    vi.unstubAllGlobals();
  });
});
