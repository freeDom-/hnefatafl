import type { BoardView } from './Board';
import type { Move, Side } from './types';

export type PublicRuleset = 'brandubh' | 'tablut' | 'copenhagen';
export type PublicStatus = 'waiting' | 'active' | 'completed' | 'cancelled' | 'voided';
export interface PublicParticipant {
  display_name: string;
  username: string | null;
  portrait_id: string;
  start_rating: number | null;
  current_rating: number | null;
}
export interface PublicResult {
  winner: Side | 'draw' | null;
  termination_reason: string | null;
  rated: boolean;
  winner_delta: number | null;
}
export interface PublicGameSummary {
  id: string;
  category: 'online';
  ruleset: PublicRuleset;
  ruleset_version: string;
  status: PublicStatus;
  mode: string;
  leg: number;
  rated: boolean;
  created_at: string;
  participants: Record<Side, PublicParticipant | null>;
  result: PublicResult | null;
}
export interface PublicPosition {
  ruleset: string;
  board: string[];
  winner: string | null;
  reason: string | null;
  ply: number;
  turn: Side;
}
export interface PublicGame extends PublicGameSummary {
  position: PublicPosition;
  deadline: string | null;
  time_per_move: number;
  time_control: string;
  increment_seconds: number;
  terminal_king_square: number | null;
  moves: Move[] | null;
}
export interface PublicReplay {
  game: PublicGame;
  validated: boolean;
  states: PublicPosition[];
  moves: Move[];
  // The public server may return stored terminal analysis; this UI deliberately
  // neither models nor renders it. Spectating never initializes an advisor.
}
export interface PublicGamePage {
  items: PublicGameSummary[];
  next_cursor: string | null;
  limit: number;
}
export interface PublicFilters {
  ruleset: string;
  status: string;
  outcome: string;
  player: string;
}
/** Explicit read-only presentation adapter, never an authenticated Game cast. */
export function publicBoardView(
  game: PublicGame,
  replay: PublicReplay | null,
  cursor: number,
): BoardView {
  const index = replay ? Math.max(0, Math.min(cursor, replay.states.length - 1)) : 0;
  const position = replay?.states[index] ?? game.position;
  return {
    ruleset: game.ruleset,
    size: Math.round(Math.sqrt(position.board.length)),
    board: position.board,
    winner: position.winner,
    reason: position.reason,
    moves: replay?.moves ?? [],
    cursor: index,
    total_moves: replay?.moves.length ?? 0,
    capturedKingSquare:
      !replay || index === replay.states.length - 1 ? game.terminal_king_square : null,
  };
}
