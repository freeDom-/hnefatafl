import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App, { Modal } from './App';
import { navigate } from './navigation';
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
    if (/^\/api\/games\/g[12]\/analytics$/.test(path))
      return json({ advisor: null, time_limit: 5, show_win_chances: true, revision: 0 });
    if (/^\/api\/games\/g[12]\/analysis$/.test(path))
      return (
        analysisResponse ??
        json({ cursor: game.cursor, evaluations: [], hint: null, status: 'complete' })
      );
    if (/^\/api\/games\/g[12]\/ai-move$/.test(path))
      return moveResponse ?? json({ ...game, id: path.split('/')[3], version: 1, winner: 'draw' });
    if (path === '/api/games/g1/replay')
      return json({
        validated: true,
        states: [initial, ...game.moves.map((_, i) => ({ ...game, cursor: i + 1, ply: i + 1 }))],
      });
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
  it.each([false, true])(
    'plays AI turns automatically without controls (mobile: %s)',
    async (mobile) => {
      vi.stubGlobal('matchMedia', () => ({
        matches: mobile,
        addEventListener() {},
        removeEventListener() {},
      }));
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
      expect(screen.queryByRole('button', { name: /Pause AI|Resume AI/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Next move' })).not.toBeInTheDocument();
      expect(screen.queryByRole('combobox', { name: 'AI playback speed' })).not.toBeInTheDocument();
      await waitFor(
        () => expect(fetcher.mock.calls.some(([path]) => path.endsWith('/ai-move'))).toBe(true),
        { timeout: 5000 },
      );
    },
  );
  it.each([false, true])(
    'keeps historical positions read-only and stops AI turns (mobile: %s)',
    async (mobile) => {
      vi.useFakeTimers();
      try {
        vi.stubGlobal('matchMedia', () => ({
          matches: mobile,
          addEventListener() {},
          removeEventListener() {},
        }));
        const saved: Game = {
          ...initial,
          mode: 'human-ai',
          cursor: 2,
          total_moves: 2,
          moves: [
            { source: 0, target: 1, side: 'attackers' },
            { source: 8, target: 7, side: 'defenders' },
          ],
          players: {
            ...initial.players,
            attackers: { name: 'Shield bearer', kind: 'ai', config: { algorithm: 'baseline' } },
          },
        };
        const fetcher = setup(undefined, saved);
        await act(async () => {
          render(<App />);
        });
        expect(
          screen.queryByRole('button', { name: /Undo move|Redo move|Restart game|New game/ }),
        ).not.toBeInTheDocument();
        await act(async () => {
          fireEvent.click(screen.getByRole('button', { name: 'Replay to move 1' }));
        });
        expect(
          screen.getAllByRole('gridcell').every((cell) => (cell as HTMLButtonElement).disabled),
        ).toBe(true);
        fireEvent.click(screen.getByRole('gridcell', { name: 'a3, attacker' }));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(3000);
        });
        expect(fetcher.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(
          0,
        );
        expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/replay'))).toHaveLength(1);
        await act(async () => {
          fireEvent.click(screen.getByRole('button', { name: 'Return to live board' }));
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(900);
        });
        const calls = fetcher.mock.calls.filter(([path]) => path.endsWith('/ai-move'));
        expect(calls).toHaveLength(1);
        expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ version: 0 });
        expect(fetcher.mock.calls.some(([path]) => path.endsWith('/seek'))).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    },
  );
  it('does not let another account play the active human seat', async () => {
    const fetcher = setup(undefined, {
      ...initial,
      players: {
        ...initial.players,
        attackers: { ...initial.players.attackers, account_id: 'another-account' },
      },
    });
    render(<App />);
    await screen.findByRole('grid');
    expect(
      screen.getAllByRole('gridcell').every((cell) => (cell as HTMLButtonElement).disabled),
    ).toBe(true);
    fireEvent.click(screen.getByRole('gridcell', { name: 'a3, attacker' }));
    expect(fetcher.mock.calls.some(([path]) => path.endsWith('/moves'))).toBe(false);
  });
  it('automatically retries a failed AI turn without requiring a resume action', async () => {
    const fetcher = setup(undefined, {
      ...initial,
      mode: 'human-ai',
      players: {
        ...initial.players,
        attackers: { name: 'Shield bearer', kind: 'ai', config: { algorithm: 'baseline' } },
      },
    });
    const original = fetcher.getMockImplementation()!;
    let attempts = 0;
    fetcher.mockImplementation((path, options) => {
      if (path.endsWith('/ai-move') && attempts++ === 0)
        return Promise.reject(new Error('offline'));
      return original(path, options);
    });
    render(<App />);
    await waitFor(
      () =>
        expect(fetcher.mock.calls.filter(([path]) => path.endsWith('/ai-move'))).toHaveLength(2),
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
    await act(async () => {
      navigate('/dashboard?setup=single-player');
    });
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
