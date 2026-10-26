import type { Move, Side } from './types';
export type GuestRecord = {
  schema: 1;
  engine_version: string;
  ruleset: string;
  ruleset_version: string;
  human_side: Side;
  moves: Move[];
  cursor: number;
};
export type GuestInfo = {
  engine_version: string;
  rulesets: { id: string; name: string; version: string }[];
};
export type GuestSnapshot = {
  record: GuestRecord;
  position: {
    ruleset: string;
    board: string[];
    turn: Side;
    ply: number;
    winner: Side | 'draw' | null;
    reason: string | null;
  };
  size: number;
  legal_moves: Move[];
  live_winner: Side | 'draw' | null;
  live_reason: string | null;
};
export const MAX_GUEST_BYTES = 262144;
export const MAX_GUEST_MOVES = 4096;
/** Structural bounds only. Authority always comes from Python engine replay. */
export function parseGuestRecord(raw: unknown): GuestRecord {
  if (
    typeof raw !== 'string' ||
    raw.length > MAX_GUEST_BYTES ||
    new TextEncoder().encode(raw).length > MAX_GUEST_BYTES
  )
    throw new Error('Invalid or oversized guest save.');
  const value = JSON.parse(raw);
  const keys = [
    'schema',
    'engine_version',
    'ruleset',
    'ruleset_version',
    'human_side',
    'moves',
    'cursor',
  ];
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).length !== keys.length ||
    keys.some((k) => !Object.hasOwn(value, k))
  )
    throw new Error('Invalid guest save format.');
  if (value.schema !== 1) throw new Error('Unsupported guest save version.');
  if (
    typeof value.engine_version !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.engine_version) ||
    !['brandubh', 'tablut', 'copenhagen'].includes(value.ruleset) ||
    typeof value.ruleset_version !== 'string' ||
    !/^\d{1,8}$/.test(value.ruleset_version) ||
    !['attackers', 'defenders'].includes(value.human_side)
  )
    throw new Error('Unsupported guest save metadata.');
  const size = value.ruleset === 'brandubh' ? 49 : value.ruleset === 'tablut' ? 81 : 121;
  if (
    !Array.isArray(value.moves) ||
    value.moves.length > MAX_GUEST_MOVES ||
    !Number.isSafeInteger(value.cursor) ||
    value.cursor < 0 ||
    value.cursor > value.moves.length
  )
    throw new Error('Invalid guest move history.');
  for (const m of value.moves)
    if (
      !m ||
      typeof m !== 'object' ||
      Object.keys(m).length !== 2 ||
      !Object.hasOwn(m, 'source') ||
      !Object.hasOwn(m, 'target') ||
      !Number.isSafeInteger(m.source) ||
      !Number.isSafeInteger(m.target) ||
      m.source < 0 ||
      m.target < 0 ||
      m.source >= size ||
      m.target >= size
    )
      throw new Error('Invalid guest move coordinates.');
  return value as GuestRecord;
}
