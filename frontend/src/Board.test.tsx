import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Board from './Board';
import type { Game } from './types';

const game: Game = {
  id: 'test',
  ruleset: 'brandubh',
  size: 3,
  board: ['A', '.', '.', '.', 'K', '.', '.', '.', 'D'],
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
  moves: [],
  cursor: 0,
  total_moves: 0,
  legal_moves: [
    { source: 0, target: 1 },
    { source: 0, target: 3 },
  ],
  mode: 'human-human',
  players: {
    attackers: { name: 'A', kind: 'human', config: {} },
    defenders: { name: 'D', kind: 'human', config: {} },
  },
  version: 0,
  captured: { attackers: 0, defenders: 0 },
  created_at: '',
};
afterEach(cleanup);
describe('accessible game board', () => {
  it('describes pieces and highlights only legal destinations for the selection', () => {
    render(<Board game={game} selected={0} onSelect={vi.fn()} />);
    expect(screen.getByRole('gridcell', { name: 'a3, attacker' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('gridcell', { name: 'b3, empty, legal move' })).toHaveClass('legal');
    expect(screen.getByRole('gridcell', { name: 'b2, king' })).not.toHaveClass('legal');
    expect(screen.getAllByRole('gridcell')).toHaveLength(9);
  });
  it('supports arrow-key navigation and escape to clear a selection', () => {
    const select = vi.fn();
    render(<Board game={game} selected={0} onSelect={select} />);
    const first = screen.getByRole('gridcell', { name: 'a3, attacker' });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(screen.getByRole('gridcell', { name: 'a2, empty, legal move' })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(select).toHaveBeenCalledWith(-1);
  });
  it('blocks interaction while a computer is calculating', () => {
    const select = vi.fn();
    render(<Board game={game} selected={null} onSelect={select} disabled />);
    fireEvent.click(screen.getByRole('gridcell', { name: 'a3, attacker' }));
    expect(select).not.toHaveBeenCalled();
  });
  it('keeps the captured king visible in the final arrangement without changing the stored board', () => {
    const finished: Game = {
      ...game,
      board: ['.', 'A', '.', 'A', '.', 'A', '.', '.', 'D'],
      winner: 'attackers',
      recorded_winner: 'attackers',
      reason: 'king_captured',
      cursor: 1,
      total_moves: 1,
      moves: [
        {
          source: 0,
          target: 1,
          side: 'attackers',
          captured: [{ square: 4, piece: 'K' }],
        },
      ],
      legal_moves: [],
    };
    const view = render(<Board game={finished} selected={null} onSelect={vi.fn()} disabled />);
    expect(screen.getByRole('gridcell', { name: 'b2, king' })).toBeDisabled();
    expect(finished.board[4]).toBe('.');
    expect(screen.getAllByRole('gridcell', { name: /king/ })).toHaveLength(1);

    // Rewinding a completed record displays its actual earlier board, not the final overlay.
    view.rerender(
      <Board
        game={{ ...finished, board: [...game.board], cursor: 0, winner: null, reason: null }}
        selected={null}
        onSelect={vi.fn()}
        disabled
      />,
    );
    expect(screen.getAllByRole('gridcell', { name: /king/ })).toHaveLength(1);
    expect(screen.getByRole('gridcell', { name: 'b2, king' })).toBeDisabled();
  });
  it('uses a server-derived captured king square for online terminal boards', () => {
    const finished = {
      ...game,
      board: ['.', 'A', '.', 'A', '.', 'A', '.', '.', 'D'],
      winner: 'attackers' as const,
      reason: 'king_captured',
      cursor: 1,
      total_moves: 1,
      moves: [{ source: 0, target: 1, side: 'attackers' as const }],
      legal_moves: [],
      capturedKingSquare: 4,
    };
    render(<Board game={finished} selected={null} onSelect={vi.fn()} disabled />);
    expect(screen.getByRole('gridcell', { name: 'b2, king' })).toBeDisabled();
    expect(screen.getAllByRole('gridcell', { name: /king/ })).toHaveLength(1);
  });
  it('draws a trusted advisor hint on a read-only review without a client legal-move list', () => {
    render(
      <Board
        game={{ ...game, legal_moves: [] }}
        selected={null}
        onSelect={vi.fn()}
        readOnly
        hint={{ source: 0, target: 1 }}
      />,
    );
    expect(document.querySelector('.hint-overlay')).not.toBeNull();
    expect(screen.getByRole('gridcell', { name: /suggested source/ })).toBeVisible();
    expect(screen.getByRole('gridcell', { name: /suggested target/ })).toBeVisible();
  });
  it('does not restore ordinary captures or a king from a different replay turn', () => {
    render(
      <Board
        game={{
          ...game,
          board: ['.', 'A', '.', '.', '.', '.', '.', '.', 'D'],
          recorded_winner: 'attackers',
          winner: null,
          cursor: 1,
          total_moves: 2,
          moves: [
            { source: 0, target: 1, side: 'attackers', captured: [{ square: 4, piece: 'D' }] },
            { source: 2, target: 5, side: 'attackers', captured: [{ square: 4, piece: 'K' }] },
          ],
        }}
        selected={null}
        onSelect={vi.fn()}
        disabled
      />,
    );
    expect(screen.getByRole('gridcell', { name: 'b2, empty' })).toBeInTheDocument();
    expect(screen.queryByRole('gridcell', { name: /king/ })).not.toBeInTheDocument();
  });
});
