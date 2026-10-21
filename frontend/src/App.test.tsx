import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { Game } from './types';

const initial: Game = {
  id: 'g1',
  ruleset: 'tablut',
  size: 3,
  board: ['A', '.', '.', '.', 'K', '.', '.', '.', 'D'],
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
  moves: [],
  cursor: 0,
  total_moves: 0,
  legal_moves: [{ source: 0, target: 1 }],
  mode: 'human-human',
  players: {
    attackers: { name: 'Astrid', kind: 'human', config: {} },
    defenders: { name: 'Erik', kind: 'human', config: {} },
  },
  version: 0,
  captured: { attackers: 0, defenders: 0 },
  created_at: '',
};
const rules = [
  {
    id: 'tablut',
    name: 'Tablut',
    size: 9,
    description: 'Help the king escape.',
    rules_markdown: '# Tablut\nMove in straight lines.',
  },
];
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('hnefatafl-game', 'g1');
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function setup(moveResponse?: Promise<Response>) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path === '/api/rulesets') return json(rules);
    if (path === '/api/ai/profiles' || path === '/api/players' || path === '/api/models')
      return json([]);
    if (path === '/api/games/g1' && options?.method === 'GET') return json(initial);
    if (path === '/api/games/g1/moves')
      return (
        moveResponse ??
        json({
          ...initial,
          version: 1,
          turn: 'defenders',
          board: ['.', 'A', '.', '.', 'K', '.', '.', '.', 'D'],
          legal_moves: [],
          cursor: 1,
          total_moves: 1,
          moves: [{ source: 0, target: 1, side: 'attackers' }],
        })
      );
    if (path === '/api/games')
      return json({
        ...initial,
        id: 'g2',
        board: ['.', '.', 'A', '.', 'K', '.', '.', '.', 'D'],
        legal_moves: [{ source: 2, target: 1 }],
      });
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
describe('game flow', () => {
  it('sends only highlighted legal moves with the current optimistic version', async () => {
    const fetcher = setup();
    render(<App />);
    const attacker = await screen.findByRole('gridcell', { name: 'a3, attacker' });
    fireEvent.click(attacker);
    fireEvent.click(screen.getByRole('gridcell', { name: 'b2, king' }));
    expect(fetcher.mock.calls.some(([path]) => path.endsWith('/moves'))).toBe(false);
    fireEvent.click(attacker);
    fireEvent.click(screen.getByRole('gridcell', { name: 'b3, empty, legal move' }));
    await screen.findByRole('gridcell', { name: 'b3, attacker' });
    const call = fetcher.mock.calls.find(([path]) => path.endsWith('/moves'))!;
    expect(JSON.parse(String(call[1]?.body))).toEqual({ source: 0, target: 1, version: 0 });
    expect(screen.getByText('Defenders to move')).toBeInTheDocument();
  });
  it('does not overwrite a new game when an old move request finishes late', async () => {
    let finish!: (response: Response) => void;
    setup(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<App />);
    fireEvent.click(await screen.findByRole('gridcell', { name: 'a3, attacker' }));
    fireEvent.click(screen.getByRole('gridcell', { name: 'b3, empty, legal move' }));
    fireEvent.click(screen.getByRole('button', { name: /New game/ }));
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await screen.findByRole('gridcell', { name: 'c3, attacker' });
    await act(async () =>
      finish({ ok: true, json: async () => ({ ...initial, version: 1 }) } as Response),
    );
    await waitFor(() => expect(localStorage.getItem('hnefatafl-game')).toBe('g2'));
    expect(screen.getByRole('gridcell', { name: 'c3, attacker' })).toBeInTheDocument();
    expect(screen.getByRole('gridcell', { name: 'a3, empty' })).toBeInTheDocument();
  });
});
