import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App, { Modal } from './App';
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
  history.replaceState(null, '', '/games/g1');
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
function setup(
  moveResponse?: Promise<Response>,
  game = initial,
  analysisResponse?: Promise<Response>,
) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path === '/api/rulesets') return json(rules);
    if (path === '/api/ai/profiles')
      return json([
        {
          id: 'baseline',
          name: 'Shield bearer',
          algorithm: 'baseline',
          parameters: { time_limit: 1 },
          supported_rulesets: ['tablut'],
        },
      ]);
    if (path === '/api/models') return json([]);
    if (path === '/api/games/g1' && options?.method === 'GET') return json(game);
    if (path === '/api/games/g1/analytics')
      return json({ advisor: null, time_limit: 5, show_win_chances: true, revision: 0 });
    if (path === '/api/games/g1/analysis')
      return (
        analysisResponse ??
        json({ cursor: game.cursor, evaluations: [], hint: null, status: 'complete' })
      );
    if (path === '/api/games/g1/ai-move')
      return moveResponse ?? json({ ...game, version: 1, winner: 'draw' });
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
  it.each([false, true])(
    'hides AI playback controls for a completed record (replay: %s)',
    async (replay) => {
      setup(undefined, {
        ...initial,
        mode: 'ai-ai',
        winner: replay ? null : 'attackers',
        recorded_winner: 'attackers',
        players: {
          attackers: { name: 'Hugin', kind: 'ai', config: { algorithm: 'baseline' } },
          defenders: { name: 'Munin', kind: 'ai', config: { algorithm: 'baseline' } },
        },
      });
      render(<App />);
      await screen.findByRole('grid');
      expect(screen.queryByRole('button', { name: /Resume AI|Pause AI/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Next move' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Restart game' })).not.toBeInTheDocument();
      expect(
        screen.getAllByRole('gridcell').every((cell) => (cell as HTMLButtonElement).disabled),
      ).toBe(true);
    },
  );
  it('resumes saved unfinished computer play at its dedicated route', async () => {
    const fetcher = setup(undefined, {
      ...initial,
      mode: 'human-ai',
      players: {
        ...initial.players,
        attackers: { name: 'Shield bearer', kind: 'ai', config: { algorithm: 'baseline' } },
      },
    });
    render(<App />);
    await screen.findByRole('grid');
    await waitFor(
      () => expect(fetcher.mock.calls.some(([path]) => path.endsWith('/ai-move'))).toBe(true),
      { timeout: 5000 },
    );
  });
  it('allows human moves while background analysis is still pending', async () => {
    const fetcher = setup(
      undefined,
      {
        ...initial,
        mode: 'human-ai',
        analysis_enabled: true,
        players: {
          ...initial.players,
          defenders: { name: 'Seer', kind: 'ai', config: { algorithm: 'mcts' } },
        },
      },
      new Promise(() => {}),
    );
    render(<App />);
    fireEvent.click(await screen.findByRole('gridcell', { name: 'a3, attacker' }));
    fireEvent.click(screen.getByRole('gridcell', { name: 'b3, empty, legal move' }));
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([path]) => path.endsWith('/moves'))).toBe(true),
    );
  });
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
    expect(screen.getByLabelText('Defenders to move')).toBeInTheDocument();
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

it('dismisses a dialog on an outside press, and ignores a release or click after an inside press', () => {
  const close = vi.fn();
  render(
    <Modal title="New saga" onClose={close}>
      <input aria-label="Inside" />
    </Modal>,
  );
  const dialog = screen.getByRole('dialog');
  vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue({
    left: 10,
    right: 100,
    top: 10,
    bottom: 100,
  } as DOMRect);
  fireEvent.pointerDown(screen.getByLabelText('Inside'), { clientX: 50, clientY: 50 });
  fireEvent.pointerUp(dialog, { clientX: 1, clientY: 1 });
  fireEvent.click(dialog, { clientX: 1, clientY: 1 });
  expect(close).not.toHaveBeenCalled();
  fireEvent.pointerDown(dialog, { clientX: 50, clientY: 50 });
  expect(close).not.toHaveBeenCalled();
  fireEvent.pointerDown(dialog, { clientX: 1, clientY: 1 });
  expect(close).toHaveBeenCalledOnce();
});
