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
});
