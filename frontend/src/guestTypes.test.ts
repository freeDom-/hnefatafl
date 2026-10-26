import { describe, it, expect } from 'vitest';
import { MAX_GUEST_BYTES, MAX_GUEST_MOVES, parseGuestRecord } from './guestTypes';
import { readGuestSlot, writeGuestSlot } from './guestStore';
const record = {
  schema: 1,
  engine_version: 'a'.repeat(64),
  ruleset: 'brandubh',
  ruleset_version: '1',
  human_side: 'attackers',
  moves: [{ source: 3, target: 1 }],
  cursor: 1,
};
describe('bounded guest-only persistence envelope', () => {
  it('accepts only the versioned move envelope; the engine still validates legality', () => {
    expect(parseGuestRecord(JSON.stringify(record))).toEqual(record);
    expect(Object.keys(record)).not.toContain('board');
    expect(
      parseGuestRecord(JSON.stringify({ ...record, moves: [{ source: 0, target: 1 }] })),
    ).toBeTruthy();
  });
  it.each([
    { schema: 2 },
    { schema: true },
    { engine_version: 'online-' + 'a'.repeat(32) },
    { ruleset: '../online' },
    { ruleset_version: '1'.repeat(20) },
    { human_side: 'admin' },
    { cursor: -1 },
    { cursor: 2 },
    { cursor: 1.5 },
    { moves: [{ source: true, target: 1 }] },
    { moves: [{ source: 49, target: 0 }] },
    { moves: [{ source: 3.2, target: 1 }] },
    { moves: [{ source: 3, target: 1, actor_id: 'fixture' }] },
    { moves: Array(MAX_GUEST_MOVES + 1).fill({ source: 3, target: 1 }) },
    { board: ['A'] },
    { winner: 'attackers' },
    { id: 'online-' + 'a'.repeat(32) },
    { account_id: 'fixture' },
    { csrf_token: 'secret' },
  ])('rejects unsupported/bad envelope %j', (change) =>
    expect(() => parseGuestRecord(JSON.stringify({ ...record, ...change }))).toThrow(),
  );
  it('rejects corrupt JSON, oversized text and missing fields before replay', () => {
    for (const raw of [
      '{',
      'null',
      '[]',
      'x'.repeat(MAX_GUEST_BYTES + 1),
      JSON.stringify({ schema: 1 }),
    ])
      expect(() => parseGuestRecord(raw)).toThrow();
  });
  it('fails explicitly when IndexedDB is unavailable without changing preferences', async () => {
    localStorage.setItem('hnefatafl-language', 'de');
    await expect(readGuestSlot()).rejects.toThrow();
    await expect(writeGuestSlot(null, record as never)).rejects.toThrow();
    expect(localStorage.getItem('hnefatafl-language')).toBe('de');
    localStorage.clear();
  });
});
